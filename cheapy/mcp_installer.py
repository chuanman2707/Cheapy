"""MCP client installer foundation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
import shutil
import shlex
import subprocess
from typing import Any

from cheapy import __version__


SERVER_NAME = "cheapy"
VERSION_CHECK_TIMEOUT_SECONDS = 5.0
OFFICIAL_CLI_TIMEOUT_SECONDS = 30.0
MAX_FAILURE_DETAIL_CHARS = 240
REDACTED = "[REDACTED]"

SECRET_PATTERNS = (
    re.compile(r"(?i)(\bauthorization\s*:\s*)(?:bearer\s+)?[^\s]+"),
    re.compile(r"(?i)\bbearer\s+[^\s]+"),
    re.compile(
        r"(?i)\b([a-z0-9_.-]*(?:token|secret|api[_-]?key|password)"
        r"[a-z0-9_.-]*)\s*[:=]\s*[^\s]+"
    ),
)


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
    return shlex.join(command)


def resolve_cheapy_executable() -> Path:
    """Find and verify the Cheapy CLI executable on PATH."""
    executable = shutil.which(SERVER_NAME)
    if executable is None:
        raise InstallerError(
            code="MISSING_EXECUTABLE",
            message="cheapy executable was not found on PATH.",
            suggestion=(
                "Install the cheapy-flights package first, then ensure the cheapy "
                "executable is on PATH."
            ),
        )

    executable_path = Path(executable).expanduser().resolve()
    try:
        result = subprocess.run(
            [str(executable_path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
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
            "unrecognized subcommand",
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

    client_executable = shutil.which(selected_client.value)
    if client_executable is None:
        return _install_via_direct_config(
            selected_client,
            executable,
            project_root=install_root,
            home=install_home,
        )

    command = _official_cli_command(
        selected_client,
        executable,
        client_executable=client_executable,
    )
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
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
            "server_name": SERVER_NAME,
            "method": "official_cli",
            "executable": str(executable),
            "config_path": None,
            "rollback_path": None,
            "mcp_entry": build_mcp_entry(executable),
            "codex_skill": _placeholder_hook_status(),
            "agents_hook": _placeholder_hook_status(),
            "claude_instructions": _placeholder_hook_status(),
            "claude_hook": _placeholder_hook_status(),
            "manual_steps": [],
        }

    if is_recoverable_official_cli_failure(result):
        return _install_via_direct_config(
            selected_client,
            executable,
            project_root=install_root,
            home=install_home,
        )

    raise _client_config_unavailable_error(
        selected_client,
        executable,
        _failure_output(result).strip() or "official CLI failed",
    )


def _official_cli_command(
    client: InstallerClient,
    executable: Path,
    *,
    client_executable: str | None = None,
) -> list[str]:
    client_command = client.value if client_executable is None else client_executable
    if client is InstallerClient.CODEX:
        return [
            client_command,
            "mcp",
            "add",
            SERVER_NAME,
            "--",
            str(executable),
            "mcp",
        ]

    if client is InstallerClient.CLAUDE:
        return [
            client_command,
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
    project_root: Path,
    home: Path,
) -> dict[str, Any]:
    from cheapy.client_configs import edit_client_config

    mcp_entry = build_mcp_entry(executable)
    try:
        edit_result = edit_client_config(
            client,
            mcp_entry,
            project_root=project_root,
            home=home,
        )
    except InstallerError as exc:
        if exc.code == "CLIENT_CONFIG_UNAVAILABLE":
            raise _client_config_unavailable_error(
                client,
                executable,
                exc.message,
            ) from exc
        raise

    return {
        "status": "ok",
        "client": client.value,
        "server_name": SERVER_NAME,
        "method": "direct_edit",
        "executable": str(executable),
        "config_path": str(edit_result.config_path),
        "rollback_path": str(edit_result.rollback_path),
        "mcp_entry": mcp_entry,
        "codex_skill": _placeholder_hook_status(),
        "agents_hook": _placeholder_hook_status(),
        "claude_instructions": _placeholder_hook_status(),
        "claude_hook": _placeholder_hook_status(),
        "manual_steps": [],
    }


def _client_config_unavailable_error(
    client: InstallerClient,
    executable: Path,
    detail: str,
) -> InstallerError:
    safe_detail = _sanitize_failure_detail(detail)
    return InstallerError(
        code="CLIENT_CONFIG_UNAVAILABLE",
        message=f"Could not configure {client.value} MCP client. {safe_detail}",
        suggestion=(
            "Run the official client command manually: "
            f"{manual_install_command(client, executable)}"
        ),
    )


def _placeholder_hook_status() -> dict[str, str]:
    return {"status": "not_applicable"}


def _failure_output(failure: str | subprocess.CompletedProcess[str]) -> str:
    if isinstance(failure, subprocess.CompletedProcess):
        return "\n".join(part for part in (failure.stdout, failure.stderr) if part)
    return failure


def _sanitize_failure_detail(detail: str) -> str:
    redacted = detail.strip() or "official CLI failed"
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1" + REDACTED, redacted)
        else:
            redacted = pattern.sub(REDACTED, redacted)

    redacted = " ".join(redacted.split())
    if len(redacted) <= MAX_FAILURE_DETAIL_CHARS:
        return redacted
    return redacted[: MAX_FAILURE_DETAIL_CHARS - 3].rstrip() + "..."
