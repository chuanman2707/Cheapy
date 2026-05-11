"""MCP client installer foundation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import shutil
import subprocess
from typing import Any

from cheapy import __version__


SERVER_NAME = "cheapy"
VERSION_CHECK_TIMEOUT_SECONDS = 5.0
OFFICIAL_CLI_TIMEOUT_SECONDS = 30.0


class InstallerClient(StrEnum):
    """Supported MCP clients for installer setup."""

    CODEX = "codex"
    CLAUDE = "claude"


@dataclass(frozen=True)
class InstallerError(Exception):
    """Structured installer failure suitable for CLI JSON output."""

    code: str
    message: str
    suggestion: str
    exit_code: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", (self.message,))

    def payload(self) -> dict[str, str | bool]:
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
        }


def build_mcp_entry(executable: Path) -> dict[str, str | list[str]]:
    """Build the stdio MCP entry shared by supported clients."""
    return {"command": str(executable), "args": ["mcp"]}


def manual_install_command(client: InstallerClient, executable: Path) -> str:
    """Return the manual official-client command for installing Cheapy MCP."""
    command = _official_cli_command(InstallerClient(client), executable)
    return " ".join(command)


def resolve_cheapy_executable() -> Path:
    """Find and verify the Cheapy CLI executable on PATH."""
    executable = shutil.which(SERVER_NAME)
    if executable is None:
        raise InstallerError(
            code="MISSING_EXECUTABLE",
            message="cheapy executable was not found on PATH.",
            suggestion="Install Cheapy or add the cheapy executable to PATH.",
        )

    executable_path = Path(executable).expanduser().resolve()
    try:
        result = subprocess.run(
            [str(executable_path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=VERSION_CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallerError(
            code="EXECUTABLE_MISMATCH",
            message=(
                "Could not verify cheapy executable against running package "
                f"version {__version__}: {exc}"
            ),
            suggestion="Reinstall Cheapy or update PATH to point at this package's executable.",
        ) from exc

    executable_version = result.stdout.strip()
    if result.returncode != 0 or executable_version != __version__:
        raise InstallerError(
            code="EXECUTABLE_MISMATCH",
            message=(
                "cheapy executable version does not match running package "
                f"version {__version__}; executable reported "
                f"{executable_version or 'no version'}."
            ),
            suggestion="Reinstall Cheapy or update PATH to point at this package's executable.",
        )

    return executable_path


def is_recoverable_official_cli_failure(
    failure: str | subprocess.CompletedProcess[str],
) -> bool:
    """Return whether an official CLI failure should try direct config fallback."""
    output = _failure_output(failure).lower()
    if "permission denied" in output or "traceback" in output:
        return False

    missing_mcp_command = "mcp" in output and any(
        phrase in output
        for phrase in (
            "no such command",
            "unknown command",
            "unknown subcommand",
            "unrecognized command",
            "invalid choice",
        )
    )
    server_already_exists = "already exists" in output and any(
        token in output for token in ("server", "mcp", SERVER_NAME)
    )
    return missing_mcp_command or server_already_exists


def install_mcp(
    client: InstallerClient,
    *,
    project_root: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Install the Cheapy MCP server for a supported client."""
    selected_client = InstallerClient(client)
    executable = resolve_cheapy_executable()
    install_root = Path.cwd() if project_root is None else project_root
    install_home = Path.home() if home is None else home

    if shutil.which(selected_client.value) is None:
        return _install_via_direct_config(
            selected_client,
            executable,
            home=install_home,
        )

    command = _official_cli_command(selected_client, executable)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=OFFICIAL_CLI_TIMEOUT_SECONDS,
            cwd=install_root,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _client_config_unavailable_error(
            selected_client,
            executable,
            str(exc),
        ) from exc

    if result.returncode == 0:
        return {
            "status": "ok",
            "client": selected_client.value,
            "method": "official_cli",
            "rollback_path": None,
            "mcp_entry": build_mcp_entry(executable),
            "hooks": _placeholder_hook_results(),
        }

    if is_recoverable_official_cli_failure(result):
        return _install_via_direct_config(
            selected_client,
            executable,
            home=install_home,
        )

    raise _client_config_unavailable_error(
        selected_client,
        executable,
        _failure_output(result).strip() or "official CLI failed",
    )


def _official_cli_command(client: InstallerClient, executable: Path) -> list[str]:
    if client is InstallerClient.CODEX:
        return [
            "codex",
            "mcp",
            "add",
            SERVER_NAME,
            "--",
            str(executable),
            "mcp",
        ]

    if client is InstallerClient.CLAUDE:
        return [
            "claude",
            "mcp",
            "add",
            "--transport",
            "stdio",
            SERVER_NAME,
            "--",
            str(executable),
            "mcp",
        ]

    raise ValueError(f"Unsupported installer client: {client}")


def _install_via_direct_config(
    client: InstallerClient,
    executable: Path,
    *,
    home: Path,
) -> dict[str, Any]:
    _ = home
    raise _client_config_unavailable_error(
        client,
        executable,
        "Direct MCP config editing is not implemented yet.",
    )


def _client_config_unavailable_error(
    client: InstallerClient,
    executable: Path,
    detail: str,
) -> InstallerError:
    return InstallerError(
        code="CLIENT_CONFIG_UNAVAILABLE",
        message=f"Could not configure {client.value} MCP client. {detail}",
        suggestion=(
            "Run the official client command manually: "
            f"{manual_install_command(client, executable)}"
        ),
    )


def _placeholder_hook_results() -> dict[str, dict[str, str]]:
    return {
        InstallerClient.CODEX.value: {"status": "not_applicable"},
        InstallerClient.CLAUDE.value: {"status": "not_applicable"},
    }


def _failure_output(failure: str | subprocess.CompletedProcess[str]) -> str:
    if isinstance(failure, subprocess.CompletedProcess):
        return "\n".join(part for part in (failure.stdout, failure.stderr) if part)
    return failure
