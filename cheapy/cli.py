"""Cheapy command line interface."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

import click
import typer
from typer.core import TyperGroup

from cheapy import __version__
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
mcp_app = typer.Typer(
    help="Run or install the Cheapy MCP server.",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(providers_app, name="providers")
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

    failed_reports = [
        report
        for report in reports
        if report["status"] == ProviderStatusCode.FAILED.value
    ]
    if failed_reports and not live:
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
