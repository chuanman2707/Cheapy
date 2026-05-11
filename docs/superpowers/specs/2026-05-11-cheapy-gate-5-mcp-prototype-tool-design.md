# Cheapy Gate 5 MCP Prototype Tool Design

## Summary

Gate 5 turns Cheapy into a usable local stdio MCP server for Codex and Claude.

The approved scope is narrow: `cheapy mcp` runs a real MCP server and exposes one tool, `search_cheapest_flights`. The tool accepts Contract V1 search fields directly as top-level MCP arguments, validates them with `SearchRequestV1`, calls the existing `cheapy.search.search_exact` orchestrator, and returns `SearchResponseV1` as structured JSON.

Gate 5 does not add a CLI search command, MCP installer commands, live providers, expanded search, round-trip support, storage, or new provider behavior.

## Goals

- Replace the current `cheapy mcp` gate stub with a real stdio MCP server.
- Expose one MCP tool named `search_cheapest_flights`.
- Use Contract V1 `SearchRequestV1` as the tool input source of truth.
- Keep MCP tool arguments as top-level `SearchRequestV1` fields, not wrapped in a `request` object.
- Call `cheapy.search.search_exact` for all search execution.
- Return Contract V1 `SearchResponseV1` as structured JSON.
- Keep stdout protocol-clean: only MCP protocol output may go to stdout.
- Send diagnostics, logs, and unexpected server errors to stderr only.
- Add focused tests for MCP tool shape, tool call behavior, and protocol cleanliness.

## Non-Goals

- No `cheapy search` CLI command.
- No `cheapy mcp install --client codex`.
- No `cheapy mcp install --client claude`.
- No Codex or Claude config editing.
- No live provider calls.
- No new provider integrations.
- No expanded search implementation.
- No round-trip search implementation.
- No flexible-date, nearby-airport, or split-ticket planner.
- No storage or price history.
- No Contract V1 model changes and no new Contract V1 error codes.

## Approved Approach

Use the official Python MCP SDK with FastMCP as a thin protocol adapter.

Expected structure:

```text
cheapy/cli.py      -> CLI command dispatch
cheapy/mcp.py      -> FastMCP server and tool registration
cheapy/search.py   -> existing exact-search orchestrator
cheapy/models/     -> Contract V1 source of truth
```

The MCP adapter owns only protocol-facing concerns:

- server construction
- tool registration
- tool argument validation
- structured response serialization
- stdio server startup
- stdout/stderr cleanliness around the MCP entrypoint

It must not duplicate provider loading, airport resolution, offer sorting, currency grouping, request ID generation, or search response assembly. Those behaviors remain inside `search_exact`.

## Components

### CLI Entrypoint

`cheapy mcp` becomes the stdio MCP server command.

The existing command in `cheapy/cli.py` currently returns an `MCP_OUTSIDE_CONTRACT_GATE` error on stderr. Gate 5 replaces that behavior with a delegation to the MCP server runner.

The command must not print Typer banners, startup messages, JSON health objects, or user-facing diagnostics to stdout. It should either stay silent on stderr during normal startup or write any diagnostic information to stderr only.

### MCP Server Module

Add a module such as `cheapy/mcp.py`.

The module should expose a small public API for the CLI, for example:

```python
def run_stdio_server() -> None:
    ...
```

It may also expose a server factory for tests, for example:

```python
def create_mcp_server() -> FastMCP:
    ...
```

The exact names can be adjusted during implementation if the MCP SDK makes another shape cleaner, but the boundary should remain explicit: the CLI starts the server, while tests can inspect or exercise the registered tool without relying only on subprocesses.

### MCP Tool

The server exposes exactly one Gate 5 tool:

```text
search_cheapest_flights
```

The tool accepts top-level arguments matching `SearchRequestV1`:

- `schema_version`
- `origin`
- `destination`
- `departure_date`
- `return_date`
- `search_mode`
- `passengers`
- `max_results`

The tool must not require a wrapper argument named `request`.

The tool function validates the received arguments with `SearchRequestV1.model_validate(...)`. This preserves strict Contract V1 behavior for extra fields, date format, passenger counts, enum values, and bounds.

The tool calls:

```python
search_exact(request)
```

and returns the resulting `SearchResponseV1` as structured JSON.

### Tool Annotations

The tool should include MCP annotations where supported:

- `readOnlyHint=True`
- `destructiveHint=False`
- `idempotentHint=True`
- `openWorldHint=False` for Gate 5, because the only default provider behavior is the bundled manual fixture and default tests must not make live network calls

These annotations are hints for clients, not security controls.

## Data Flow

`search_cheapest_flights` runs this flow:

1. MCP client sends top-level tool arguments.
2. MCP adapter validates them as a `SearchRequestV1`.
3. The adapter calls `search_exact(request)`.
4. `search_exact` handles airport resolution, Gate 4 exact one-way scope checks, provider registry loading, manual fixture provider execution, and `SearchResponseV1` assembly.
5. The adapter serializes the response as structured JSON for MCP.
6. The MCP server writes only protocol output to stdout.

For the default fixture acceptance path:

- request: `CXR` to `SGN` on `2026-07-10`
- search mode: `exact`
- return date: `None`
- provider: bundled `manual_fixture`
- response: `SearchResponseV1(status="success")` with the two deterministic VND fixture offers

## Structured Output

The preferred output is MCP structured content backed by `SearchResponseV1`.

Implementation should use the MCP SDK mechanism that gives clients structured data, not a hand-written prose response. Depending on the exact SDK API, the adapter can return one of:

- a `SearchResponseV1` instance, if FastMCP generates structured output from Pydantic models correctly
- `response.model_dump(mode="json")`, if returning a plain JSON-compatible dict is clearer or more reliable

The acceptance requirement is client-visible structured JSON with the Contract V1 top-level fields:

- `schema_version`
- `status`
- `request_id`
- `offers`
- `warnings`
- `errors`
- `provider_statuses`
- `search_plan`
- `mixed_currency`
- `currency_groups`
- `currency_notes`
- `candidates`

The adapter must not replace this with a text summary.

## Error Handling

### Runtime Search Failures

Runtime search failures remain data-level failures returned by `search_exact`.

Examples:

- unknown airport
- unsupported expanded search
- unsupported round trip
- no enabled provider
- no exact-capable provider
- provider failure

These return `SearchResponseV1(status="failed")` or another Contract V1 status according to existing search orchestration rules.

### Input Contract Failures

Invalid tool arguments are validation failures.

Examples:

- missing required fields
- invalid `schema_version`
- invalid date format
- string passenger counts where strict integers are required
- extra fields
- `max_results` outside Contract V1 bounds

These should be reported by the MCP tool validation layer as tool/protocol validation errors. Gate 5 does not convert invalid input into `SearchResponseV1`, because there is no valid request object from which to build the response contract.

### Unexpected Adapter Failures

Unexpected adapter or server failures must not print tracebacks or diagnostics to stdout.

If the implementation logs anything, it must use stderr. Error messages exposed through MCP should avoid secrets, raw provider payloads, full tracebacks, and raw environment details.

### Event Loop Boundary

`search_exact` is synchronous and currently drives provider async code with `asyncio.run()`.

The MCP adapter must avoid calling `search_exact` directly from inside an active event loop in a way that would trigger nested event loop errors. The implementation should choose one of these safe patterns:

- register a synchronous FastMCP tool if the SDK executes it safely outside an active event loop
- if the tool must be async, dispatch `search_exact` to a worker thread before awaiting the result

The implementation plan should verify the selected SDK behavior before coding the final adapter.

## Protocol Cleanliness

`cheapy mcp` is a stdio protocol process.

Rules:

- stdout is reserved for MCP protocol messages only.
- No Typer output, startup text, logs, warnings, provider diagnostics, or health JSON may go to stdout.
- stderr may contain diagnostics when needed.
- Provider or adapter exceptions must not leak tracebacks to stdout.
- Default tests must not require live network access.

## Tests

Add focused MCP tests, likely in `tests/test_mcp.py`.

Required coverage:

- The MCP server registers exactly the Gate 5 tool `search_cheapest_flights`.
- The tool input schema exposes `SearchRequestV1` fields as top-level arguments.
- The tool input schema does not require or advertise a wrapper argument named `request`.
- A fixture MCP tool call for `CXR` to `SGN` on `2026-07-10` returns structured `SearchResponseV1` data with `status="success"`.
- The fixture tool call returns the deterministic manual fixture offer IDs.
- Invalid input is rejected by the MCP/tool validation layer.
- `cheapy mcp` starts as a subprocess and responds to basic MCP initialize/list-tools traffic without writing non-protocol text to stdout.
- Existing CLI tests are updated so `cheapy mcp` no longer expects `MCP_OUTSIDE_CONTRACT_GATE`.

Focused verification commands:

```bash
uv run pytest tests/test_mcp.py -v
uv run pytest tests/test_cli.py -v
```

Full verification:

```bash
uv run pytest -v
```

## Acceptance Criteria

Gate 5 is complete when:

- `cheapy mcp` runs a real stdio MCP server.
- The server exposes exactly one Gate 5 tool, `search_cheapest_flights`.
- The tool input uses top-level `SearchRequestV1` fields.
- The tool does not use a `request` wrapper argument.
- The tool validates input with Contract V1 behavior.
- The tool calls `cheapy.search.search_exact`.
- The tool returns `SearchResponseV1` as structured JSON.
- The fixture request `CXR` to `SGN` on `2026-07-10` returns the two deterministic manual fixture offers through MCP.
- stdout remains MCP protocol-clean.
- diagnostics and unexpected server errors do not go to stdout.
- no CLI search command is added.
- no MCP installer command is added.
- no live provider, expanded search, storage, or round-trip behavior is added.
- `uv run pytest -v` passes.

## Deferred Work

Deferred to later gates:

- `cheapy mcp install --client codex`
- `cheapy mcp install --client claude`
- client config backup and atomic edit behavior
- CLI `cheapy search`
- MCP resources and prompts from the master spec
- expanded search orchestration
- round-trip search
- flexible-date planner
- nearby-airport planner
- split-ticket planner
- live provider integration
- storage and price history
