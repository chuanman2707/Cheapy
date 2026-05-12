# Agent Instructions

## Package Manager
Use **uv**: `uv sync --extra dev`, `uv run pytest -v`, `uv run cheapy --version`.

## Project Skills
Before changing MCP, CLI, contracts, packaging, or tests, read the matching project-local skill under `.codex/skills/`.

## File-Scoped Commands
| Task | Command |
|------|---------|
| Contract tests | `uv run pytest tests/test_contracts.py -v` |
| CLI tests | `uv run pytest tests/test_cli.py -v` |
| Schema tests | `uv run pytest tests/test_schema_export.py -v` |
| Full tests | `uv run pytest -v` |

## Key Conventions
- Contract V1 models in `cheapy/models/contracts.py` are the source of truth.
- Keep `cheapy mcp` stdout protocol-clean; diagnostics and errors go to stderr.
- Do not add storage, live provider calls, or real MCP/provider behavior to foundation work.
- Default tests must not make live network calls.

## Commit Attribution
AI commits SHOULD include the model identity in the commit body when creating shared commits.

<!-- BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS -->
## Cheapy MCP Flight Search

Before using Cheapy MCP, use the project skill at `.codex/skills/cheapy/SKILL.md`.
<!-- END CHEAPY MANAGED CODEX INSTRUCTIONS -->

<!-- BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS -->
## Cheapy MCP Flight Search

Before using Cheapy MCP, follow `.cheapy/claude-instructions.md`.
<!-- END CHEAPY MANAGED CLAUDE INSTRUCTIONS -->
