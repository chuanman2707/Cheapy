"""Agent hook installer tests."""

from __future__ import annotations

from pathlib import Path

from cheapy.agent_hooks import install_agent_hooks


CODEX_BEGIN = "<!-- BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CODEX_END = "<!-- END CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CLAUDE_BEGIN = "<!-- BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"
CLAUDE_END = "<!-- END CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"


def _assert_not_applicable(report: dict[str, object], key: str) -> None:
    assert report[key] == {"status": "not_applicable"}


def test_codex_hooks_create_skill_and_agents_hook_only(tmp_path: Path) -> None:
    report = install_agent_hooks("codex", tmp_path)

    skill_path = tmp_path / ".codex" / "skills" / "cheapy" / "SKILL.md"
    agents_path = tmp_path / "AGENTS.md"
    claude_instructions_path = tmp_path / ".cheapy" / "claude-instructions.md"
    claude_path = tmp_path / "CLAUDE.md"

    assert report["codex_skill"]["status"] == "updated"  # type: ignore[index]
    assert report["agents_hook"]["status"] == "updated"  # type: ignore[index]
    _assert_not_applicable(report, "claude_instructions")
    _assert_not_applicable(report, "claude_hook")
    assert report["manual_steps"] == []

    assert skill_path.exists()
    assert agents_path.exists()
    assert not claude_instructions_path.exists()
    assert not claude_path.exists()

    skill_text = skill_path.read_text(encoding="utf-8")
    assert "search_cheapest_flights" in skill_text
    assert "round-trip search is deferred" in skill_text
    assert "do not pass return_date" in skill_text

    agents_text = agents_path.read_text(encoding="utf-8")
    assert CODEX_BEGIN in agents_text
    assert CODEX_END in agents_text
    assert CLAUDE_BEGIN not in agents_text


def test_codex_hooks_are_idempotent(tmp_path: Path) -> None:
    first_report = install_agent_hooks("codex", tmp_path)
    second_report = install_agent_hooks("codex", tmp_path)

    agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert first_report["codex_skill"]["status"] == "updated"  # type: ignore[index]
    assert first_report["agents_hook"]["status"] == "updated"  # type: ignore[index]
    assert second_report["codex_skill"]["status"] == "unchanged"  # type: ignore[index]
    assert second_report["agents_hook"]["status"] == "unchanged"  # type: ignore[index]
    assert agents_text.count(CODEX_BEGIN) == 1
    assert agents_text.count(CODEX_END) == 1


def test_claude_hooks_create_instructions_and_claude_hook_only(
    tmp_path: Path,
) -> None:
    report = install_agent_hooks("claude", tmp_path)

    skill_path = tmp_path / ".codex" / "skills" / "cheapy" / "SKILL.md"
    agents_path = tmp_path / "AGENTS.md"
    claude_instructions_path = tmp_path / ".cheapy" / "claude-instructions.md"
    claude_path = tmp_path / "CLAUDE.md"

    _assert_not_applicable(report, "codex_skill")
    _assert_not_applicable(report, "agents_hook")
    assert report["claude_instructions"]["status"] == "updated"  # type: ignore[index]
    assert report["claude_hook"]["status"] == "updated"  # type: ignore[index]
    assert report["manual_steps"] == []

    assert not skill_path.exists()
    assert not agents_path.exists()
    assert claude_instructions_path.exists()
    assert claude_path.exists()

    instruction_text = claude_instructions_path.read_text(encoding="utf-8")
    assert "search_cheapest_flights" in instruction_text
    assert "round-trip search is deferred" in instruction_text
    assert "do not pass return_date" in instruction_text

    claude_text = claude_path.read_text(encoding="utf-8")
    assert CLAUDE_BEGIN in claude_text
    assert CLAUDE_END in claude_text
    assert CODEX_BEGIN not in claude_text


def test_claude_hook_preserves_claude_md_symlink(tmp_path: Path) -> None:
    agents_path = tmp_path / "AGENTS.md"
    claude_path = tmp_path / "CLAUDE.md"
    agents_path.write_text("# Agent Instructions\n", encoding="utf-8")
    claude_path.symlink_to("AGENTS.md")

    report = install_agent_hooks("claude", tmp_path)

    assert report["claude_hook"]["status"] == "updated"  # type: ignore[index]
    assert claude_path.is_symlink()
    assert claude_path.readlink() == Path("AGENTS.md")
    target_text = agents_path.read_text(encoding="utf-8")
    assert CLAUDE_BEGIN in target_text
    assert CLAUDE_END in target_text


def test_managed_block_replacement_preserves_unmanaged_content(
    tmp_path: Path,
) -> None:
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text(
        "\n".join(
            [
                "# Existing",
                "",
                "before content",
                CODEX_BEGIN,
                "old managed content",
                CODEX_END,
                "after content",
                "",
            ]
        ),
        encoding="utf-8",
    )

    install_agent_hooks("codex", tmp_path)

    agents_text = agents_path.read_text(encoding="utf-8")
    assert "before content" in agents_text
    assert "after content" in agents_text
    assert "old managed content" not in agents_text
    assert agents_text.count(CODEX_BEGIN) == 1
    assert agents_text.count(CODEX_END) == 1


def test_missing_agent_file_is_created_with_managed_block(tmp_path: Path) -> None:
    report = install_agent_hooks("claude", tmp_path)

    claude_path = tmp_path / "CLAUDE.md"
    assert report["claude_hook"]["status"] == "updated"  # type: ignore[index]
    assert claude_path.exists()
    assert CLAUDE_BEGIN in claude_path.read_text(encoding="utf-8")
