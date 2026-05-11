"""Agent instruction hooks for MCP installer setup."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


CODEX_CLIENT = "codex"
CLAUDE_CLIENT = "claude"

CODEX_BEGIN = "<!-- BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CODEX_END = "<!-- END CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CLAUDE_BEGIN = "<!-- BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"
CLAUDE_END = "<!-- END CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"

CODEX_SKILL_TEXT = """---
name: cheapy-flight-search
description: Use when searching one-way flights with Cheapy MCP.
---

# Cheapy Flight Search

Use Cheapy only for exact one-way MVP flight searches.

- Call only `search_cheapest_flights`.
- Pass `schema_version="1"`.
- Normalize origin and destination to 3-letter IATA codes before calling the tool.
- Normalize dates to ISO `YYYY-MM-DD`.
- Use Contract V1 passenger defaults unless the user explicitly says otherwise: `adults=1`, `children=0`, `infants_on_lap=0`, `infants_in_seat=0`.
- Expanded, flexible, nearby-airport, split-ticket, and round-trip search is deferred; do not pass return_date.
- Do not ask the user to choose providers.
"""

CLAUDE_INSTRUCTIONS_TEXT = """# Cheapy MCP Flight Search

Use Cheapy only for exact one-way MVP flight searches.

- Call only `search_cheapest_flights`.
- Pass `schema_version="1"`.
- Normalize origin and destination to 3-letter IATA codes before calling the tool.
- Normalize dates to ISO `YYYY-MM-DD`.
- Use Contract V1 passenger defaults unless the user explicitly says otherwise: `adults=1`, `children=0`, `infants_on_lap=0`, `infants_in_seat=0`.
- Expanded, flexible, nearby-airport, split-ticket, and round-trip search is deferred; do not pass return_date.
- Do not ask the user to choose providers.
"""

CODEX_HOOK_BODY = """## Cheapy MCP Flight Search

Before using Cheapy MCP, use the project skill at `.codex/skills/cheapy/SKILL.md`.
"""

CLAUDE_HOOK_BODY = """## Cheapy MCP Flight Search

Before using Cheapy MCP, follow `.cheapy/claude-instructions.md`.
"""


def install_agent_hooks(client: object, project_root: Path) -> dict[str, Any]:
    """Install selected-client agent instruction hooks for Cheapy MCP."""
    selected_client = _client_value(client)
    root = Path(project_root)
    if selected_client == CODEX_CLIENT:
        agents_path = root / "AGENTS.md"
        return {
            "codex_skill": _write_report(
                root / ".codex" / "skills" / "cheapy" / "SKILL.md",
                CODEX_SKILL_TEXT,
            ),
            "agents_hook": _write_report(
                agents_path,
                _managed_text(agents_path, CODEX_BEGIN, CODEX_END, CODEX_HOOK_BODY),
            ),
            "claude_instructions": _not_applicable(),
            "claude_hook": _not_applicable(),
            "manual_steps": [],
        }

    if selected_client == CLAUDE_CLIENT:
        claude_path = root / "CLAUDE.md"
        return {
            "codex_skill": _not_applicable(),
            "agents_hook": _not_applicable(),
            "claude_instructions": _write_report(
                root / ".cheapy" / "claude-instructions.md",
                CLAUDE_INSTRUCTIONS_TEXT,
            ),
            "claude_hook": _write_report(
                claude_path,
                _managed_text(
                    claude_path,
                    CLAUDE_BEGIN,
                    CLAUDE_END,
                    CLAUDE_HOOK_BODY,
                ),
            ),
            "manual_steps": [],
        }

    raise ValueError(f"Unsupported installer client: {selected_client}")


def _client_value(client: object) -> str:
    value = getattr(client, "value", client)
    return str(value)


def _not_applicable() -> dict[str, str]:
    return {"status": "not_applicable"}


def _write_report(path: Path, text: str) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    status = "updated"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        status = "unchanged"
    else:
        path.write_text(text, encoding="utf-8")
    return {"status": status, "path": str(path)}


def _managed_text(path: Path, begin: str, end: str, body: str) -> str:
    current_text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = _managed_block(begin, end, body)
    pattern = re.compile(
        rf"{re.escape(begin)}.*?{re.escape(end)}\n?",
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(current_text))
    if matches:
        first = matches[0]
        last = matches[-1]
        before = current_text[: first.start()].rstrip()
        after = current_text[last.end() :].lstrip()
        if before and after:
            return f"{before}\n\n{block}\n\n{after}"
        if before:
            return f"{before}\n\n{block}\n"
        if after:
            return f"{block}\n\n{after}"
        return f"{block}\n"

    base = current_text.rstrip()
    if base:
        return f"{base}\n\n{block}\n"
    return f"{block}\n"


def _managed_block(begin: str, end: str, body: str) -> str:
    return f"{begin}\n{body.strip()}\n{end}"
