from __future__ import annotations

from pathlib import Path


SKILL_PATH = Path(".codex/skills/cheapy/SKILL.md")


REQUIRED_ALIASES = {
    "nha trang": "CXR",
    "cam ranh": "CXR",
    "sài gòn": "SGN",
    "sai gon": "SGN",
    "tp hcm": "SGN",
    "ho chi minh": "SGN",
    "hà nội": "HAN",
    "ha noi": "HAN",
    "đà nẵng": "DAD",
    "da nang": "DAD",
    "phú quốc": "PQC",
    "phu quoc": "PQC",
}


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()

    assert lines[0] == "---"
    closing_index = lines.index("---", 1)

    entries: dict[str, str] = {}
    for line in lines[1:closing_index]:
        key, value = line.split(":", maxsplit=1)
        entries[key] = value.strip()

    return entries


def test_cheapy_skill_exists_in_project_local_path() -> None:
    assert SKILL_PATH.exists()


def test_cheapy_skill_has_valid_yaml_frontmatter() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    frontmatter = _frontmatter(text)

    assert lines[0] == "---"
    assert lines.index("---", 1) > 0
    assert frontmatter["name"] == "cheapy-flight-search"
    assert frontmatter["description"].startswith("Use when")


def test_cheapy_skill_explicitly_says_tools_accept_iata_only() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    normalized = text.lower()

    assert "only accept iata" in normalized
    assert "3-letter iata" in normalized
    assert "do not pass city names" in normalized


def test_cheapy_skill_contains_vietnamese_aliases_for_snapshot_airports() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8").lower()

    for alias, iata in REQUIRED_ALIASES.items():
        assert alias in text
        assert iata.lower() in text


def test_cheapy_skill_does_not_claim_runtime_resolves_aliases() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8").lower()

    assert "the agent is responsible" in text
    assert "cheapy runtime does not resolve vietnamese aliases" in text
    assert "cheapy runtime resolves vietnamese aliases" not in text
