"""Cheapy command line interface."""

from __future__ import annotations

import json
import shutil
import sys
from typing import Any

import click
import typer
from typer.core import TyperGroup

from cheapy import __version__
from cheapy.models import SearchRequestV1, SearchResponseV1


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


@app.command()
def mcp() -> None:
    """Run the stdio MCP server."""
    _json_echo(
        _error_payload(
            "MCP_OUTSIDE_CONTRACT_GATE",
            "MCP server is outside this contract foundation gate.",
            "Use contract commands such as 'cheapy schema' in this gate.",
        ),
        err=True,
    )
    raise typer.Exit(code=2)
