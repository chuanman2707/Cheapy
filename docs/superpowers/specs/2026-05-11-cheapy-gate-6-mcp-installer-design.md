# Cheapy Gate 6 MCP Installer Design

Date: 2026-05-11

## Summary

Gate 6 adds the local installer path for Cheapy's MCP prototype:

```bash
cheapy mcp install --client codex
cheapy mcp install --client claude
```

The installer registers the existing stdio MCP server with Codex or Claude Code, creates or updates the project-local agent instruction hooks, and reports exactly what changed.

The installer treats the current working directory as the project root for project-local instruction files and hooks. MCP client config remains user/local by default.

The approved low-tech-debt approach is:

1. Prefer the official client CLI.
2. Fall back to direct config editing only when the official client CLI is unavailable or cannot complete the install.
3. Create Cheapy-managed backups only for direct config edits.

This keeps Cheapy coupled to client config internals only on the fallback path.

## Goals

- Add `cheapy mcp install --client codex|claude`.
- Resolve the installed `cheapy` executable to an absolute path before writing or passing config.
- Register the MCP server entry with server name `cheapy`, command `/absolute/path/to/cheapy`, and args `["mcp"]`.
- Prefer official client commands:
  - Codex: `codex mcp add cheapy -- /absolute/path/to/cheapy mcp`
  - Claude: `claude mcp add --transport stdio cheapy -- /absolute/path/to/cheapy mcp`
- Direct-edit fallback must parse before writing, preserve unrelated config, update idempotently, create timestamped backups, write atomically, and set config/backup permissions to `0600` where supported.
- Create or update the project-local Codex skill file at `.codex/skills/cheapy/SKILL.md`.
- Add or update a managed `AGENTS.md` hook that points Codex to `.codex/skills/cheapy/SKILL.md`.
- Create or update Claude instructions at `.cheapy/claude-instructions.md`.
- Add or update a managed `CLAUDE.md` hook that points Claude to `.cheapy/claude-instructions.md`.
- Treat the current working directory as the project root for `.codex/`, `.cheapy/`, `AGENTS.md`, and `CLAUDE.md`.
- Preserve existing `AGENTS.md`, `CLAUDE.md`, and unrelated MCP config content.
- Return machine-readable JSON on stdout for successful installs.
- Send usage errors and installer failures as structured JSON to stderr.
- Keep default tests fully local with no live provider calls.

## Non-Goals

- No live provider calls.
- No provider changes.
- No `cheapy search` CLI.
- No MCP tool contract changes.
- No changes to `cheapy mcp` server runtime behavior beyond command nesting needed for `install`.
- No HTTP MCP transport.
- No project-shared `.mcp.json` as the default install target.
- No global Codex skill installation.
- No storage, watchlists, scheduler, or alerts.
- No automatic rollback command in Gate 6. Backups are created for manual recovery and failure reporting.

## Approved Approach

Use official client CLIs first and direct config editing second.

This is the lowest-tech-debt path because Codex and Claude own their preferred config mutation formats. Cheapy only needs client-specific config knowledge when the official client CLI is missing or fails in a recoverable way.

Cheapy-managed backups are created only when Cheapy directly edits a config file. If an official client CLI succeeds, Cheapy reports the command path but does not create a redundant backup.

## CLI Shape

The CLI should expose:

```bash
cheapy mcp install --client codex
cheapy mcp install --client claude
```

`--client` is required and constrained to `codex` or `claude`.

The existing `cheapy mcp` command must continue to run the stdio MCP server with protocol-clean stdout. The implementation plan should choose a Typer shape that supports both:

```bash
cheapy mcp
cheapy mcp install --client codex
```

without printing Typer help or diagnostics to stdout when the stdio server command is used.

## Components

### CLI Entrypoint

`cheapy/cli.py` owns command dispatch only.

It parses `--client`, calls the installer orchestration layer, prints success JSON to stdout, and preserves the existing structured stderr behavior for usage and runtime failures.

It must not contain config parsing, backup, or hook-update logic.

### Installer Orchestrator

Add a focused module such as `cheapy/mcp_installer.py`.

Responsibilities:

- client enum and installer result models
- `cheapy` executable resolution
- MCP server entry construction
- official CLI invocation
- fallback decision
- direct config edit delegation
- agent hook delegation
- final install report assembly

The orchestration boundary should make it easy to test with mocked `shutil.which`, mocked `subprocess.run`, and temporary config files.

### Client Config Editors

Client config editing can live in `cheapy/mcp_installer.py` if it stays small, or in a sibling module such as `cheapy/client_configs.py` if separating it keeps the code clearer.

Fallback editors must:

- know the fallback config path for the specific client
- parse the existing file before writing
- preserve unrelated keys and server entries
- add or update only the `cheapy` server entry
- avoid duplicate entries
- create a timestamped backup before writing
- write a temporary file on the same filesystem
- replace atomically with `os.replace`
- chmod config and backup files to `0600` where supported
- leave the original config untouched if parsing or writing fails before replacement

The direct-edit fallback should be conservative. If the installer cannot determine a safe path or parse the known format, it should fail with exact manual instructions instead of guessing.

### Agent Hooks

Add a focused module such as `cheapy/agent_hooks.py`.

Responsibilities:

- write the Codex skill file at `.codex/skills/cheapy/SKILL.md`
- write Claude instructions at `.cheapy/claude-instructions.md`
- insert or update managed blocks in `AGENTS.md` and `CLAUDE.md`
- preserve all unmanaged content
- report `updated`, `unchanged`, or `manual_required` for each hook

Managed blocks should use explicit markers so future installer runs can update them idempotently.

Example marker shape:

```text
<!-- BEGIN CHEAPY MANAGED MCP INSTRUCTIONS -->
...
<!-- END CHEAPY MANAGED MCP INSTRUCTIONS -->
```

If a hook file cannot be updated safely, the installer must print the exact manual text to add and mark skill activation as incomplete.

## Data Flow

Install flow:

1. Parse `--client`.
2. Resolve `shutil.which("cheapy")`.
3. If unresolved, fail with `MISSING_EXECUTABLE` and instruct the user to install `cheapy-flights` first.
4. Build the MCP server entry:

   ```json
   {
     "command": "/absolute/path/to/cheapy",
     "args": ["mcp"]
   }
   ```

5. Attempt official client CLI install:
   - Codex: `codex mcp add cheapy -- /absolute/path/to/cheapy mcp`
   - Claude: `claude mcp add --transport stdio cheapy -- /absolute/path/to/cheapy mcp`
6. If the official CLI succeeds, record method `official_cli`.
7. If the official CLI is unavailable or fails in a recoverable way, attempt direct config edit.
8. During direct edit, create backup, update or insert the `cheapy` server entry, and atomically replace the config.
9. Create or update project-local instruction files.
10. Create or update managed hooks in `AGENTS.md` and `CLAUDE.md`.
11. Print a JSON install report.

Success report should include:

- `status`
- `client`
- `server_name`
- `method`
- `executable`
- `config_path` when known
- `backup_path` when a direct-edit backup was created
- `mcp_entry`
- `codex_skill`
- `agents_hook`
- `claude_instructions`
- `claude_hook`
- `manual_steps`

## Config Scope

Default installs are user/local client installs, not project-shared installs.

Rationale: the MCP server command uses an absolute local executable path, which is not portable across team members. Writing project-shared `.mcp.json` by default would make the repo contain machine-specific paths.

For Claude Code, the official command should use the default/local scope. Claude's project scope and `.mcp.json` can be considered later as an explicit option if the product needs team-shared MCP config.

## Codex Behavior

Official path:

```bash
codex mcp add cheapy -- /absolute/path/to/cheapy mcp
```

Fallback direct edit target should be Codex's user config, expected at:

```text
~/.codex/config.toml
```

The fallback editor should add or update a TOML table equivalent to:

```toml
[mcp_servers.cheapy]
command = "/absolute/path/to/cheapy"
args = ["mcp"]
```

The implementation plan must verify the exact Codex config shape against the installed CLI behavior before coding the fallback editor. If the observed shape differs, the plan should use the observed shape and update tests accordingly.

## Claude Behavior

Official path:

```bash
claude mcp add --transport stdio cheapy -- /absolute/path/to/cheapy mcp
```

Claude Code documents stdio MCP installation through `claude mcp add` and stores default local-scope server config in `~/.claude.json` under the current project path. Project scope writes `.mcp.json`, but Gate 6 does not use project scope by default.

Fallback direct edit target should be:

```text
~/.claude.json
```

The fallback editor should update the current project entry under `projects` and set:

```json
{
  "mcpServers": {
    "cheapy": {
      "type": "stdio",
      "command": "/absolute/path/to/cheapy",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

If the file cannot be parsed or the project shape is incompatible, fail closed and print the manual `claude mcp add` command.

Reference: Anthropic's Claude Code MCP documentation describes local stdio installation with `claude mcp add --transport stdio <name> -- <command> [args...]`, local scope in `~/.claude.json`, and project scope through `.mcp.json`.

## Instruction Content

### Codex Skill

The project-local Codex skill must teach Codex:

- use Cheapy for flight-search requests
- call only the high-level MCP tool `search_cheapest_flights`
- normalize clear airport names to IATA before tool calls
- ask for clarification when airport text is ambiguous
- normalize dates to ISO `YYYY-MM-DD`
- require origin, destination, and departure date before calling
- use Contract V1 passenger defaults when the user does not specify passenger counts
- ask for clarification when the user indicates non-default passengers but leaves the counts ambiguous
- use exact mode for fixed one-way MVP searches
- explain that expanded/flexible searches are deferred until Cheapy exposes them
- never ask the user to choose a provider
- explain mixed currency cautiously if it appears

The existing `.codex/skills/cheapy/SKILL.md` should be updated rather than replaced blindly. The final content should match the current Contract V1 and Gate 5 MCP tool behavior.

### AGENTS.md Hook

The `AGENTS.md` managed block should point Codex to:

```text
.codex/skills/cheapy/SKILL.md
```

It should stay concise and preserve the existing project instructions.

### Claude Instructions

Claude instructions live at:

```text
.cheapy/claude-instructions.md
```

They should contain the same operational guidance as the Codex skill, translated into plain Claude-readable project instructions rather than Codex skill frontmatter.

### CLAUDE.md Hook

The installer should add a managed block in `CLAUDE.md` pointing to:

```text
.cheapy/claude-instructions.md
```

The current repository has `CLAUDE.md` as a symlink to `AGENTS.md`. Gate 6 must handle this deliberately:

- If updating the symlink target is safe, insert or update the managed block in the target content.
- If not safe, do not replace or unlink the symlink. Report `manual_required` with exact text.

## Error Handling

Usage errors:

- invalid or missing `--client`
- exit code `2`
- structured JSON error on stderr

Installer runtime failures:

- unresolved `cheapy` executable: `MISSING_EXECUTABLE`
- official client unavailable and no safe fallback path: `CLIENT_CONFIG_UNAVAILABLE`
- config parse failure: `CONFIG_PARSE_FAILED`
- config write failure: `CONFIG_WRITE_FAILED`
- backup failure: `CONFIG_BACKUP_FAILED`
- unsafe hook update: not fatal to MCP config, but reported as `manual_required`

Official CLI failure handling:

- If the official CLI is missing, attempt fallback direct edit.
- If the official CLI exists but returns non-zero, attempt fallback only when the target config path and format are known.
- If neither path is safe, fail with the exact manual command the user can run.

Direct edit failure handling:

- Parse failures happen before backup/write and leave the original untouched.
- Backup failures stop the write.
- Write failures leave the original untouched if replacement has not happened.
- If replacement succeeds but validation after write fails, report backup path and manual rollback instructions.

Hook failure handling:

- The MCP config install may still succeed if an instruction hook requires manual action.
- The JSON report must make this explicit with `manual_steps`.

## Idempotency

Repeated installs must not duplicate:

- MCP server entries
- Codex skill content
- AGENTS.md managed block
- Claude instruction content
- CLAUDE.md managed block

If an existing `cheapy` MCP entry points to a different command or args, Gate 6 updates it to the current resolved executable and `["mcp"]`.

If an existing managed block is present, Gate 6 replaces only the block contents.

If unmanaged user text exists before or after a managed block, Gate 6 preserves it byte-for-byte where possible.

## Security And Permissions

- Do not print secrets.
- Do not copy global user config into project files.
- Avoid writing project-shared MCP config by default because it would contain machine-local absolute paths.
- Set direct-edit config and backup permissions to `0600` where supported.
- Do not follow unsafe path assumptions if a config path is not a regular file or safe parent directory.
- Do not make network calls in installer tests.

## Tests

Add focused tests that use temporary files and mocked process calls.

Expected test files:

- `tests/test_mcp_installer.py`
- `tests/test_agent_hooks.py`
- updates to `tests/test_cli.py`

Required coverage:

- Codex install uses official CLI when `codex` exists.
- Claude install uses official CLI when `claude` exists.
- Official CLI success does not create a Cheapy-managed backup.
- Missing official CLI triggers direct-edit fallback when safe.
- Direct-edit fallback creates timestamped backup.
- Backup and config permissions are set to `0600` where supported.
- Direct edit preserves unrelated config content and server entries.
- Direct edit updates an existing stale `cheapy` entry idempotently.
- Repeated install does not duplicate config entries or managed blocks.
- Parse failure leaves original config untouched.
- Write failure leaves original config untouched and reports backup path when available.
- Codex skill file is created or updated with current MCP guidance.
- `AGENTS.md` hook is inserted and updated idempotently.
- Claude instruction file is created or updated.
- `CLAUDE.md` symlink behavior is explicit and tested.
- CLI success prints JSON to stdout and no errors to stderr.
- CLI failures print structured JSON to stderr.

Focused verification:

```bash
uv run pytest tests/test_mcp_installer.py tests/test_agent_hooks.py tests/test_cli.py -v
uv run pytest -v
```

## Acceptance Criteria

Gate 6 is complete when:

- `cheapy mcp install --client codex` succeeds through official CLI or safe fallback.
- `cheapy mcp install --client claude` succeeds through official CLI or safe fallback.
- The installed MCP entry starts `/absolute/path/to/cheapy mcp`.
- Direct config edits create backups and are idempotent.
- Official CLI installs do not create redundant Cheapy-managed backups.
- Project-local Codex skill and Claude instruction files are present.
- `AGENTS.md` and `CLAUDE.md` activation hooks are present or exact manual steps are reported.
- Tests verify installer behavior without requiring real Codex or Claude installs.
- Full test suite passes.
