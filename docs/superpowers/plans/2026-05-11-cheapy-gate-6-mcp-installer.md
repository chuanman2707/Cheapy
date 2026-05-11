# Cheapy Gate 6 MCP Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `cheapy mcp install --client codex|claude` with official-client-first registration, safe direct-edit fallback, and selected-client agent instruction hooks.

**Architecture:** Keep MCP runtime code separate from installer code. Add `cheapy/mcp_installer.py` for orchestration, `cheapy/client_configs.py` for direct config fallback, and `cheapy/agent_hooks.py` for project-local instruction hooks. Convert the existing `cheapy mcp` Typer command into a group whose no-subcommand path still runs the stdio MCP server.

**Tech Stack:** Python 3.12+, Typer, Pydantic-adjacent stdlib dataclasses, `tomlkit` for Codex TOML preservation, stdlib `json`, `pathlib`, `subprocess`, `shutil`, `tempfile`, `os.replace`, `stat`, pytest, uv.

---

## Current Baseline

Run before starting implementation:

```bash
uv run pytest -q
git status --short
```

Expected baseline:

```text
105 passed
```

The working tree may contain unrelated untracked project docs. Do not stage them unless this plan explicitly names them.

Read required project-local skills before editing MCP, CLI, packaging, or tests:

```bash
sed -n '1,220p' .codex/skills/mcp-builder/SKILL.md
sed -n '1,220p' .codex/skills/ai-native-cli/SKILL.md
sed -n '1,220p' .codex/skills/python-testing-patterns/SKILL.md
sed -n '1,220p' .codex/skills/uv-package-manager/SKILL.md
```

## File Structure

Create:

- `cheapy/mcp_installer.py`: installer orchestration, client enum, executable resolution/version verification, official CLI invocation, fallback routing, report/error payloads.
- `cheapy/client_configs.py`: direct config fallback editors for Codex TOML and Claude JSON, redacted rollback artifact creation, atomic writes, permission helpers, manual command helpers.
- `cheapy/agent_hooks.py`: selected-client instruction file creation and managed block updates for `AGENTS.md`/`CLAUDE.md`.
- `tests/test_mcp_installer.py`: installer orchestration, official CLI behavior, executable verification, error/report payload tests.
- `tests/test_client_configs.py`: direct edit fallback, rollback artifacts, missing config creation, redaction, TOML/JSON preservation, idempotency.
- `tests/test_agent_hooks.py`: selected-client instruction files, managed block behavior, symlink handling.

Modify:

- `pyproject.toml`: add `tomlkit`.
- `uv.lock`: update via `uv add tomlkit`.
- `cheapy/cli.py`: change `mcp` command into a group supporting both `cheapy mcp` and `cheapy mcp install --client ...`.
- `tests/test_cli.py`: add install command JSON/error tests.
- `tests/test_mcp.py`: add or keep protocol regression for `python -m cheapy mcp` after CLI nesting changes.

Do not modify:

- `cheapy/mcp.py` tool behavior.
- `cheapy/search.py`.
- `cheapy/models/contracts.py`.
- Provider modules.
- README unless the user separately asks for docs.

---

### Task 1: Add TOML Writer Dependency

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add `tomlkit`**

Run:

```bash
uv add tomlkit
```

Expected:

```text
pyproject.toml and uv.lock are updated.
```

- [ ] **Step 2: Verify import**

Run:

```bash
uv run python -c "import tomlkit; print(tomlkit.__name__)"
```

Expected:

```text
tomlkit
```

- [ ] **Step 3: Run current focused tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_mcp.py -v
```

Expected:

```text
20 passed
```

- [ ] **Step 4: Commit dependency update**

Run:

```bash
git add pyproject.toml uv.lock
git commit -m "build: add tomlkit for installer config edits" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds with only `pyproject.toml` and `uv.lock` staged.

---

### Task 2: Build Installer Foundation

**Files:**

- Create: `cheapy/mcp_installer.py`
- Create: `tests/test_mcp_installer.py`

- [ ] **Step 1: Write failing installer foundation tests**

Create `tests/test_mcp_installer.py`:

```python
from __future__ import annotations

from pathlib import Path
import subprocess

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


def test_build_mcp_entry_uses_absolute_cheapy_command() -> None:
    entry = build_mcp_entry(Path("/opt/bin/cheapy"))

    assert entry == {"command": "/opt/bin/cheapy", "args": ["mcp"]}


def test_manual_install_commands_are_exact() -> None:
    executable = Path("/opt/bin/cheapy")

    assert manual_install_command(InstallerClient.CODEX, executable) == (
        "codex mcp add cheapy -- /opt/bin/cheapy mcp"
    )
    assert manual_install_command(InstallerClient.CLAUDE, executable) == (
        "claude mcp add --transport stdio cheapy -- /opt/bin/cheapy mcp"
    )


def test_resolve_cheapy_executable_fails_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: None)

    with pytest.raises(InstallerError) as exc_info:
        resolve_cheapy_executable()

    assert exc_info.value.code == "MISSING_EXECUTABLE"
    assert exc_info.value.exit_code == 1
    assert "Install Cheapy" in exc_info.value.suggestion


def test_resolve_cheapy_executable_verifies_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: "/tmp/cheapy")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == ["/tmp/cheapy", "--version"]
        return subprocess.CompletedProcess(args, 0, stdout="0.1.0\n", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    assert resolve_cheapy_executable() == Path("/tmp/cheapy")


def test_resolve_cheapy_executable_rejects_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: "/tmp/cheapy")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="9.9.9\n", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    with pytest.raises(InstallerError) as exc_info:
        resolve_cheapy_executable()

    assert exc_info.value.code == "EXECUTABLE_MISMATCH"
    assert "0.1.0" in exc_info.value.message


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("No such command 'mcp'", True),
        ("error: unrecognized subcommand 'mcp'", True),
        ("server cheapy already exists", True),
        ("permission denied writing config", False),
        ("unexpected traceback", False),
    ],
)
def test_recoverable_official_cli_failure_classification(
    stderr: str,
    expected: bool,
) -> None:
    result = subprocess.CompletedProcess(["client"], 1, stdout="", stderr=stderr)

    assert is_recoverable_official_cli_failure(result) is expected


def test_install_codex_uses_official_cli_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    executable = tmp_path / "cheapy"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr("cheapy.mcp_installer.resolve_cheapy_executable", lambda: executable)
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: "/usr/bin/codex")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    report = install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert calls == [["/usr/bin/codex", "mcp", "add", "cheapy", "--", str(executable), "mcp"]]
    assert report["status"] == "ok"
    assert report["client"] == "codex"
    assert report["method"] == "official_cli"
    assert report["rollback_path"] is None
    assert report["mcp_entry"] == {"command": str(executable), "args": ["mcp"]}
    assert report["claude_instructions"]["status"] == "not_applicable"


def test_install_claude_uses_official_cli_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    executable = tmp_path / "cheapy"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr("cheapy.mcp_installer.resolve_cheapy_executable", lambda: executable)
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: "/usr/bin/claude")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("cheapy.mcp_installer.subprocess.run", fake_run)

    report = install_mcp(InstallerClient.CLAUDE, project_root=tmp_path, home=tmp_path)

    assert calls == [
        [
            "/usr/bin/claude",
            "mcp",
            "add",
            "--transport",
            "stdio",
            "cheapy",
            "--",
            str(executable),
            "mcp",
        ]
    ]
    assert report["status"] == "ok"
    assert report["client"] == "claude"
    assert report["method"] == "official_cli"
    assert report["rollback_path"] is None
    assert report["codex_skill"]["status"] == "not_applicable"


def test_install_does_not_fallback_for_unknown_official_cli_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "cheapy"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr("cheapy.mcp_installer.resolve_cheapy_executable", lambda: executable)
    monkeypatch.setattr("cheapy.mcp_installer.shutil.which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(
        "cheapy.mcp_installer.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="permission denied"),
    )

    with pytest.raises(InstallerError) as exc_info:
        install_mcp(InstallerClient.CODEX, project_root=tmp_path, home=tmp_path)

    assert exc_info.value.code == "CLIENT_CONFIG_UNAVAILABLE"
    assert "codex mcp add cheapy --" in exc_info.value.suggestion
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/test_mcp_installer.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.mcp_installer'`.

- [ ] **Step 3: Implement installer foundation**

Create `cheapy/mcp_installer.py`:

```python
"""Install Cheapy as a local MCP server for supported clients."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import shutil
import subprocess
from typing import Any

from cheapy import __version__


SERVER_NAME = "cheapy"


class InstallerClient(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"


@dataclass(frozen=True)
class InstallerError(Exception):
    code: str
    message: str
    suggestion: str
    exit_code: int = 1

    def payload(self) -> dict[str, Any]:
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
        }


def build_mcp_entry(executable: Path) -> dict[str, Any]:
    return {"command": str(executable), "args": ["mcp"]}


def manual_install_command(client: InstallerClient, executable: Path) -> str:
    if client is InstallerClient.CODEX:
        return f"codex mcp add {SERVER_NAME} -- {executable} mcp"
    return f"claude mcp add --transport stdio {SERVER_NAME} -- {executable} mcp"


def resolve_cheapy_executable() -> Path:
    resolved = shutil.which("cheapy")
    if resolved is None:
        raise InstallerError(
            code="MISSING_EXECUTABLE",
            message="cheapy executable was not found on PATH.",
            suggestion="Install Cheapy with the cheapy-flights package or add the cheapy executable to PATH.",
        )

    executable = Path(resolved).resolve()
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except OSError as exc:
        raise InstallerError(
            code="MISSING_EXECUTABLE",
            message=f"cheapy executable could not be executed: {exc}",
            suggestion="Reinstall cheapy-flights or fix the executable on PATH.",
        ) from exc

    observed = result.stdout.strip()
    if result.returncode != 0 or observed != __version__:
        raise InstallerError(
            code="EXECUTABLE_MISMATCH",
            message=f"Resolved cheapy executable version '{observed}' does not match running package version '{__version__}'.",
            suggestion="Fix PATH so 'cheapy' points to this cheapy-flights install, then rerun the installer.",
        )

    return executable


def official_cli_command(client: InstallerClient, executable: Path, client_executable: str) -> list[str]:
    if client is InstallerClient.CODEX:
        return [client_executable, "mcp", "add", SERVER_NAME, "--", str(executable), "mcp"]
    return [
        client_executable,
        "mcp",
        "add",
        "--transport",
        "stdio",
        SERVER_NAME,
        "--",
        str(executable),
        "mcp",
    ]


def is_recoverable_official_cli_failure(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    recoverable_fragments = (
        "no such command",
        "unrecognized subcommand",
        "unknown command",
        "already exists",
        "server cheapy already exists",
    )
    return any(fragment in text for fragment in recoverable_fragments)


def _not_applicable() -> dict[str, Any]:
    return {"status": "not_applicable", "path": None}


def install_mcp(
    client: InstallerClient,
    *,
    project_root: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    project_root = (project_root or Path.cwd()).resolve()
    home = (home or Path.home()).resolve()
    executable = resolve_cheapy_executable()
    entry = build_mcp_entry(executable)
    client_executable = shutil.which(client.value)
    manual_command = manual_install_command(client, executable)
    config_path: str | None = None
    rollback_path: str | None = None
    method: str

    if client_executable is not None:
        command = official_cli_command(client, executable, client_executable)
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            cwd=project_root,
        )
        if result.returncode == 0:
            method = "official_cli"
        elif is_recoverable_official_cli_failure(result):
            method = "direct_edit"
            config_path, rollback_path = _direct_edit(client, entry, project_root, home)
        else:
            stderr_summary = result.stderr.strip() or result.stdout.strip() or "official client command failed"
            raise InstallerError(
                code="CLIENT_CONFIG_UNAVAILABLE",
                message=f"{client.value} official installer failed: {stderr_summary}",
                suggestion=f"Run manually: {manual_command}",
            )
    else:
        method = "direct_edit"
        config_path, rollback_path = _direct_edit(client, entry, project_root, home)

    hooks = _install_hooks(client, project_root)

    return {
        "status": "ok",
        "client": client.value,
        "server_name": SERVER_NAME,
        "method": method,
        "executable": str(executable),
        "config_path": config_path,
        "rollback_path": rollback_path,
        "mcp_entry": entry,
        "codex_skill": hooks["codex_skill"],
        "agents_hook": hooks["agents_hook"],
        "claude_instructions": hooks["claude_instructions"],
        "claude_hook": hooks["claude_hook"],
        "manual_steps": hooks["manual_steps"],
    }


def _direct_edit(
    client: InstallerClient,
    entry: dict[str, Any],
    project_root: Path,
    home: Path,
) -> tuple[str, str]:
    from cheapy.client_configs import edit_client_config

    result = edit_client_config(client, entry, project_root=project_root, home=home)
    return str(result.config_path), str(result.rollback_path)


def _install_hooks(client: InstallerClient, project_root: Path) -> dict[str, Any]:
    from cheapy.agent_hooks import install_agent_hooks

    return install_agent_hooks(client, project_root)
```

- [ ] **Step 4: Run installer foundation tests**

Run:

```bash
uv run pytest tests/test_mcp_installer.py -v
```

Expected: the pure helper tests pass. `test_install_codex_uses_official_cli_success` and `test_install_claude_uses_official_cli_success` fail with `ModuleNotFoundError: No module named 'cheapy.agent_hooks'`; that module is added in Task 4.

- [ ] **Step 5: Commit foundation**

Run:

```bash
git add cheapy/mcp_installer.py tests/test_mcp_installer.py
git commit -m "feat: add mcp installer foundation" -m "AI-Model: GPT-5 Codex"
```

Expected: commit includes only `cheapy/mcp_installer.py` and `tests/test_mcp_installer.py`.

---

### Task 3: Add Direct Config Fallback Editors

**Files:**

- Create: `cheapy/client_configs.py`
- Create: `tests/test_client_configs.py`
- Modify: `tests/test_mcp_installer.py`

- [ ] **Step 1: Write failing config editor tests**

Create `tests/test_client_configs.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
import stat

import tomlkit

from cheapy.client_configs import edit_client_config, redact_secret_values
from cheapy.mcp_installer import InstallerClient


ENTRY = {"command": "/opt/bin/cheapy", "args": ["mcp"]}


def test_codex_missing_config_creates_minimal_config_and_rollback(tmp_path: Path) -> None:
    result = edit_client_config(
        InstallerClient.CODEX,
        ENTRY,
        project_root=tmp_path / "project",
        home=tmp_path,
    )

    config_path = tmp_path / ".codex" / "config.toml"
    assert result.config_path == config_path
    assert config_path.exists()
    parsed = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["cheapy"]["command"] == "/opt/bin/cheapy"
    assert list(parsed["mcp_servers"]["cheapy"]["args"]) == ["mcp"]
    assert result.rollback_path.exists()
    rollback = json.loads(result.rollback_path.read_text(encoding="utf-8"))
    assert rollback["previous_entry_exists"] is False
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.rollback_path.stat().st_mode) == 0o600


def test_codex_preserves_comments_and_unrelated_servers(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        "# keep me\n"
        "model = \"gpt-5\"\n"
        "\n"
        "[mcp_servers.context7]\n"
        "command = \"npx\"\n"
        "args = [\"-y\", \"context7\"]\n",
        encoding="utf-8",
    )

    result = edit_client_config(
        InstallerClient.CODEX,
        ENTRY,
        project_root=tmp_path / "project",
        home=tmp_path,
    )

    text = config_path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "[mcp_servers.context7]" in text
    assert "[mcp_servers.cheapy]" in text
    assert result.rollback_path.exists()


def test_codex_updates_stale_entry_idempotently(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        "[mcp_servers.cheapy]\n"
        "command = \"/old/cheapy\"\n"
        "args = [\"old\"]\n",
        encoding="utf-8",
    )

    first = edit_client_config(InstallerClient.CODEX, ENTRY, project_root=tmp_path, home=tmp_path)
    second = edit_client_config(InstallerClient.CODEX, ENTRY, project_root=tmp_path, home=tmp_path)

    text = config_path.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.cheapy]") == 1
    parsed = tomlkit.parse(text)
    assert parsed["mcp_servers"]["cheapy"]["command"] == "/opt/bin/cheapy"
    assert list(parsed["mcp_servers"]["cheapy"]["args"]) == ["mcp"]
    assert first.rollback_path != second.rollback_path


def test_claude_missing_config_creates_project_scoped_local_entry(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    result = edit_client_config(
        InstallerClient.CLAUDE,
        ENTRY,
        project_root=project_root,
        home=tmp_path,
    )

    config_path = tmp_path / ".claude.json"
    parsed = json.loads(config_path.read_text(encoding="utf-8"))
    entry = parsed["projects"][str(project_root)]["mcpServers"]["cheapy"]
    assert entry == {
        "type": "stdio",
        "command": "/opt/bin/cheapy",
        "args": ["mcp"],
        "env": {},
    }
    assert result.config_path == config_path
    assert result.rollback_path.exists()


def test_claude_preserves_unrelated_projects_and_servers(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "projects": {
                    "/other": {
                        "mcpServers": {
                            "db": {
                                "type": "stdio",
                                "command": "npx",
                                "args": ["db"],
                                "env": {"API_KEY": "secret"},
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    edit_client_config(InstallerClient.CLAUDE, ENTRY, project_root=project_root, home=tmp_path)

    parsed = json.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["projects"]["/other"]["mcpServers"]["db"]["env"]["API_KEY"] == "secret"
    assert parsed["projects"][str(project_root)]["mcpServers"]["cheapy"]["command"] == "/opt/bin/cheapy"


def test_rollback_artifact_redacts_secret_like_values(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        "[mcp_servers.cheapy]\n"
        "command = \"/old/cheapy\"\n"
        "args = [\"mcp\"]\n"
        "[mcp_servers.cheapy.env]\n"
        "API_KEY = \"super-secret\"\n",
        encoding="utf-8",
    )

    result = edit_client_config(InstallerClient.CODEX, ENTRY, project_root=tmp_path, home=tmp_path)

    rollback_text = result.rollback_path.read_text(encoding="utf-8")
    assert "super-secret" not in rollback_text
    assert "***REDACTED***" in rollback_text


def test_redact_secret_values_redacts_nested_secret_keys() -> None:
    assert redact_secret_values(
        {
            "headers": {"Authorization": "Bearer abc"},
            "env": {"TOKEN": "abc", "SAFE": "ok"},
            "args": ["--api-key", "abc"],
        }
    ) == {
        "headers": "***REDACTED***",
        "env": {"TOKEN": "***REDACTED***", "SAFE": "ok"},
        "args": ["--api-key", "***REDACTED***"],
    }
```

- [ ] **Step 2: Run failing config editor tests**

Run:

```bash
uv run pytest tests/test_client_configs.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.client_configs'`.

- [ ] **Step 3: Implement direct config editors**

Create `cheapy/client_configs.py`:

```python
"""Direct MCP client config fallback editors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import tomlkit
from tomlkit.items import Table

from cheapy.mcp_installer import InstallerClient, InstallerError, SERVER_NAME


SECRET_KEY_RE = re.compile(r"(token|secret|api[_-]?key|authorization|bearer|password)", re.IGNORECASE)


@dataclass(frozen=True)
class ConfigEditResult:
    config_path: Path
    rollback_path: Path


def edit_client_config(
    client: InstallerClient,
    entry: dict[str, Any],
    *,
    project_root: Path,
    home: Path,
) -> ConfigEditResult:
    if client is InstallerClient.CODEX:
        return _edit_codex_config(entry, home=home)
    return _edit_claude_config(entry, project_root=project_root, home=home)


def redact_secret_values(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            if SECRET_KEY_RE.search(str(key)):
                redacted[key] = "***REDACTED***"
            elif key in {"env", "headers"}:
                redacted[key] = redact_secret_values(nested)
            else:
                redacted[key] = redact_secret_values(nested)
        return redacted
    if isinstance(value, list):
        result: list[Any] = []
        redact_next = False
        for item in value:
            if redact_next:
                result.append("***REDACTED***")
                redact_next = False
                continue
            result.append(redact_secret_values(item))
            if isinstance(item, str) and SECRET_KEY_RE.search(item):
                redact_next = True
        return result
    return value


def _edit_codex_config(entry: dict[str, Any], *, home: Path) -> ConfigEditResult:
    codex_dir = home / ".codex"
    config_path = codex_dir / "config.toml"
    _ensure_safe_parent(codex_dir, home)
    codex_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    if config_path.exists():
        try:
            document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise InstallerError(
                code="CONFIG_PARSE_FAILED",
                message=f"Could not parse Codex config at {config_path}.",
                suggestion="Fix the TOML file or run: codex mcp add cheapy -- /absolute/path/to/cheapy mcp",
            ) from exc
    else:
        document = tomlkit.document()

    mcp_servers = document.get("mcp_servers")
    if mcp_servers is None:
        mcp_servers = tomlkit.table()
        document["mcp_servers"] = mcp_servers
    if not isinstance(mcp_servers, Table):
        raise InstallerError(
            code="CONFIG_PARSE_FAILED",
            message="Codex config key 'mcp_servers' is not a TOML table.",
            suggestion="Fix ~/.codex/config.toml or run the Codex MCP add command manually.",
        )

    previous = _plain_data(mcp_servers.get(SERVER_NAME))
    cheapy_table = tomlkit.table()
    cheapy_table["command"] = entry["command"]
    cheapy_table["args"] = entry["args"]
    mcp_servers[SERVER_NAME] = cheapy_table

    rollback_path = _write_rollback_artifact(
        client=InstallerClient.CODEX,
        config_path=config_path,
        previous_entry=previous,
        new_entry=entry,
    )
    _atomic_write(config_path, tomlkit.dumps(document))
    _chmod_600(config_path)
    return ConfigEditResult(config_path=config_path, rollback_path=rollback_path)


def _edit_claude_config(
    entry: dict[str, Any],
    *,
    project_root: Path,
    home: Path,
) -> ConfigEditResult:
    config_path = home / ".claude.json"
    if config_path.exists():
        try:
            document = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallerError(
                code="CONFIG_PARSE_FAILED",
                message=f"Could not parse Claude config at {config_path}.",
                suggestion="Fix ~/.claude.json or run the Claude MCP add command manually.",
            ) from exc
    else:
        document = {}

    projects = document.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise InstallerError(
            code="CONFIG_PARSE_FAILED",
            message="Claude config key 'projects' is not an object.",
            suggestion="Fix ~/.claude.json or run the Claude MCP add command manually.",
        )
    project = projects.setdefault(str(project_root), {})
    if not isinstance(project, dict):
        raise InstallerError(
            code="CONFIG_PARSE_FAILED",
            message="Claude project config is not an object.",
            suggestion="Fix ~/.claude.json or run the Claude MCP add command manually.",
        )
    servers = project.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallerError(
            code="CONFIG_PARSE_FAILED",
            message="Claude project key 'mcpServers' is not an object.",
            suggestion="Fix ~/.claude.json or run the Claude MCP add command manually.",
        )

    previous = servers.get(SERVER_NAME)
    servers[SERVER_NAME] = {
        "type": "stdio",
        "command": entry["command"],
        "args": entry["args"],
        "env": {},
    }

    rollback_path = _write_rollback_artifact(
        client=InstallerClient.CLAUDE,
        config_path=config_path,
        previous_entry=previous,
        new_entry=servers[SERVER_NAME],
    )
    _atomic_write(config_path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    _chmod_600(config_path)
    return ConfigEditResult(config_path=config_path, rollback_path=rollback_path)


def _write_rollback_artifact(
    *,
    client: InstallerClient,
    config_path: Path,
    previous_entry: Any,
    new_entry: dict[str, Any],
) -> Path:
    rollback_dir = config_path.parent / ".cheapy-rollbacks"
    rollback_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rollback_path = rollback_dir / f"{client.value}-{SERVER_NAME}-{timestamp}.json"
    payload = {
        "client": client.value,
        "config_path": str(config_path),
        "server_name": SERVER_NAME,
        "previous_entry_exists": previous_entry is not None,
        "previous_entry": redact_secret_values(previous_entry),
        "new_entry": redact_secret_values(new_entry),
        "manual_rollback": (
            f"Edit {config_path} and restore or remove the '{SERVER_NAME}' MCP server entry "
            "according to previous_entry_exists and previous_entry."
        ),
    }
    rollback_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _chmod_600(rollback_path)
    return rollback_path


def _plain_data(value: Any) -> Any:
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def _ensure_safe_parent(parent: Path, home: Path) -> None:
    resolved_home = home.resolve()
    resolved_parent = parent.resolve() if parent.exists() else parent
    if not str(resolved_parent).startswith(str(resolved_home)):
        raise InstallerError(
            code="CLIENT_CONFIG_UNAVAILABLE",
            message=f"Refusing to create config outside home: {parent}",
            suggestion="Run the official MCP add command manually.",
        )
    if parent.exists() and not parent.is_dir():
        raise InstallerError(
            code="CLIENT_CONFIG_UNAVAILABLE",
            message=f"Config parent exists but is not a directory: {parent}",
            suggestion="Fix the config path or run the official MCP add command manually.",
        )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise InstallerError(
                code="CONFIG_WRITE_FAILED",
                message=f"Could not write config file at {path}: {exc}",
                suggestion="Check file permissions and rerun the installer.",
            ) from exc


def _chmod_600(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        return
```

- [ ] **Step 4: Run config editor tests**

Run:

```bash
uv run pytest tests/test_client_configs.py -v
```

Expected: PASS.

- [ ] **Step 5: Run installer tests again**

Run:

```bash
uv run pytest tests/test_mcp_installer.py -v
```

Expected: `test_install_codex_uses_official_cli_success` and `test_install_claude_uses_official_cli_success` still fail with missing `cheapy.agent_hooks`; no config editor failures remain.

- [ ] **Step 6: Commit config fallback**

Run:

```bash
git add cheapy/client_configs.py tests/test_client_configs.py tests/test_mcp_installer.py
git commit -m "feat: add mcp client config fallback" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 4: Add Selected-Client Agent Hooks

**Files:**

- Create: `cheapy/agent_hooks.py`
- Create: `tests/test_agent_hooks.py`
- Modify: `tests/test_mcp_installer.py`

- [ ] **Step 1: Write failing agent hook tests**

Create `tests/test_agent_hooks.py`:

```python
from __future__ import annotations

from pathlib import Path

from cheapy.agent_hooks import install_agent_hooks
from cheapy.mcp_installer import InstallerClient


def test_codex_hooks_create_skill_and_agents_block_only(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Agent Instructions\n\nKeep me.\n", encoding="utf-8")

    result = install_agent_hooks(InstallerClient.CODEX, tmp_path)

    skill_path = tmp_path / ".codex" / "skills" / "cheapy" / "SKILL.md"
    assert skill_path.exists()
    skill_text = skill_path.read_text(encoding="utf-8")
    assert "search_cheapest_flights" in skill_text
    assert "round-trip search is deferred" in skill_text
    agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Keep me." in agents_text
    assert "BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS" in agents_text
    assert ".codex/skills/cheapy/SKILL.md" in agents_text
    assert result["codex_skill"]["status"] == "updated"
    assert result["agents_hook"]["status"] == "updated"
    assert result["claude_instructions"]["status"] == "not_applicable"
    assert result["claude_hook"]["status"] == "not_applicable"


def test_codex_hooks_are_idempotent(tmp_path: Path) -> None:
    install_agent_hooks(InstallerClient.CODEX, tmp_path)
    install_agent_hooks(InstallerClient.CODEX, tmp_path)

    agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_text.count("BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS") == 1


def test_claude_hooks_create_instruction_and_claude_block_only(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Claude Instructions\n\nKeep me.\n", encoding="utf-8")

    result = install_agent_hooks(InstallerClient.CLAUDE, tmp_path)

    instructions_path = tmp_path / ".cheapy" / "claude-instructions.md"
    assert instructions_path.exists()
    instructions_text = instructions_path.read_text(encoding="utf-8")
    assert "search_cheapest_flights" in instructions_text
    assert "round-trip search is deferred" in instructions_text
    claude_text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Keep me." in claude_text
    assert "BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS" in claude_text
    assert ".cheapy/claude-instructions.md" in claude_text
    assert result["codex_skill"]["status"] == "not_applicable"
    assert result["agents_hook"]["status"] == "not_applicable"
    assert result["claude_instructions"]["status"] == "updated"
    assert result["claude_hook"]["status"] == "updated"


def test_claude_symlink_updates_target_without_replacing_symlink(tmp_path: Path) -> None:
    agents_path = tmp_path / "AGENTS.md"
    claude_path = tmp_path / "CLAUDE.md"
    agents_path.write_text("# Shared Instructions\n", encoding="utf-8")
    claude_path.symlink_to(agents_path)

    install_agent_hooks(InstallerClient.CLAUDE, tmp_path)

    assert claude_path.is_symlink()
    text = agents_path.read_text(encoding="utf-8")
    assert "BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS" in text
    assert ".cheapy/claude-instructions.md" in text


def test_managed_block_replaces_only_managed_content(tmp_path: Path) -> None:
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text(
        "before\n"
        "<!-- BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS -->\n"
        "old\n"
        "<!-- END CHEAPY MANAGED CODEX INSTRUCTIONS -->\n"
        "after\n",
        encoding="utf-8",
    )

    install_agent_hooks(InstallerClient.CODEX, tmp_path)

    text = agents_path.read_text(encoding="utf-8")
    assert "before" in text
    assert "after" in text
    assert "old" not in text
    assert text.count("BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS") == 1
```

- [ ] **Step 2: Run failing agent hook tests**

Run:

```bash
uv run pytest tests/test_agent_hooks.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.agent_hooks'`.

- [ ] **Step 3: Implement agent hooks**

Create `cheapy/agent_hooks.py`:

```python
"""Project-local agent instruction hooks for Cheapy MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cheapy.mcp_installer import InstallerClient


CODEX_BEGIN = "<!-- BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CODEX_END = "<!-- END CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CLAUDE_BEGIN = "<!-- BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"
CLAUDE_END = "<!-- END CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"


CODEX_SKILL_TEXT = """---
name: cheapy-flight-search
description: Use when a user asks an agent to search flights with Cheapy, normalize airport aliases to IATA codes, or call Cheapy MCP tools.
---

# Cheapy Flight Search

Use this skill before calling Cheapy MCP tools.

Cheapy currently supports exact one-way MVP searches through the `search_cheapest_flights` MCP tool.

Always pass origin and destination as 3-letter IATA airport codes. Do not pass city names, airport names, or Vietnamese aliases into Cheapy tools. Ask for clarification when an airport is ambiguous.

Normalize dates to ISO `YYYY-MM-DD` before calling the tool. Required fields are origin, destination, and departure date. If the user does not specify passengers, use Contract V1 defaults: 1 adult, 0 children, 0 infants. If the user indicates non-default passengers but leaves counts unclear, ask a follow-up question.

Call only `search_cheapest_flights`. Use `search_mode=\"exact\"` for fixed one-way searches. Expanded, flexible-date, nearby-airport, split-ticket, and round-trip search is deferred; do not pass `return_date` until Cheapy supports round trips.

Do not ask the user to choose providers. Explain mixed currency cautiously if it appears.

## Vietnamese Airport Aliases

| User text | IATA |
| --- | --- |
| nha trang | CXR |
| cam ranh | CXR |
| sân bay cam ranh | CXR |
| sài gòn | SGN |
| sai gon | SGN |
| saigon | SGN |
| tphcm | SGN |
| tp hcm | SGN |
| ho chi minh | SGN |
| ho chi minh city | SGN |
| hồ chí minh | SGN |
| hà nội | HAN |
| ha noi | HAN |
| hanoi | HAN |
| nội bài | HAN |
| noi bai | HAN |
| đà nẵng | DAD |
| da nang | DAD |
| phú quốc | PQC |
| phu quoc | PQC |

## Supported MVP Airports

Vietnam: CXR, SGN, HAN, DAD, PQC.

Regional and Asia: SIN, BKK, KUL, TPE, HKG, ICN, NRT, DOH, DXB.

Long haul: LAX, SFO, JFK, LHR, CDG, FRA, SYD, MEL.
"""


CLAUDE_INSTRUCTIONS_TEXT = """# Cheapy Flight Search

Use Cheapy for flight-search requests when the local MCP server is installed.

Cheapy currently supports exact one-way MVP searches through the `search_cheapest_flights` MCP tool.

Pass origin and destination as 3-letter IATA airport codes. Normalize clear airport aliases before calling the tool. Ask for clarification when an airport is ambiguous.

Normalize dates to ISO `YYYY-MM-DD`. Required fields are origin, destination, and departure date. If the user does not specify passengers, use Contract V1 defaults: 1 adult, 0 children, 0 infants. If the user indicates non-default passengers but leaves counts unclear, ask a follow-up question.

Call only `search_cheapest_flights`. Use `search_mode=\"exact\"` for fixed one-way searches. Expanded, flexible-date, nearby-airport, split-ticket, and round-trip search is deferred; do not pass `return_date` until Cheapy supports round trips.

Do not ask the user to choose providers. Explain mixed currency cautiously if it appears.
"""


def install_agent_hooks(client: InstallerClient, project_root: Path) -> dict[str, Any]:
    if client is InstallerClient.CODEX:
        skill_path = project_root / ".codex" / "skills" / "cheapy" / "SKILL.md"
        _write_text_if_changed(skill_path, CODEX_SKILL_TEXT)
        agents_path = project_root / "AGENTS.md"
        _upsert_managed_block(
            agents_path,
            CODEX_BEGIN,
            CODEX_END,
            "## Cheapy MCP Skill\n- For flight-search requests, read `.codex/skills/cheapy/SKILL.md` before calling Cheapy MCP tools.\n",
        )
        return {
            "codex_skill": {"status": "updated", "path": str(skill_path)},
            "agents_hook": {"status": "updated", "path": str(agents_path)},
            "claude_instructions": _not_applicable(),
            "claude_hook": _not_applicable(),
            "manual_steps": [],
        }

    instructions_path = project_root / ".cheapy" / "claude-instructions.md"
    _write_text_if_changed(instructions_path, CLAUDE_INSTRUCTIONS_TEXT)
    claude_path = project_root / "CLAUDE.md"
    _upsert_managed_block(
        claude_path,
        CLAUDE_BEGIN,
        CLAUDE_END,
        "## Cheapy MCP Instructions\n- For flight-search requests, follow `.cheapy/claude-instructions.md` before calling Cheapy MCP tools.\n",
    )
    return {
        "codex_skill": _not_applicable(),
        "agents_hook": _not_applicable(),
        "claude_instructions": {"status": "updated", "path": str(instructions_path)},
        "claude_hook": {"status": "updated", "path": str(claude_path)},
        "manual_steps": [],
    }


def _not_applicable() -> dict[str, Any]:
    return {"status": "not_applicable", "path": None}


def _write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def _upsert_managed_block(path: Path, begin: str, end: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = "# Agent Instructions\n"

    block = f"{begin}\n{body.rstrip()}\n{end}"
    if begin in text and end in text:
        before, rest = text.split(begin, 1)
        _old, after = rest.split(end, 1)
        new_text = f"{before}{block}{after}"
    else:
        separator = "\n\n" if text.strip() else ""
        new_text = f"{text.rstrip()}{separator}{block}\n"

    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
```

- [ ] **Step 4: Run agent hook tests**

Run:

```bash
uv run pytest tests/test_agent_hooks.py -v
```

Expected: PASS.

- [ ] **Step 5: Run installer foundation tests**

Run:

```bash
uv run pytest tests/test_mcp_installer.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit agent hooks**

Run:

```bash
git add cheapy/agent_hooks.py tests/test_agent_hooks.py tests/test_mcp_installer.py
git commit -m "feat: add mcp installer agent hooks" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 5: Wire CLI Command Group And Protocol Regression

**Files:**

- Modify: `cheapy/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_mcp.py`

- [ ] **Step 1: Add failing CLI install tests**

Append to `tests/test_cli.py`:

```python
def test_mcp_install_codex_prints_json(monkeypatch, tmp_path) -> None:
    def fake_install(client, *, project_root=None, home=None):
        assert client.value == "codex"
        return {
            "status": "ok",
            "client": "codex",
            "server_name": "cheapy",
            "method": "official_cli",
            "executable": "/tmp/cheapy",
            "config_path": None,
            "rollback_path": None,
            "mcp_entry": {"command": "/tmp/cheapy", "args": ["mcp"]},
            "codex_skill": {"status": "updated", "path": str(tmp_path / ".codex/skills/cheapy/SKILL.md")},
            "agents_hook": {"status": "updated", "path": str(tmp_path / "AGENTS.md")},
            "claude_instructions": {"status": "not_applicable", "path": None},
            "claude_hook": {"status": "not_applicable", "path": None},
            "manual_steps": [],
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cheapy.cli.install_mcp", fake_install)

    result = runner.invoke(app, ["mcp", "install", "--client", "codex"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["client"] == "codex"
    assert payload["method"] == "official_cli"


def test_mcp_install_reports_installer_error(monkeypatch, tmp_path) -> None:
    from cheapy.mcp_installer import InstallerError

    def fake_install(client, *, project_root=None, home=None):
        raise InstallerError(
            code="MISSING_EXECUTABLE",
            message="cheapy executable was not found on PATH.",
            suggestion="Install Cheapy with the cheapy-flights package.",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cheapy.cli.install_mcp", fake_install)

    result = runner.invoke(app, ["mcp", "install", "--client", "codex"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "MISSING_EXECUTABLE",
        "message": "cheapy executable was not found on PATH.",
        "suggestion": "Install Cheapy with the cheapy-flights package.",
    }


def test_mcp_install_rejects_invalid_client() -> None:
    result = runner.invoke(app, ["mcp", "install", "--client", "bogus"])

    assert result.exit_code == 2
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["error"] is True
    assert error["code"] == "USAGE_ERROR"
    assert "Invalid value" in error["message"]
```

- [ ] **Step 2: Add explicit MCP protocol regression**

Append to `tests/test_mcp.py`:

```python
def test_python_module_mcp_entrypoint_still_lists_tools_after_cli_nesting() -> None:
    async def action(session: ClientSession) -> list[str]:
        response = await session.list_tools()
        return [tool.name for tool in response.tools]

    assert asyncio.run(_with_mcp_session(action)) == ["search_cheapest_flights"]
```

- [ ] **Step 3: Run failing CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_mcp_install_codex_prints_json tests/test_cli.py::test_mcp_install_reports_installer_error tests/test_cli.py::test_mcp_install_rejects_invalid_client -v
```

Expected: FAIL because `mcp` is still a plain Typer command and has no `install` subcommand.

- [ ] **Step 4: Wire CLI group**

Modify imports near the top of `cheapy/cli.py`:

```python
from pathlib import Path
```

Add imports after existing Cheapy imports:

```python
from cheapy.mcp_installer import InstallerClient, InstallerError, install_mcp
```

Replace the current `@app.command() def mcp()` block with this group:

```python
mcp_app = typer.Typer(
    help="Run or install the Cheapy MCP server.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(mcp_app, name="mcp")


@mcp_app.callback()
def mcp_main(ctx: typer.Context) -> None:
    """Run the stdio MCP server when no MCP subcommand is provided."""
    if ctx.invoked_subcommand is None:
        run_stdio_server()


@mcp_app.command("install")
def mcp_install(
    client: InstallerClient = typer.Option(
        ...,
        "--client",
        case_sensitive=False,
        help="MCP client to configure.",
    ),
) -> None:
    """Install Cheapy into a supported MCP client."""
    try:
        report = install_mcp(client, project_root=Path.cwd())
    except InstallerError as exc:
        _json_echo(exc.payload(), err=True)
        raise typer.Exit(code=exc.exit_code)

    _json_echo(report)
```

Keep the existing `providers` Typer registration exactly once. Add the new `mcp_app` registration near the current MCP command replacement and do not duplicate `providers`.

- [ ] **Step 5: Run CLI and MCP tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_mcp.py -v
```

Expected: PASS.

- [ ] **Step 6: Run installer/config/hooks focused tests**

Run:

```bash
uv run pytest tests/test_mcp_installer.py tests/test_client_configs.py tests/test_agent_hooks.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit CLI integration**

Run:

```bash
git add cheapy/cli.py tests/test_cli.py tests/test_mcp.py
git commit -m "feat: wire mcp installer cli" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 6: Final Verification And Scope Guard

**Files:**

- Verify only unless failures require fixes in files from previous tasks.

- [ ] **Step 1: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: all tests PASS.

- [ ] **Step 2: Verify package command still works**

Run:

```bash
uv run cheapy --version
```

Expected:

```text
0.1.0
```

- [ ] **Step 3: Verify no out-of-scope features were added**

Run:

```bash
rg -n "def search\\(|@app\\.command\\(\"search\"|price_history|get_price_history|http" cheapy tests
```

Expected: no matches that add CLI search, price history, or HTTP MCP transport.

- [ ] **Step 4: Verify installer docs and instructions mention one-way only**

Run:

```bash
rg -n "round-trip search is deferred|do not pass `return_date`|search_cheapest_flights" cheapy tests docs/superpowers/specs/2026-05-11-cheapy-gate-6-mcp-installer-design.md
```

Expected: matches in `cheapy/agent_hooks.py`, `tests/test_agent_hooks.py`, and the Gate 6 spec.

- [ ] **Step 5: Review git status**

Run:

```bash
git status --short
```

Expected: only intended files are modified or untracked. Do not stage unrelated untracked docs.

- [ ] **Step 6: Commit verification fixes when Step 5 shows plan-owned changes**

If Step 5 shows modifications in plan-owned files after verification fixes, commit only those fixes:

```bash
git add cheapy tests pyproject.toml uv.lock
git commit -m "test: verify mcp installer behavior" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds when verification fixes changed plan-owned files. If `git status --short` is clean except unrelated untracked docs, do not run this commit command.

## Notes For Implementers

- Keep `cheapy mcp` stdout protocol-clean. No installer text, Typer help, logs, or diagnostics may appear on stdout for the stdio server path.
- Do not write full copies of user MCP client configs. The direct-edit fallback creates redacted rollback artifacts only.
- Official client CLI success does not create a Cheapy-managed rollback artifact.
- `--client codex` must not create Claude instruction files or hooks.
- `--client claude` must not create Codex skill files or hooks.
- Claude CLI is not installed in the current environment; tests must mock it.
- Codex CLI exists in the current environment, but tests must still mock it so the suite is deterministic.
