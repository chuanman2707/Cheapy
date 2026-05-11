"""Agent instruction hooks for MCP installer setup."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
from typing import Any


CODEX_CLIENT = "codex"
CLAUDE_CLIENT = "claude"

CODEX_BEGIN = "<!-- BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CODEX_END = "<!-- END CHEAPY MANAGED CODEX INSTRUCTIONS -->"
CLAUDE_BEGIN = "<!-- BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"
CLAUDE_END = "<!-- END CHEAPY MANAGED CLAUDE INSTRUCTIONS -->"

CODEX_SKILL_HEADER = """---
name: cheapy-flight-search
description: Use when searching one-way flights with Cheapy MCP.
---

# Cheapy Flight Search

"""

CLAUDE_INSTRUCTIONS_HEADER = """# Cheapy MCP Flight Search

"""

INSTRUCTION_BODY = """Use Cheapy only for exact one-way MVP flight searches.

- Call only `search_cheapest_flights`.
- Pass `schema_version="1"`.
- Before calls, require origin, destination, and departure date; ask a follow-up if any are missing.
- Normalize clear origin and destination airports to 3-letter IATA codes.
- If airport meaning is unclear, clarify ambiguous airports instead of guessing.
- Normalize dates to ISO `YYYY-MM-DD`.
- Use Contract V1 passenger defaults when unspecified: `adults=1`, `children=0`, `infants_on_lap=0`, `infants_in_seat=0`.
- Ask a follow-up for ambiguous non-default passenger counts.
- expanded, flexible, nearby-airport, split-ticket, and round-trip search is deferred; do not pass return_date.
- Do not ask the user to choose providers.
- Explain mixed currency cautiously; preserve provider currency and do not overstate comparisons.
"""

CODEX_HOOK_BODY = """## Cheapy MCP Flight Search

Before using Cheapy MCP, use the project skill at `.codex/skills/cheapy/SKILL.md`.
"""

CLAUDE_HOOK_BODY = """## Cheapy MCP Flight Search

Before using Cheapy MCP, follow `.cheapy/claude-instructions.md`.
"""

STALE_LEGACY_INSTRUCTION_PATTERNS = (
    re.compile(
        r"^[^\n]*\bDecide one-way or round-trip from the user's sentence\.[^\n]*\n?",
        flags=re.IGNORECASE | re.MULTILINE,
    ),
)


class UnsafeHookTargetError(Exception):
    """Hook target is outside the project root or otherwise unsafe."""


class UnsafeManagedBlockError(Exception):
    """Managed block markers are malformed or duplicated."""


def install_agent_hooks(client: object, project_root: Path) -> dict[str, Any]:
    """Install selected-client agent instruction hooks for Cheapy MCP."""
    selected_client = _client_value(client)
    root = Path(project_root)
    manual_steps: list[str] = []
    if selected_client == CODEX_CLIENT:
        agents_path = root / "AGENTS.md"
        return {
            "codex_skill": _safe_write_managed_block(
                root / ".codex" / "skills" / "cheapy" / "SKILL.md",
                root,
                CODEX_BEGIN,
                CODEX_END,
                INSTRUCTION_BODY,
                manual_steps,
                new_file_prefix=CODEX_SKILL_HEADER,
                transform_existing=_remove_stale_legacy_instruction_text,
            ),
            "agents_hook": _safe_write_managed_block(
                agents_path,
                root,
                CODEX_BEGIN,
                CODEX_END,
                CODEX_HOOK_BODY,
                manual_steps,
            ),
            "claude_instructions": _not_applicable(),
            "claude_hook": _not_applicable(),
            "manual_steps": manual_steps,
        }

    if selected_client == CLAUDE_CLIENT:
        claude_path = root / "CLAUDE.md"
        return {
            "codex_skill": _not_applicable(),
            "agents_hook": _not_applicable(),
            "claude_instructions": _safe_write_managed_block(
                root / ".cheapy" / "claude-instructions.md",
                root,
                CLAUDE_BEGIN,
                CLAUDE_END,
                INSTRUCTION_BODY,
                manual_steps,
                new_file_prefix=CLAUDE_INSTRUCTIONS_HEADER,
                transform_existing=_remove_stale_legacy_instruction_text,
            ),
            "claude_hook": _safe_write_managed_block(
                claude_path,
                root,
                CLAUDE_BEGIN,
                CLAUDE_END,
                CLAUDE_HOOK_BODY,
                manual_steps,
            ),
            "manual_steps": manual_steps,
        }

    raise ValueError(f"Unsupported installer client: {selected_client}")


def _client_value(client: object) -> str:
    value = getattr(client, "value", client)
    return str(value)


def _not_applicable() -> dict[str, str]:
    return {"status": "not_applicable"}


def _safe_write_managed_block(
    path: Path,
    project_root: Path,
    begin: str,
    end: str,
    body: str,
    manual_steps: list[str],
    *,
    new_file_prefix: str = "",
    transform_existing: Callable[[str], str] | None = None,
) -> dict[str, str]:
    block = _managed_block(begin, end, body)
    try:
        _validate_safe_target(path, project_root)
        return _write_report(
            path,
            _managed_text(
                path,
                begin,
                end,
                block,
                new_file_prefix=new_file_prefix,
                transform_existing=transform_existing,
            ),
        )
    except (
        OSError,
        UnicodeError,
        UnsafeHookTargetError,
        UnsafeManagedBlockError,
    ):
        manual_steps.append(_manual_block_step(path, block))
        return _manual_required(path)


def _write_report(path: Path, text: str) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    status = "updated"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        status = "unchanged"
    else:
        path.write_text(text, encoding="utf-8")
    return {"status": status, "path": str(path)}


def _manual_required(path: Path) -> dict[str, str]:
    return {"status": "manual_required", "path": str(path)}


def _manual_block_step(path: Path, block: str) -> str:
    return f"Manually add or replace the Cheapy managed block in {path}:\n{block}"


def _validate_safe_target(path: Path, project_root: Path) -> None:
    root = project_root.resolve(strict=True)
    if path.is_symlink():
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise UnsafeHookTargetError(str(exc)) from exc
    else:
        target = path.resolve(strict=path.exists())

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UnsafeHookTargetError(f"{target} is outside {root}") from exc


def _managed_text(
    path: Path,
    begin: str,
    end: str,
    block: str,
    *,
    new_file_prefix: str = "",
    transform_existing: Callable[[str], str] | None = None,
) -> str:
    current_text = path.read_text(encoding="utf-8") if path.exists() else ""
    if transform_existing is not None and current_text:
        current_text = transform_existing(current_text)
    begin_count = current_text.count(begin)
    end_count = current_text.count(end)
    if begin_count != end_count or begin_count > 1:
        raise UnsafeManagedBlockError("Managed block markers are malformed.")

    pattern = re.compile(
        rf"{re.escape(begin)}.*?{re.escape(end)}",
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(current_text))
    if matches:
        if len(matches) != 1:
            raise UnsafeManagedBlockError("Managed block markers are duplicated.")
        first = matches[0]
        last = matches[-1]
        return f"{current_text[: first.start()]}{block}{current_text[last.end() :]}"

    if begin_count or end_count:
        raise UnsafeManagedBlockError("Managed block markers are malformed.")

    if current_text:
        separator = "" if current_text.endswith("\n") else "\n"
        return f"{current_text}{separator}\n{block}\n"
    return f"{new_file_prefix}{block}\n"


def _managed_block(begin: str, end: str, body: str) -> str:
    return f"{begin}\n{body.strip()}\n{end}"


def _remove_stale_legacy_instruction_text(text: str) -> str:
    updated = text
    for pattern in STALE_LEGACY_INSTRUCTION_PATTERNS:
        updated = pattern.sub("", updated)
    return updated
