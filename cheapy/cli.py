"""Cheapy command line interface."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any

import click
from pydantic import ValidationError
import typer
from typer.core import TyperGroup

from cheapy import __version__
from cheapy.airports import AirportNotFound, resolve_airport
from cheapy.mcp import run_stdio_server
from cheapy.mcp_installer import InstallerClient, InstallerError, install_mcp
from cheapy.models import ProviderStatusCode, SearchRequestV1, SearchResponseV1
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.registry import (
    ProviderLoadError,
    ProviderManifestError,
    discover_provider_manifests,
    load_enabled_providers,
)
from cheapy.search_service import search_with_storage
from cheapy.storage import sqlite as storage
from cheapy.watchlist import build_watchlist_request, evaluate_watchlist


LIVE_TEST_ENV = "CHEAPY_RUN_LIVE_TESTS"


def _json_echo(payload: dict[str, Any], *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, sort_keys=True), err=err)


def _error_payload(code: str, message: str, suggestion: str) -> dict[str, Any]:
    return {
        "error": True,
        "code": code,
        "message": message,
        "suggestion": suggestion,
    }


class JsonUsageErrorGroup(TyperGroup):
    """Emit Click/Typer usage errors as structured JSON for agents."""

    def main(self, *args: Any, **kwargs: Any) -> Any:
        standalone_mode = kwargs.pop("standalone_mode", True)
        try:
            result = super().main(*args, standalone_mode=False, **kwargs)
            if standalone_mode and isinstance(result, int) and result != 0:
                sys.exit(result)
            return result
        except click.UsageError as exc:
            _json_echo(
                _error_payload(
                    "USAGE_ERROR",
                    exc.format_message(),
                    "Run 'cheapy --help' for valid usage.",
                ),
                err=True,
            )
            if standalone_mode:
                sys.exit(2)
            raise
        except click.exceptions.Exit as exc:
            if standalone_mode:
                sys.exit(exc.exit_code)
            raise


app = typer.Typer(
    cls=JsonUsageErrorGroup,
    help="Cheapy flight-search MCP utilities.",
    no_args_is_help=True,
    invoke_without_command=True,
)
providers_app = typer.Typer(
    help="Inspect packaged Cheapy providers.",
    no_args_is_help=True,
)
history_app = typer.Typer(
    help="Inspect local Cheapy search history.",
    no_args_is_help=False,
    invoke_without_command=True,
)
watchlist_app = typer.Typer(
    help="Manage local Cheapy price watchlists.",
    no_args_is_help=True,
)
mcp_app = typer.Typer(
    help="Run or install the Cheapy MCP server.",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(providers_app, name="providers")
app.add_typer(history_app, name="history")
app.add_typer(watchlist_app, name="watchlist")
app.add_typer(mcp_app, name="mcp")


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print Cheapy version and exit.",
    ),
) -> None:
    """Run Cheapy CLI."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def doctor(
    human: bool = typer.Option(
        False,
        "--human",
        help="Print a concise human-readable health report.",
    ),
) -> None:
    """Check local Cheapy installation health."""
    executable = shutil.which("cheapy")
    if executable is None:
        _json_echo(
            _error_payload(
                "MISSING_EXECUTABLE",
                "cheapy executable was not found on PATH.",
                "Install Cheapy or add the cheapy executable to PATH.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    report = {
        "status": "ok",
        "version": __version__,
        "executable": executable,
    }
    if human:
        typer.echo("Cheapy doctor")
        typer.echo(f"version: {report['version']}")
        typer.echo(f"executable: {report['executable']}")
        typer.echo(f"status: {report['status']}")
        return

    _json_echo(report)


@app.command()
def schema() -> None:
    """Export public contract JSON schemas."""
    schemas = {
        "SearchRequestV1": SearchRequestV1.model_json_schema(),
        "SearchResponseV1": SearchResponseV1.model_json_schema(),
    }
    typer.echo(json.dumps(schemas, indent=2, sort_keys=True))


@mcp_app.callback(invoke_without_command=True)
def mcp(ctx: typer.Context) -> None:
    """Run the stdio MCP server."""
    if ctx.invoked_subcommand is None:
        run_stdio_server()


@mcp_app.command("install")
def mcp_install(
    client: InstallerClient = typer.Option(
        ...,
        "--client",
        help="MCP client to configure.",
    ),
) -> None:
    """Install Cheapy MCP for a supported client."""
    try:
        report = install_mcp(client, project_root=Path.cwd())
    except InstallerError as exc:
        _json_echo(exc.payload(), err=True)
        raise typer.Exit(code=exc.exit_code)

    _json_echo(report)


def _provider_fixture_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
    )


def _storage_disabled_exit() -> None:
    _json_echo(
        _error_payload(
            "STORAGE_DISABLED",
            "Local Cheapy storage is disabled.",
            "Unset CHEAPY_DISABLE_STORAGE or set it to a value other than 1.",
        ),
        err=True,
    )
    raise typer.Exit(code=1)


def _history_storage_error_exit() -> None:
    _json_echo(
        _error_payload(
            "HISTORY_STORAGE_ERROR",
            "Local search history could not be read.",
            "Verify local storage permissions or set CHEAPY_DB_PATH to a readable SQLite database.",
        ),
        err=True,
    )
    raise typer.Exit(code=1)


def _watchlist_storage_error_exit() -> None:
    _json_echo(
        _error_payload(
            "WATCHLIST_STORAGE_ERROR",
            "Local watchlists could not be read or written.",
            (
                "Verify local storage permissions or set CHEAPY_DB_PATH to a "
                "writable SQLite database."
            ),
        ),
        err=True,
    )
    raise typer.Exit(code=1)


def _normalize_iata(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise click.BadParameter("Airport must be a 3-letter IATA code.")
    try:
        resolve_airport(normalized)
    except AirportNotFound as exc:
        raise click.BadParameter(
            "Airport is not in Cheapy's packaged airport catalog."
        ) from exc
    return normalized


def _normalize_currency(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise click.BadParameter("Currency must be a 3-letter alphabetic code.")
    return normalized


@history_app.callback(invoke_without_command=True)
def history(ctx: typer.Context) -> None:
    """Inspect local Cheapy search history."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@history_app.command("list")
def history_list(
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        max=100,
        help="Maximum number of search runs to list.",
    ),
) -> None:
    """List local search history."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()

    try:
        with storage.open_database() as conn:
            runs = storage.list_history(conn, limit=limit)
    except storage.StorageDisabled:
        _storage_disabled_exit()
    except (OSError, sqlite3.Error):
        _history_storage_error_exit()
    _json_echo({"status": "ok", "runs": runs})


@history_app.command("show")
def history_show(run_id: int = typer.Argument(..., help="Search run id.")) -> None:
    """Show one local search run."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()

    try:
        with storage.open_database() as conn:
            payload = storage.show_history(conn, run_id)
    except storage.StorageDisabled:
        _storage_disabled_exit()
    except (OSError, sqlite3.Error):
        _history_storage_error_exit()
    if payload is None:
        _json_echo(
            _error_payload(
                "HISTORY_RUN_NOT_FOUND",
                "Search run was not found.",
                "Run 'cheapy history list' to see available search runs.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    _json_echo({"status": "ok", **payload})


@watchlist_app.command("add")
def watchlist_add(
    name: str = typer.Option(..., "--name", help="Watchlist name."),
    origin: str = typer.Option(..., "--origin", help="Origin IATA code."),
    destination: str = typer.Option(
        ...,
        "--destination",
        help="Destination IATA code.",
    ),
    departure_date: str = typer.Option(
        ...,
        "--departure-date",
        help="Departure date in YYYY-MM-DD format.",
    ),
    return_date: str | None = typer.Option(
        None,
        "--return-date",
        help="Optional return date in YYYY-MM-DD format.",
    ),
    max_price_amount: float | None = typer.Option(
        None,
        "--max-price-amount",
        help="Maximum acceptable price.",
    ),
    currency: str | None = typer.Option(
        None,
        "--currency",
        help="Currency code for price comparison.",
    ),
    max_stops: int | None = typer.Option(
        None,
        "--max-stops",
        min=0,
        help="Maximum allowed stops.",
    ),
    max_results: int = typer.Option(
        5,
        "--max-results",
        min=1,
        max=20,
        help="Maximum search results.",
    ),
) -> None:
    """Add a local watchlist."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()

    normalized_origin = _normalize_iata(origin)
    normalized_destination = _normalize_iata(destination)
    currency_code = _normalize_currency(currency)
    if max_price_amount is not None and max_price_amount <= 0:
        raise click.BadParameter("Maximum price must be positive.")
    try:
        SearchRequestV1.model_validate(
            {
                "schema_version": "1",
                "origin": normalized_origin,
                "destination": normalized_destination,
                "departure_date": departure_date,
                "return_date": return_date,
                "max_results": max_results,
            }
        )
    except ValidationError as exc:
        raise click.BadParameter("Watchlist search parameters are invalid.") from exc

    try:
        with storage.open_database() as conn:
            watchlist = storage.add_watchlist(
                conn,
                name=name,
                origin=normalized_origin,
                destination=normalized_destination,
                departure_date=departure_date,
                return_date=return_date,
                max_price_amount=max_price_amount,
                currency=currency_code,
                max_stops=max_stops,
                max_results=max_results,
            )
    except storage.StorageDisabled:
        _storage_disabled_exit()
    except (OSError, sqlite3.Error):
        _watchlist_storage_error_exit()

    _json_echo({"status": "ok", "watchlist": watchlist})


@watchlist_app.command("list")
def watchlist_list() -> None:
    """List local watchlists."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()

    try:
        with storage.open_database() as conn:
            watchlists = storage.list_watchlists(conn)
    except storage.StorageDisabled:
        _storage_disabled_exit()
    except (OSError, sqlite3.Error):
        _watchlist_storage_error_exit()

    _json_echo({"status": "ok", "watchlists": watchlists})


@watchlist_app.command("check")
def watchlist_check(
    watchlist_id: int = typer.Argument(..., help="Watchlist id."),
) -> None:
    """Run a manual watchlist check."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()

    try:
        with storage.open_database() as conn:
            watchlist = storage.get_watchlist(conn, watchlist_id)
    except storage.StorageDisabled:
        _storage_disabled_exit()
    except (OSError, sqlite3.Error):
        _watchlist_storage_error_exit()

    if watchlist is None:
        _json_echo(
            _error_payload(
                "WATCHLIST_NOT_FOUND",
                "Watchlist was not found.",
                "Run 'cheapy watchlist list' to see available watchlists.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    request = build_watchlist_request(watchlist)
    try:
        with storage.open_database() as conn:
            historical_comparison = storage.watchlist_historical_comparison(
                conn,
                watchlist,
            )
    except storage.StorageDisabled:
        _storage_disabled_exit()
    except (OSError, sqlite3.Error):
        _watchlist_storage_error_exit()

    result = search_with_storage(request)
    if result.search_run_id is None:
        _json_echo(
            _error_payload(
                "WATCHLIST_CHECK_NOT_RECORDED",
                "Watchlist check could not be recorded.",
                "Verify local storage is writable and rerun the check.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    decision_payload = evaluate_watchlist(
        response=result.response,
        watchlist=watchlist,
        historical_comparison=historical_comparison,
    )
    best_offer = decision_payload["best_offer"]
    try:
        with storage.open_database() as conn:
            storage.record_watchlist_check(
                conn,
                watchlist_id=watchlist_id,
                search_run_id=result.search_run_id,
                decision=decision_payload["decision"],
                best_offer_id=(
                    best_offer["offer_id"] if best_offer is not None else None
                ),
                best_price_amount=(
                    best_offer["price_amount"] if best_offer is not None else None
                ),
                currency=(
                    best_offer["currency"]
                    if best_offer is not None
                    else watchlist.get("currency")
                ),
                rationale={"reasons": decision_payload["rationale"]},
            )
    except storage.StorageDisabled:
        _storage_disabled_exit()
    except (OSError, sqlite3.Error):
        _watchlist_storage_error_exit()

    _json_echo(
        {
            "status": "ok",
            "watchlist_id": watchlist_id,
            "search_run_id": result.search_run_id,
            **decision_payload,
        }
    )


@providers_app.command("list")
def providers_list() -> None:
    """List packaged Cheapy providers."""
    try:
        manifests = discover_provider_manifests()
    except ProviderManifestError as exc:
        _json_echo(
            _error_payload(
                "PROVIDER_MANIFEST_INVALID",
                str(exc),
                "Reinstall Cheapy and verify provider package data is valid.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if not manifests:
        _json_echo(
            _error_payload(
                "NO_PROVIDER_AVAILABLE",
                "No packaged Cheapy providers were found.",
                "Reinstall Cheapy and verify package data is present.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    _json_echo(
        {
            "status": "ok",
            "providers": [
                {
                    "name": manifest.name,
                    "display_name": manifest.display_name,
                    "capabilities": manifest.capabilities,
                    "default_enabled": manifest.default_enabled,
                    "enabled": manifest.default_enabled,
                    "provider_kind": manifest.provider_kind,
                }
                for manifest in manifests
            ],
        }
    )


@providers_app.command("test")
def providers_test(
    human: bool = typer.Option(
        False,
        "--human",
        help="Print a concise human-readable provider report.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Run opt-in live provider smoke checks.",
    ),
) -> None:
    """Run packaged provider smoke checks."""
    if live and os.environ.get(LIVE_TEST_ENV) != "1":
        _json_echo(
            _error_payload(
                "LIVE_TESTS_NOT_ENABLED",
                "Live provider tests require CHEAPY_RUN_LIVE_TESTS=1.",
                "Set CHEAPY_RUN_LIVE_TESTS=1 and rerun 'cheapy providers test --live'.",
            ),
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        providers = load_live_test_providers() if live else load_enabled_providers()
    except ProviderManifestError as exc:
        _json_echo(
            _error_payload(
                "PROVIDER_MANIFEST_INVALID",
                str(exc),
                "Reinstall Cheapy and verify provider package data is valid.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    except ProviderLoadError as exc:
        _json_echo(
            _error_payload(
                "PROVIDER_TEST_ERROR",
                str(exc),
                "Run 'cheapy providers test --human' for a concise provider report.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if not providers:
        _json_echo(
            _error_payload(
                "NO_PROVIDER_AVAILABLE",
                "No enabled packaged Cheapy providers were found.",
                "Reinstall Cheapy and verify package data is present.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        reports = asyncio.run(_run_provider_checks(providers, live=live))
    except Exception:
        code = "PROVIDER_LIVE_TEST_ERROR" if live else "PROVIDER_TEST_ERROR"
        message = (
            "A live provider check raised an unexpected exception."
            if live
            else "A provider check raised an unexpected exception."
        )
        _json_echo(
            _error_payload(
                code,
                message,
                "Run 'cheapy providers test --human' for a concise provider report.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    failed_reports = _failed_provider_reports(reports, live=live)
    if failed_reports:
        _json_echo(
            _error_payload(
                "PROVIDER_TEST_FAILED",
                "One or more provider checks failed.",
                "Run 'cheapy providers test --human' for a concise provider report.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if human:
        _echo_provider_human_report(reports, status="ok")
        return

    _json_echo(
        {
            "status": "ok",
            "providers_tested": len(providers),
            "providers": reports,
        }
    )


def _failed_provider_reports(
    reports: list[dict[str, Any]],
    *,
    live: bool,
) -> list[dict[str, Any]]:
    failed_reports = [
        report
        for report in reports
        if report["status"] == ProviderStatusCode.FAILED.value
    ]
    if not live:
        return failed_reports
    return [
        report
        for report in failed_reports
        if report.get("provider_kind") != "live"
    ]


def _echo_provider_human_report(reports: list[dict[str, Any]], *, status: str) -> None:
    typer.echo("Cheapy providers test")
    for report in reports:
        typer.echo(
            f"{report['name']} {report['provider_kind']} {report['capability']}: "
            f"{report['status']} (offers: {report['offer_count']}, "
            f"errors: {report['error_count']}, live: {report['live_smoke']})"
        )
    typer.echo(f"status: {status}")


async def _run_provider_checks(
    providers: list[Any],
    *,
    live: bool,
) -> list[dict[str, Any]]:
    request = _provider_fixture_request()
    reports: list[dict[str, Any]] = []
    for provider in providers:
        provider_kind = _provider_kind(provider.name)
        if provider_kind == "live" and not live:
            reports.append(
                {
                    "name": provider.name,
                    "provider_kind": provider_kind,
                    "capability": "exact_one_way",
                    "status": ProviderStatusCode.SKIPPED.value,
                    "offer_count": 0,
                    "error_count": 0,
                    "live_smoke": "not_run",
                }
            )
            continue

        check_request = _live_smoke_request() if provider_kind == "live" else request
        result = await provider.search_exact_one_way(check_request)
        reports.append(
            {
                "name": result.provider_name,
                "provider_kind": provider_kind,
                "capability": result.capability,
                "status": result.status.value,
                "offer_count": len(result.offers),
                "error_count": len(result.errors),
                "live_smoke": "run" if provider_kind == "live" else "not_applicable",
            }
        )
    return reports


def _provider_kind(provider_name: str) -> str:
    for manifest in discover_provider_manifests():
        if manifest.name == provider_name:
            return manifest.provider_kind
    return "fixture"


def _live_smoke_request() -> ProviderExactOneWayRequest:
    from datetime import date, timedelta

    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date=(date.today() + timedelta(days=30)).isoformat(),
    )


def load_live_test_providers() -> list[Any]:
    return load_enabled_providers()
