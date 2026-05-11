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


def _assert_gate_6_instruction_text(text: str) -> None:
    for phrase in (
        "clarify ambiguous airports",
        "origin, destination, and departure date",
        "Contract V1 passenger defaults",
        "ambiguous non-default passenger counts",
        "exact one-way MVP",
        "expanded, flexible, nearby-airport, split-ticket, and round-trip search is deferred",
        "do not pass return_date",
        "Do not ask the user to choose providers",
        "mixed currency",
    ):
        assert phrase in text


def _expected_codex_agents_manual_step(path: Path) -> str:
    return (
        f"Manually add or replace the Cheapy managed block in {path}:\n"
        f"{CODEX_BEGIN}\n"
        "## Cheapy MCP Flight Search\n\n"
        "Before using Cheapy MCP, use the project skill at "
        "`.codex/skills/cheapy/SKILL.md`.\n"
        f"{CODEX_END}"
    )


def _expected_claude_hook_manual_step(path: Path) -> str:
    return (
        f"Manually add or replace the Cheapy managed block in {path}:\n"
        f"{CLAUDE_BEGIN}\n"
        "## Cheapy MCP Flight Search\n\n"
        "Before using Cheapy MCP, follow `.cheapy/claude-instructions.md`.\n"
        f"{CLAUDE_END}"
    )


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
    _assert_gate_6_instruction_text(skill_text)

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
    _assert_gate_6_instruction_text(instruction_text)

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


def test_codex_hook_out_of_tree_symlink_returns_manual_required(
    tmp_path: Path,
) -> None:
    outside_path = tmp_path.parent / f"{tmp_path.name}-outside-agents.md"
    outside_path.write_text("outside content\n", encoding="utf-8")
    agents_path = tmp_path / "AGENTS.md"
    agents_path.symlink_to(outside_path)

    try:
        report = install_agent_hooks("codex", tmp_path)

        assert agents_path.is_symlink()
        assert outside_path.read_text(encoding="utf-8") == "outside content\n"
        assert report["agents_hook"] == {
            "status": "manual_required",
            "path": str(agents_path),
        }
        assert report["manual_steps"] == [_expected_codex_agents_manual_step(agents_path)]
    finally:
        outside_path.unlink(missing_ok=True)


def test_claude_hook_broken_symlink_returns_manual_required(tmp_path: Path) -> None:
    claude_path = tmp_path / "CLAUDE.md"
    claude_path.symlink_to("missing-target.md")

    report = install_agent_hooks("claude", tmp_path)

    assert claude_path.is_symlink()
    assert not (tmp_path / "missing-target.md").exists()
    assert report["claude_hook"] == {
        "status": "manual_required",
        "path": str(claude_path),
    }
    assert report["manual_steps"] == [_expected_claude_hook_manual_step(claude_path)]


def test_codex_skill_parent_symlink_outside_returns_manual_required(
    tmp_path: Path,
) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-outside-codex"
    outside_dir.mkdir()
    codex_dir = tmp_path / ".codex"
    codex_dir.symlink_to(outside_dir, target_is_directory=True)

    try:
        report = install_agent_hooks("codex", tmp_path)

        outside_files = list(outside_dir.rglob("*"))
        assert outside_files == []
        assert report["codex_skill"]["status"] == "manual_required"  # type: ignore[index]
        assert report["codex_skill"]["path"] == str(  # type: ignore[index]
            tmp_path / ".codex" / "skills" / "cheapy" / "SKILL.md"
        )
        assert report["manual_steps"]
    finally:
        outside_dir.rmdir()


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


def test_duplicate_managed_blocks_return_manual_required_and_preserve_file(
    tmp_path: Path,
) -> None:
    agents_path = tmp_path / "AGENTS.md"
    original_text = "\n".join(
        [
            "# Existing",
            CODEX_BEGIN,
            "first managed content",
            CODEX_END,
            "user text between blocks",
            CODEX_BEGIN,
            "second managed content",
            CODEX_END,
            "after content",
            "",
        ]
    )
    agents_path.write_text(original_text, encoding="utf-8")

    report = install_agent_hooks("codex", tmp_path)

    assert agents_path.read_text(encoding="utf-8") == original_text
    assert report["agents_hook"] == {
        "status": "manual_required",
        "path": str(agents_path),
    }
    assert report["manual_steps"] == [_expected_codex_agents_manual_step(agents_path)]


def test_managed_block_replacement_preserves_outside_whitespace_exactly(
    tmp_path: Path,
) -> None:
    agents_path = tmp_path / "AGENTS.md"
    before = "# Existing\n  before content  \n\n"
    after = "\n\n\t after content  \n"
    agents_path.write_text(
        f"{before}{CODEX_BEGIN}\nold managed content\n{CODEX_END}{after}",
        encoding="utf-8",
    )

    install_agent_hooks("codex", tmp_path)

    agents_text = agents_path.read_text(encoding="utf-8")
    block_start = agents_text.index(CODEX_BEGIN)
    block_end = agents_text.index(CODEX_END) + len(CODEX_END)
    assert agents_text[:block_start] == before
    assert agents_text[block_end:] == after


def test_missing_agent_file_is_created_with_managed_block(tmp_path: Path) -> None:
    report = install_agent_hooks("claude", tmp_path)

    claude_path = tmp_path / "CLAUDE.md"
    assert report["claude_hook"]["status"] == "updated"  # type: ignore[index]
    assert claude_path.exists()
    assert CLAUDE_BEGIN in claude_path.read_text(encoding="utf-8")


def test_codex_agents_hook_write_failure_returns_manual_required(
    tmp_path: Path,
) -> None:
    agents_path = tmp_path / "AGENTS.md"
    agents_path.mkdir()

    report = install_agent_hooks("codex", tmp_path)

    assert report["codex_skill"]["status"] == "updated"  # type: ignore[index]
    assert report["agents_hook"] == {
        "status": "manual_required",
        "path": str(agents_path),
    }
    _assert_not_applicable(report, "claude_instructions")
    _assert_not_applicable(report, "claude_hook")
    assert report["manual_steps"] == [_expected_codex_agents_manual_step(agents_path)]


def test_existing_codex_skill_preserves_custom_text_and_adds_managed_guidance(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / ".codex" / "skills" / "cheapy" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    custom_text = "\n".join(
        [
            "---",
            "name: custom-cheapy",
            "---",
            "",
            "# Custom Cheapy Notes",
            "",
            "| User text | IATA |",
            "| --- | --- |",
            "| custom airport | CXR |",
            "",
        ]
    )
    skill_path.write_text(custom_text, encoding="utf-8")

    report = install_agent_hooks("codex", tmp_path)

    skill_text = skill_path.read_text(encoding="utf-8")
    assert report["codex_skill"]["status"] == "updated"  # type: ignore[index]
    assert "| custom airport | CXR |" in skill_text
    assert "custom-cheapy" in skill_text
    assert CODEX_BEGIN in skill_text
    assert CODEX_END in skill_text
    assert "search_cheapest_flights" in skill_text


def test_claude_hook_write_failure_returns_manual_required(tmp_path: Path) -> None:
    claude_path = tmp_path / "CLAUDE.md"
    claude_path.mkdir()

    report = install_agent_hooks("claude", tmp_path)

    _assert_not_applicable(report, "codex_skill")
    _assert_not_applicable(report, "agents_hook")
    assert report["claude_instructions"]["status"] == "updated"  # type: ignore[index]
    assert report["claude_hook"] == {
        "status": "manual_required",
        "path": str(claude_path),
    }
    assert report["manual_steps"] == [_expected_claude_hook_manual_step(claude_path)]
