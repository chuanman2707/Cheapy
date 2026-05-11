"""MCP installer foundation tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

import pytest

from cheapy.mcp_installer import (
    InstallerClient,
    InstallerError,
    build_mcp_entry,
    install_mcp,
    is_recoverable_official_cli_failure,
    manual_install_command,
    resolve_cheapy_executable,
)


@dataclass(frozen=True)
class RunCall:
    command: list[str]
    cwd: Path | None
    timeout: float | None


def test_build_mcp_entry_uses_cheapy_stdio_command() -> None:
    assert build_mcp_entry(Path("/opt/bin/cheapy")) == {
        "command": "/opt/bin/cheapy",
        "args": ["mcp"],
    }


def test_manual_codex_install_command_uses_official_cli_shape() -> None:
    assert (
        manual_install_command(InstallerClient.CODEX, Path("/opt/bin/cheapy"))
        == "codex mcp add cheapy -- /opt/bin/cheapy mcp"
    )


def test_manual_claude_install_command_uses_official_cli_shape() -> None:
    assert (
        manual_install_command(InstallerClient.CLAUDE, Path("/opt/bin/cheapy"))
        == "claude mcp add --transport stdio cheapy -- /opt/bin/cheapy mcp"
    )


def test_resolve_cheapy_executable_verifies_version_and_returns_canonical_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("/opt/bin/cheapy")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: str(executable))

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command == [str(executable), "--version"]
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["text"] is True
        assert kwargs["timeout"] > 0
        return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    assert resolve_cheapy_executable() == executable.resolve()


def test_resolve_cheapy_executable_reports_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: None)

    with pytest.raises(InstallerError) as exc_info:
        resolve_cheapy_executable()

    error = exc_info.value
    assert error.code == "MISSING_EXECUTABLE"
    assert error.exit_code == 1
    assert "Install Cheapy" in error.suggestion
    assert error.payload() == {
        "error": True,
        "code": "MISSING_EXECUTABLE",
        "message": error.message,
        "suggestion": error.suggestion,
    }


def test_resolve_cheapy_executable_reports_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("/opt/bin/cheapy")
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: str(executable))

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="9.9.9\n", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    with pytest.raises(InstallerError) as exc_info:
        resolve_cheapy_executable()

    assert exc_info.value.code == "EXECUTABLE_MISMATCH"
    assert "running package version 0.1.0" in exc_info.value.message


@pytest.mark.parametrize(
    "output",
    [
        "Error: No such command 'mcp'.",
        "unknown command mcp for codex",
        "MCP server 'cheapy' already exists",
    ],
)
def test_official_cli_recoverable_failures(output: str) -> None:
    assert is_recoverable_official_cli_failure(output) is True


@pytest.mark.parametrize(
    "output",
    [
        "Permission denied while writing config",
        "Traceback (most recent call last): RuntimeError: boom",
    ],
)
def test_official_cli_nonrecoverable_failures(output: str) -> None:
    assert is_recoverable_official_cli_failure(output) is False


def test_install_codex_uses_official_cli_and_returns_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")
    calls: list[RunCall] = []

    def fake_which(name: str) -> str | None:
        return {"cheapy": str(executable), "codex": "/usr/local/bin/codex"}.get(name)

    def fake_run(
        command: list[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        timeout: float,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(RunCall(command=command, cwd=cwd, timeout=timeout))
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    report = install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert calls[1].command == [
        "codex",
        "mcp",
        "add",
        "cheapy",
        "--",
        str(executable.resolve()),
        "mcp",
    ]
    assert calls[1].cwd == tmp_path
    assert report == {
        "status": "ok",
        "client": "codex",
        "method": "official_cli",
        "rollback_path": None,
        "mcp_entry": {"command": str(executable.resolve()), "args": ["mcp"]},
        "hooks": {
            "codex": {"status": "not_applicable"},
            "claude": {"status": "not_applicable"},
        },
    }


def test_install_claude_uses_official_cli_and_returns_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")
    calls: list[RunCall] = []

    def fake_which(name: str) -> str | None:
        return {"cheapy": str(executable), "claude": "/usr/local/bin/claude"}.get(name)

    def fake_run(
        command: list[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        timeout: float,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(RunCall(command=command, cwd=cwd, timeout=timeout))
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    report = install_mcp(InstallerClient.CLAUDE, project_root=tmp_path, home=tmp_path)

    assert calls[1].command == [
        "claude",
        "mcp",
        "add",
        "--transport",
        "stdio",
        "cheapy",
        "--",
        str(executable.resolve()),
        "mcp",
    ]
    assert calls[1].cwd == tmp_path
    assert report["status"] == "ok"
    assert report["client"] == "claude"
    assert report["method"] == "official_cli"
    assert report["rollback_path"] is None
    assert report["mcp_entry"] == {"command": str(executable.resolve()), "args": ["mcp"]}
    assert report["hooks"]["codex"]["status"] == "not_applicable"


def test_install_unknown_official_cli_failure_does_not_use_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")

    def fake_which(name: str) -> str | None:
        return {"cheapy": str(executable), "codex": "/usr/local/bin/codex"}.get(name)

    def fake_run(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="unexpected config failure",
        )

    def fail_direct_fallback(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("direct config fallback should not run")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)
    monkeypatch.setattr(
        "cheapy.mcp_installer._install_via_direct_config",
        fail_direct_fallback,
    )

    with pytest.raises(InstallerError) as exc_info:
        install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert exc_info.value.code == "CLIENT_CONFIG_UNAVAILABLE"
    assert "codex mcp add cheapy -- /opt/bin/cheapy mcp" in exc_info.value.suggestion


def test_install_missing_official_cli_reports_manual_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")

    def fake_which(name: str) -> str | None:
        return str(executable) if name == "cheapy" else None

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    with pytest.raises(InstallerError) as exc_info:
        install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert exc_info.value.code == "CLIENT_CONFIG_UNAVAILABLE"
    assert "codex mcp add cheapy -- /opt/bin/cheapy mcp" in exc_info.value.suggestion
