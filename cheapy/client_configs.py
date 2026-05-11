"""Safe direct edits for supported MCP client config files."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import tomlkit

from cheapy.mcp_installer import InstallerClient, InstallerError, SERVER_NAME


REDACTED = "[REDACTED]"
CONFIG_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|api(?:[_-]?key|key)|authorization|bearer|password)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConfigEditResult:
    """Result of a direct client config edit."""

    config_path: Path
    rollback_path: Path


def edit_client_config(
    client: InstallerClient,
    entry: Mapping[str, Any],
    *,
    project_root: Path,
    home: Path,
) -> ConfigEditResult:
    """Create or update the Cheapy MCP entry for a supported client."""
    selected_client = InstallerClient(client)
    if selected_client is InstallerClient.CODEX:
        return _edit_codex_config(entry, home=home)
    if selected_client is InstallerClient.CLAUDE:
        return _edit_claude_config(entry, project_root=project_root, home=home)
    raise ValueError(f"Unsupported installer client: {client}")


def redact_secret_values(value: Any) -> Any:
    """Return a JSON-safe copy with likely secret values redacted."""
    return _redact_secret_values(value)


def _edit_codex_config(
    entry: Mapping[str, Any],
    *,
    home: Path,
) -> ConfigEditResult:
    codex_dir = home / ".codex"
    config_path = codex_dir / "config.toml"
    _ensure_safe_parent(codex_dir, home)
    _ensure_private_dir(codex_dir, chmod_existing=False)

    if config_path.exists():
        original_text = config_path.read_text(encoding="utf-8")
        try:
            document = tomlkit.parse(original_text)
        except Exception as exc:
            raise _parse_failed_error(
                config_path,
                f"Could not parse Codex TOML config: {exc}",
            ) from exc
    else:
        document = tomlkit.document()

    try:
        servers = document.get("mcp_servers")
        if servers is None:
            servers = tomlkit.table()
            document["mcp_servers"] = servers

        previous_entry_exists = SERVER_NAME in servers
        previous_entry = (
            _to_plain_data(servers[SERVER_NAME]) if previous_entry_exists else None
        )

        servers[SERVER_NAME] = _codex_entry_table(entry)
        new_entry = _to_plain_data(servers[SERVER_NAME])
        next_text = tomlkit.dumps(document)
    except Exception as exc:
        raise _parse_failed_error(
            config_path,
            f"Could not update Codex MCP server table: {exc}",
        ) from exc

    rollback_path = _write_rollback_artifact(
        client=InstallerClient.CODEX,
        config_path=config_path,
        home=home,
        previous_entry_exists=previous_entry_exists,
        previous_entry=previous_entry,
        new_entry=new_entry,
    )
    _atomic_write_text(config_path, next_text, mode=CONFIG_FILE_MODE)
    return ConfigEditResult(config_path=config_path, rollback_path=rollback_path)


def _edit_claude_config(
    entry: Mapping[str, Any],
    *,
    project_root: Path,
    home: Path,
) -> ConfigEditResult:
    config_path = home / ".claude.json"
    _ensure_safe_parent(config_path.parent, home)
    _ensure_private_dir(config_path.parent, chmod_existing=False)

    if config_path.exists():
        original_text = config_path.read_text(encoding="utf-8")
        try:
            document = json.loads(original_text)
        except json.JSONDecodeError as exc:
            raise _parse_failed_error(
                config_path,
                f"Could not parse Claude JSON config: {exc}",
            ) from exc
    else:
        document = {}

    if not isinstance(document, dict):
        raise _parse_failed_error(config_path, "Claude config root must be an object.")

    projects = document.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise _parse_failed_error(config_path, "Claude projects must be an object.")

    project_config = projects.setdefault(str(project_root), {})
    if not isinstance(project_config, dict):
        raise _parse_failed_error(
            config_path,
            f"Claude project config for {project_root} must be an object.",
        )

    servers = project_config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise _parse_failed_error(config_path, "Claude mcpServers must be an object.")

    previous_entry_exists = SERVER_NAME in servers
    previous_entry = (
        _to_plain_data(servers[SERVER_NAME]) if previous_entry_exists else None
    )
    servers[SERVER_NAME] = _claude_entry(entry)
    new_entry = _to_plain_data(servers[SERVER_NAME])
    next_text = json.dumps(document, indent=2) + "\n"

    rollback_path = _write_rollback_artifact(
        client=InstallerClient.CLAUDE,
        config_path=config_path,
        home=home,
        previous_entry_exists=previous_entry_exists,
        previous_entry=previous_entry,
        new_entry=new_entry,
    )
    _atomic_write_text(config_path, next_text, mode=CONFIG_FILE_MODE)
    return ConfigEditResult(config_path=config_path, rollback_path=rollback_path)


def _codex_entry_table(entry: Mapping[str, Any]) -> Any:
    table = tomlkit.table()
    table["command"] = str(entry["command"])
    table["args"] = [str(arg) for arg in entry.get("args", [])]
    return table


def _claude_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": str(entry["command"]),
        "args": [str(arg) for arg in entry.get("args", [])],
        "env": {},
    }


def _write_rollback_artifact(
    *,
    client: InstallerClient,
    config_path: Path,
    home: Path,
    previous_entry_exists: bool,
    previous_entry: Any,
    new_entry: Any,
) -> Path:
    rollback_dir = home / ".cheapy" / "client-config-rollbacks"
    _ensure_safe_parent(rollback_dir, home)
    _ensure_private_dir(rollback_dir, chmod_existing=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    rollback_path = rollback_dir / f"{client.value}-{SERVER_NAME}-{timestamp}.json"
    payload = {
        "client": client.value,
        "config_path": str(config_path),
        "server_name": SERVER_NAME,
        "previous_entry_exists": previous_entry_exists,
        "previous_entry": redact_secret_values(previous_entry),
        "new_entry": redact_secret_values(new_entry),
        "manual_rollback": (
            f"Edit {config_path} and restore only the {SERVER_NAME!r} server entry "
            "from previous_entry, or remove it if previous_entry_exists is false."
        ),
    }
    _atomic_write_text(
        rollback_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        mode=CONFIG_FILE_MODE,
    )
    return rollback_path


def _redact_secret_values(value: Any, *, redact_all_scalars: bool = False) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if redact_all_scalars or _is_secret_key(key_text):
                redacted[key] = _redact_all_scalar_values(item)
            elif key_text.lower() in {"env", "headers"}:
                redacted[key] = _redact_secret_values(item, redact_all_scalars=True)
            else:
                redacted[key] = _redact_secret_values(item)
        return redacted

    if isinstance(value, list):
        redacted_list: list[Any] = []
        redact_next = False
        for item in value:
            if redact_next:
                redacted_list.append(_redact_all_scalar_values(item))
                redact_next = False
                continue

            if isinstance(item, str):
                flag, separator, _ = item.partition("=")
                if item.startswith("-") and _is_secret_key(flag):
                    if separator:
                        redacted_list.append(f"{flag}={REDACTED}")
                    else:
                        redacted_list.append(item)
                        redact_next = True
                    continue
                redacted_list.append(_redact_string(item))
                continue

            redacted_list.append(_redact_secret_values(item))
        return redacted_list

    if redact_all_scalars:
        return REDACTED
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_all_scalar_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _redact_all_scalar_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_all_scalar_values(item) for item in value]
    return REDACTED


def _is_secret_key(key: str) -> bool:
    return SECRET_KEY_PATTERN.search(key) is not None


def _redact_string(value: str) -> str:
    redacted = re.sub(
        r"(?i)(bearer\s+)[^\s]+",
        rf"\1{REDACTED}",
        value,
    )
    return re.sub(
        r"(?i)((?:token|secret|api[_-]?key|password)\s*[:=]\s*)[^\s]+",
        rf"\1{REDACTED}",
        redacted,
    )


def _to_plain_data(value: Any) -> Any:
    if hasattr(value, "unwrap"):
        value = value.unwrap()
    if isinstance(value, Mapping):
        return {str(key): _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    return value


def _ensure_safe_parent(parent: Path, home: Path) -> None:
    try:
        home_path = home.expanduser().resolve(strict=True)
        parent_path = parent.expanduser().resolve(strict=False)
    except OSError as exc:
        raise _client_config_unavailable_error(
            f"Could not resolve client config path safely: {exc}"
        ) from exc

    if not parent_path.is_relative_to(home_path):
        raise _client_config_unavailable_error(
            f"Refusing to edit config outside home directory: {parent}"
        )
    if parent.exists() and not parent.is_dir():
        raise _client_config_unavailable_error(
            f"Refusing to edit config because parent is not a directory: {parent}"
        )


def _ensure_private_dir(path: Path, *, chmod_existing: bool) -> None:
    existed = path.exists()
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    if chmod_existing or not existed:
        _chmod(path, PRIVATE_DIR_MODE)


def _atomic_write_text(path: Path, text: str, *, mode: int) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        _chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        _chmod(path, mode)
    except BaseException:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        return


def _parse_failed_error(config_path: Path, message: str) -> InstallerError:
    return InstallerError(
        code="CONFIG_PARSE_FAILED",
        message=message,
        suggestion=f"Fix {config_path} or run the official MCP add command manually.",
    )


def _client_config_unavailable_error(message: str) -> InstallerError:
    return InstallerError(
        code="CLIENT_CONFIG_UNAVAILABLE",
        message=message,
        suggestion="Run the official MCP add command manually.",
    )
