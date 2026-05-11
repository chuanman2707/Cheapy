"""Direct MCP client config fallback tests."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import tomlkit

from cheapy.client_configs import edit_client_config, redact_secret_values
from cheapy.mcp_installer import InstallerClient, InstallerError


ENTRY = {"command": "/opt/bin/cheapy", "args": ["mcp"]}


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_codex_missing_config_creates_minimal_config_and_rollback(
    tmp_path: Path,
) -> None:
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
    assert parsed["mcp_servers"]["cheapy"]["args"] == ["mcp"]
    assert result.rollback_path.exists()
    assert _mode(config_path) == 0o600
    assert _mode(result.rollback_path) == 0o600

    rollback = json.loads(result.rollback_path.read_text(encoding="utf-8"))
    assert rollback["client"] == "codex"
    assert rollback["config_path"] == str(config_path)
    assert rollback["server_name"] == "cheapy"
    assert rollback["previous_entry_exists"] is False
    assert rollback["previous_entry"] is None
    assert rollback["new_entry"] == ENTRY
    assert rollback["manual_rollback"]


def test_codex_preserves_comments_unrelated_tables_and_servers(
    tmp_path: Path,
) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "# keep top comment",
                'model = "gpt-5"',
                "",
                "[profile.default]",
                'approval_policy = "never"',
                "",
                "[mcp_servers.other]",
                'command = "/bin/other"',
                'args = ["serve"]',
                "",
            ]
        ),
        encoding="utf-8",
    )

    edit_client_config(
        InstallerClient.CODEX,
        ENTRY,
        project_root=tmp_path / "project",
        home=tmp_path,
    )

    text = config_path.read_text(encoding="utf-8")
    assert "# keep top comment" in text
    assert "[profile.default]" in text
    parsed = tomlkit.parse(text)
    assert parsed["model"] == "gpt-5"
    assert parsed["profile"]["default"]["approval_policy"] == "never"
    assert parsed["mcp_servers"]["other"]["command"] == "/bin/other"
    assert parsed["mcp_servers"]["other"]["args"] == ["serve"]
    assert parsed["mcp_servers"]["cheapy"]["command"] == "/opt/bin/cheapy"


def test_codex_stale_entry_updates_idempotently_without_duplicate_table(
    tmp_path: Path,
) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[mcp_servers.cheapy]",
                'command = "/old/bin/cheapy"',
                'args = ["old"]',
                "",
            ]
        ),
        encoding="utf-8",
    )

    edit_client_config(
        InstallerClient.CODEX,
        ENTRY,
        project_root=tmp_path / "project",
        home=tmp_path,
    )
    edit_client_config(
        InstallerClient.CODEX,
        ENTRY,
        project_root=tmp_path / "project",
        home=tmp_path,
    )

    text = config_path.read_text(encoding="utf-8")
    parsed = tomlkit.parse(text)
    assert parsed["mcp_servers"]["cheapy"]["command"] == "/opt/bin/cheapy"
    assert parsed["mcp_servers"]["cheapy"]["args"] == ["mcp"]
    assert text.count("[mcp_servers.cheapy]") == 1


def test_claude_missing_config_creates_project_scoped_stdio_entry(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    result = edit_client_config(
        InstallerClient.CLAUDE,
        ENTRY,
        project_root=project_root,
        home=tmp_path,
    )

    config_path = tmp_path / ".claude.json"
    assert result.config_path == config_path
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["projects"][str(project_root)]["mcpServers"]["cheapy"] == {
        "type": "stdio",
        "command": "/opt/bin/cheapy",
        "args": ["mcp"],
        "env": {},
    }
    assert _mode(config_path) == 0o600


def test_claude_preserves_unrelated_projects_servers_and_secret_env_values(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    other_project = tmp_path / "other-project"
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "projects": {
                    str(other_project): {
                        "mcpServers": {
                            "other": {
                                "type": "stdio",
                                "command": "/bin/other",
                                "args": [],
                                "env": {"API_KEY": "other-secret"},
                            }
                        }
                    },
                    str(project_root): {
                        "mcpServers": {
                            "existing": {
                                "type": "stdio",
                                "command": "/bin/existing",
                                "args": ["serve"],
                                "env": {"TOKEN": "existing-secret"},
                            }
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    edit_client_config(
        InstallerClient.CLAUDE,
        ENTRY,
        project_root=project_root,
        home=tmp_path,
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["projects"][str(other_project)]["mcpServers"]["other"]["env"] == {
        "API_KEY": "other-secret",
    }
    assert payload["projects"][str(project_root)]["mcpServers"]["existing"]["env"] == {
        "TOKEN": "existing-secret",
    }
    assert payload["projects"][str(project_root)]["mcpServers"]["cheapy"][
        "command"
    ] == "/opt/bin/cheapy"


def test_rollback_artifact_redacts_previous_cheapy_without_unrelated_secrets(
    tmp_path: Path,
) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[mcp_servers.cheapy]",
                'command = "/old/bin/cheapy"',
                'args = ["mcp", "--api-key", "old-arg-secret"]',
                "",
                "[mcp_servers.cheapy.env]",
                'TOKEN = "old-token-secret"',
                "",
                "[mcp_servers.other]",
                'command = "/bin/other"',
                "",
                "[mcp_servers.other.env]",
                'API_KEY = "unrelated-server-secret"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = edit_client_config(
        InstallerClient.CODEX,
        ENTRY,
        project_root=tmp_path / "project",
        home=tmp_path,
    )

    artifact = result.rollback_path.read_text(encoding="utf-8")
    payload = json.loads(artifact)
    assert payload["previous_entry_exists"] is True
    assert "old-arg-secret" not in artifact
    assert "old-token-secret" not in artifact
    assert "unrelated-server-secret" not in artifact
    assert payload["previous_entry"]["args"] == ["mcp", "--api-key", "[REDACTED]"]
    assert payload["previous_entry"]["env"]["TOKEN"] == "[REDACTED]"


def test_redact_secret_values_handles_nested_dicts_and_secret_args() -> None:
    redacted = redact_secret_values(
        {
            "headers": {"Authorization": "Bearer header-secret"},
            "nested": {
                "api_key": "nested-secret",
                "safe": "keep",
                "env": {"SAFE_NAME": "env-secret"},
            },
            "args": ["mcp", "--api-key", "arg-secret", "--region", "SGN"],
        }
    )

    assert redacted == {
        "headers": {"Authorization": "[REDACTED]"},
        "nested": {
            "api_key": "[REDACTED]",
            "safe": "keep",
            "env": {"SAFE_NAME": "[REDACTED]"},
        },
        "args": ["mcp", "--api-key", "[REDACTED]", "--region", "SGN"],
    }


def test_invalid_toml_raises_parse_failed_and_leaves_original_file(
    tmp_path: Path,
) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    original = "[mcp_servers\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(InstallerError) as exc_info:
        edit_client_config(
            InstallerClient.CODEX,
            ENTRY,
            project_root=tmp_path / "project",
            home=tmp_path,
        )

    assert exc_info.value.code == "CONFIG_PARSE_FAILED"
    assert config_path.read_text(encoding="utf-8") == original


def test_unsafe_parent_path_raises_client_config_unavailable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (home / ".codex").symlink_to(outside, target_is_directory=True)

    with pytest.raises(InstallerError) as exc_info:
        edit_client_config(
            InstallerClient.CODEX,
            ENTRY,
            project_root=home / "project",
            home=home,
        )

    assert exc_info.value.code == "CLIENT_CONFIG_UNAVAILABLE"
