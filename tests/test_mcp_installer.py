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
    kwargs: dict[str, Any]


def _not_applicable() -> dict[str, str]:
    return {"status": "not_applicable"}


def _hook_report(client: InstallerClient, project_root: Path) -> dict[str, Any]:
    if client is InstallerClient.CODEX:
        return {
            "codex_skill": {
                "status": "updated",
                "path": str(
                    project_root / ".codex" / "skills" / "cheapy" / "SKILL.md"
                ),
            },
            "agents_hook": {
                "status": "updated",
                "path": str(project_root / "AGENTS.md"),
            },
            "claude_instructions": _not_applicable(),
            "claude_hook": _not_applicable(),
            "manual_steps": [],
        }

    return {
        "codex_skill": _not_applicable(),
        "agents_hook": _not_applicable(),
        "claude_instructions": {
            "status": "updated",
            "path": str(project_root / ".cheapy" / "claude-instructions.md"),
        },
        "claude_hook": {
            "status": "updated",
            "path": str(project_root / "CLAUDE.md"),
        },
        "manual_steps": [],
    }


def _expected_success_report(
    client: InstallerClient,
    executable: Path,
    project_root: Path,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "client": client.value,
        "server_name": "cheapy",
        "method": "official_cli",
        "executable": str(executable.resolve()),
        "config_path": None,
        "rollback_path": None,
        "mcp_entry": {"command": str(executable.resolve()), "args": ["mcp"]},
        **_hook_report(client, project_root),
    }


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


def test_manual_install_command_quotes_executable_paths_with_spaces() -> None:
    assert (
        manual_install_command(
            InstallerClient.CODEX,
            Path("/tmp/Cheapy Bin/cheapy"),
        )
        == "codex mcp add cheapy -- '/tmp/Cheapy Bin/cheapy' mcp"
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
        assert kwargs["stdin"] is subprocess.DEVNULL
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
    assert error.suggestion == (
        "Install the cheapy-flights package first, then ensure the cheapy "
        "executable is on PATH."
    )
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
        "error: unrecognized subcommand 'mcp'",
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
    client_executable = Path("/usr/local/bin/codex")
    calls: list[RunCall] = []

    def fake_which(name: str) -> str | None:
        return {"cheapy": str(executable), "codex": str(client_executable)}.get(name)

    def fake_run(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(RunCall(command=command, kwargs=kwargs))
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    report = install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert calls[1].command == [
        str(client_executable),
        "mcp",
        "add",
        "cheapy",
        "--",
        str(executable.resolve()),
        "mcp",
    ]
    assert calls[0].kwargs == {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "timeout": 5.0,
    }
    assert calls[1].kwargs == {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "timeout": 30.0,
        "cwd": tmp_path,
    }
    assert report == _expected_success_report(
        InstallerClient.CODEX,
        executable,
        tmp_path,
    )


def test_install_claude_uses_official_cli_and_returns_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")
    client_executable = Path("/usr/local/bin/claude")
    calls: list[RunCall] = []

    def fake_which(name: str) -> str | None:
        return {"cheapy": str(executable), "claude": str(client_executable)}.get(name)

    def fake_run(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(RunCall(command=command, kwargs=kwargs))
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    report = install_mcp(InstallerClient.CLAUDE, project_root=tmp_path, home=tmp_path)

    assert calls[1].command == [
        str(client_executable),
        "mcp",
        "add",
        "--transport",
        "stdio",
        "cheapy",
        "--",
        str(executable.resolve()),
        "mcp",
    ]
    assert calls[0].kwargs["stdin"] is subprocess.DEVNULL
    assert calls[1].kwargs == {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "timeout": 30.0,
        "cwd": tmp_path,
    }
    assert report == _expected_success_report(
        InstallerClient.CLAUDE,
        executable,
        tmp_path,
    )


def test_install_recoverable_official_cli_failure_enters_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")

    def fake_which(name: str) -> str | None:
        return {"cheapy": str(executable), "codex": "/usr/local/bin/codex"}.get(name)

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr="error: unrecognized subcommand 'mcp'",
        )

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    report = install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    config_path = tmp_path / ".codex" / "config.toml"
    assert report["status"] == "ok"
    assert report["client"] == "codex"
    assert report["method"] == "direct_edit"
    assert report["config_path"] == str(config_path)
    assert report["rollback_path"]
    assert Path(str(report["rollback_path"])).exists()
    assert config_path.exists()
    assert 'command = "/opt/bin/cheapy"' in config_path.read_text(encoding="utf-8")


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

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    with pytest.raises(InstallerError) as exc_info:
        install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert exc_info.value.code == "CLIENT_CONFIG_UNAVAILABLE"
    assert "codex mcp add cheapy -- /opt/bin/cheapy mcp" in exc_info.value.suggestion
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_install_unknown_official_cli_failure_redacts_and_bounds_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")
    secret_output = "\n".join(
        [
            "fatal config write failed",
            "Authorization: Bearer sk-live-secret-token",
            "TOKEN=super-token-value",
            "api_key = abc123",
            "password: p@ssw0rd",
            "secret=my-secret",
            "details: " + ("x" * 600),
        ]
    )

    def fake_which(name: str) -> str | None:
        return {"cheapy": str(executable), "codex": "/usr/local/bin/codex"}.get(name)

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=secret_output)

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    with pytest.raises(InstallerError) as exc_info:
        install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    message = exc_info.value.message
    assert "sk-live-secret-token" not in message
    assert "super-token-value" not in message
    assert "abc123" not in message
    assert "p@ssw0rd" not in message
    assert "my-secret" not in message
    assert "\n" not in message
    assert len(message) <= 320


def test_install_missing_official_cli_performs_direct_fallback(
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

    report = install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    config_path = tmp_path / ".codex" / "config.toml"
    assert report["status"] == "ok"
    assert report["client"] == "codex"
    assert report["method"] == "direct_edit"
    assert report["config_path"] == str(config_path)
    assert report["rollback_path"]
    assert Path(str(report["rollback_path"])).exists()
    assert config_path.exists()
    assert 'command = "/opt/bin/cheapy"' in config_path.read_text(encoding="utf-8")


def test_install_missing_official_cli_preserves_parse_error_with_manual_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = Path("/opt/bin/cheapy")
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("[mcp_servers\n", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        return str(executable) if name == "cheapy" else None

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="0.1.0\n", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", fake_which)
    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    with pytest.raises(InstallerError) as exc_info:
        install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert exc_info.value.code == "CONFIG_PARSE_FAILED"
    assert "Could not parse Codex TOML config" in exc_info.value.message
    assert "Run manually: codex mcp add cheapy -- /opt/bin/cheapy mcp" in (
        exc_info.value.suggestion
    )
