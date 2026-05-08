"""Cheapy command line interface."""

from __future__ import annotations

import json
import shutil
import sys

import typer

from cheapy import __version__
from cheapy.models import SearchRequestV1, SearchResponseV1

app = typer.Typer(
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
def doctor() -> None:
    """Check local Cheapy installation health."""
    executable = shutil.which("cheapy")
    if executable is None:
        typer.echo("ERROR: cheapy executable was not found on PATH.", err=True)
        raise typer.Exit(code=1)

    typer.echo("Cheapy doctor")
    typer.echo(f"version: {__version__}")
    typer.echo(f"executable: {executable}")
    typer.echo("status: ok")


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
    print("ERROR: MCP server is outside this contract foundation gate.", file=sys.stderr)
    raise typer.Exit(code=2)
