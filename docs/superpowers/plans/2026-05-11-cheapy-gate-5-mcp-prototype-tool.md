# Cheapy Gate 5 MCP Prototype Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Gate 5: make `cheapy mcp` run a real stdio MCP server exposing `search_cheapest_flights`.

**Architecture:** Add a focused MCP adapter in `cheapy/mcp.py` that registers one FastMCP tool and delegates all search behavior to `cheapy.search.search_exact`. Keep Contract V1 models as the tool input/output source of truth, keep `cheapy.cli` as the command dispatch layer, and verify behavior through MCP client-visible structured output.

**Tech Stack:** Python 3.12+, Pydantic v2, official Python MCP SDK/FastMCP, stdlib `asyncio`, Typer, uv, pytest.

---

## Current Baseline

Run this before starting:

```bash
uv run pytest -q
git status --short
```

Expected test baseline:

```text
85 passed
```

Expected working tree note:

```text
The working tree may contain unrelated untracked project docs. Do not stage or edit them unless this plan explicitly names them.
```

Before editing implementation files, read the project-local skills that match this gate:

```bash
sed -n '1,220p' .codex/skills/mcp-builder/SKILL.md
sed -n '1,220p' .codex/skills/python-testing-patterns/SKILL.md
sed -n '1,200p' .codex/skills/uv-package-manager/SKILL.md
sed -n '1,180p' .codex/skills/ai-native-cli/SKILL.md
```

Gate 5 must not add:

- `cheapy search`
- `cheapy mcp install --client codex`
- `cheapy mcp install --client claude`
- live provider calls
- storage
- expanded search
- round-trip search
- flexible-date, nearby-airport, or split-ticket planners
- MCP resources or prompts
- Contract V1 model changes

## File Structure

Create:

- `cheapy/mcp.py`: FastMCP server factory, `search_cheapest_flights` tool registration, safe call into `search_exact`, and stdio runner.
- `tests/test_mcp.py`: MCP client integration tests for tool shape, structured output, invalid input, and protocol-clean startup.

Modify:

- `pyproject.toml`: add the official Python MCP SDK dependency.
- `uv.lock`: update via `uv add mcp`.
- `cheapy/cli.py`: replace the old MCP gate stub with delegation to `run_stdio_server()`.
- `tests/test_cli.py`: remove the old assertion that `cheapy mcp` exits with `MCP_OUTSIDE_CONTRACT_GATE`; MCP process behavior is covered in `tests/test_mcp.py`.

Do not modify:

- `cheapy/search.py`
- `cheapy/models/contracts.py`
- `cheapy/providers/**`
- `README.md`

---

### Task 1: Add MCP SDK Dependency

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add the official MCP Python SDK**

Run:

```bash
uv add mcp
```

Expected:

```text
pyproject.toml and uv.lock are updated.
```

- [ ] **Step 2: Verify FastMCP imports**

Run:

```bash
uv run python -c "from mcp.server.fastmcp import FastMCP; print(FastMCP.__name__)"
```

Expected:

```text
FastMCP
```

- [ ] **Step 3: Verify existing tests still pass**

Run:

```bash
uv run pytest -q
```

Expected:

```text
85 passed
```

- [ ] **Step 4: Commit dependency update**

Run:

```bash
git add pyproject.toml uv.lock
git commit -m "build: add mcp sdk dependency" -m "Generated-by: Codex (GPT-5)"
```

Expected:

```text
Commit succeeds with only pyproject.toml and uv.lock staged.
```

---

### Task 2: Add MCP Integration Tests

**Files:**

- Create: `tests/test_mcp.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Create failing MCP tests**

Create `tests/test_mcp.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import sys
from typing import Any, TypeVar

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from cheapy.models import SearchResponseV1, SearchStatus


T = TypeVar("T")


async def _with_mcp_session(action: Callable[[ClientSession], Awaitable[T]]) -> T:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cheapy", "mcp"],
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await action(session)


def _input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    assert isinstance(schema, dict)
    return schema


def _structured_content(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    assert isinstance(structured, dict)
    return structured


def _text_content(result: Any) -> str:
    return "\n".join(
        block.text
        for block in result.content
        if isinstance(block, types.TextContent)
    )


def test_mcp_lists_only_search_cheapest_flights_tool() -> None:
    async def action(session: ClientSession) -> list[Any]:
        response = await session.list_tools()
        return list(response.tools)

    tools = asyncio.run(_with_mcp_session(action))

    assert [tool.name for tool in tools] == ["search_cheapest_flights"]


def test_mcp_search_tool_uses_top_level_contract_fields() -> None:
    async def action(session: ClientSession) -> Any:
        response = await session.list_tools()
        return response.tools[0]

    tool = asyncio.run(_with_mcp_session(action))
    input_schema = _input_schema(tool)
    properties = input_schema["properties"]

    assert "request" not in properties
    assert {
        "schema_version",
        "origin",
        "destination",
        "departure_date",
        "return_date",
        "search_mode",
        "passengers",
        "max_results",
    }.issubset(properties)
    assert {
        "schema_version",
        "origin",
        "destination",
        "departure_date",
    }.issubset(set(input_schema["required"]))


def test_mcp_search_tool_returns_structured_contract_response() -> None:
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "search_mode": "exact",
        "passengers": {
            "adults": 1,
            "children": 0,
            "infants_on_lap": 0,
            "infants_in_seat": 0,
        },
        "max_results": 5,
    }

    async def action(session: ClientSession) -> Any:
        return await session.call_tool("search_cheapest_flights", arguments)

    result = asyncio.run(_with_mcp_session(action))

    assert getattr(result, "isError", False) is False
    payload = _structured_content(result)
    response = SearchResponseV1.model_validate(payload)

    assert response.schema_version == "1"
    assert response.status == SearchStatus.SUCCESS
    assert [offer.offer_id for offer in response.offers] == [
        "manual_fixture:cxr-sgn-20260710-1",
        "manual_fixture:cxr-sgn-20260710-2",
    ]
    assert response.errors == []


def test_mcp_search_tool_rejects_invalid_contract_input() -> None:
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026/07/10",
    }

    async def action(session: ClientSession) -> Any:
        return await session.call_tool("search_cheapest_flights", arguments)

    result = asyncio.run(_with_mcp_session(action))

    assert getattr(result, "isError", False) is True
    text = _text_content(result).lower()
    assert "date" in text or "validation" in text
```

- [ ] **Step 2: Remove the old MCP gate-stub CLI test**

Edit `tests/test_cli.py` and delete the function `test_mcp_remains_outside_contract_foundation_gate`.

The deleted block starts with:

```python
def test_mcp_remains_outside_contract_foundation_gate() -> None:
```

and ends before:

```python
def test_unknown_command_reports_json_usage_error() -> None:
```

- [ ] **Step 3: Verify the new MCP tests fail for the expected reason**

Run:

```bash
uv run pytest tests/test_mcp.py -v
```

Expected:

```text
FAIL because `cheapy mcp` still exits with the old MCP_OUTSIDE_CONTRACT_GATE stub instead of serving MCP initialize/list-tools/call-tool traffic.
```

- [ ] **Step 4: Verify unrelated CLI tests still pass after deleting the stale test**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected:

```text
PASS
```

---

### Task 3: Implement MCP Adapter And CLI Entrypoint

**Files:**

- Create: `cheapy/mcp.py`
- Modify: `cheapy/cli.py`
- Test: `tests/test_mcp.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Create the FastMCP adapter**

Create `cheapy/mcp.py`:

```python
"""Cheapy MCP server."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from cheapy.models import PassengersV1, SearchMode, SearchRequestV1, SearchResponseV1
from cheapy.search import search_exact


_TOOL_ANNOTATIONS: dict[str, object] = {
    "title": "Search Cheapest Flights",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def create_mcp_server() -> FastMCP:
    """Create the Cheapy MCP server."""
    server = FastMCP("cheapy_mcp")

    @server.tool(
        name="search_cheapest_flights",
        annotations=_TOOL_ANNOTATIONS,
    )
    async def search_cheapest_flights(
        schema_version: Literal["1"],
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        search_mode: SearchMode = SearchMode.EXACT,
        passengers: PassengersV1 | None = None,
        max_results: int = 5,
    ) -> SearchResponseV1:
        """Search exact one-way flights and return Contract V1 results."""
        request = SearchRequestV1.model_validate(
            _request_payload(
                schema_version=schema_version,
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                return_date=return_date,
                search_mode=search_mode,
                passengers=passengers,
                max_results=max_results,
            )
        )
        return await asyncio.to_thread(search_exact, request)

    return server


def run_stdio_server() -> None:
    """Run the Cheapy MCP server over stdio."""
    create_mcp_server().run()


def _request_payload(
    *,
    schema_version: Literal["1"],
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
    search_mode: SearchMode | str,
    passengers: object,
    max_results: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date,
        "search_mode": search_mode.value
        if isinstance(search_mode, SearchMode)
        else search_mode,
        "max_results": max_results,
    }
    if isinstance(passengers, PassengersV1):
        payload["passengers"] = passengers.model_dump(mode="json")
    elif passengers is not None:
        payload["passengers"] = passengers
    return payload
```

- [ ] **Step 2: Wire `cheapy mcp` to the MCP server runner**

Edit `cheapy/cli.py`.

Add this import near the other Cheapy imports:

```python
from cheapy.mcp import run_stdio_server
```

Replace the existing `mcp` command body with:

```python
@app.command()
def mcp() -> None:
    """Run the stdio MCP server."""
    run_stdio_server()
```

The old `_json_echo(_error_payload(...), err=True)` block for `MCP_OUTSIDE_CONTRACT_GATE` must be removed.

- [ ] **Step 3: Run focused MCP tests**

Run:

```bash
uv run pytest tests/test_mcp.py -v
```

Expected:

```text
PASS
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected:

```text
PASS
```

- [ ] **Step 5: Commit MCP adapter and tests**

Run:

```bash
git add cheapy/mcp.py cheapy/cli.py tests/test_mcp.py tests/test_cli.py
git commit -m "feat: add mcp search tool" -m "Generated-by: Codex (GPT-5)"
```

Expected:

```text
Commit succeeds with only the MCP adapter, CLI update, and MCP/CLI tests staged.
```

---

### Task 4: Full Verification And Scope Check

**Files:**

- Verify: `pyproject.toml`
- Verify: `uv.lock`
- Verify: `cheapy/mcp.py`
- Verify: `cheapy/cli.py`
- Verify: `tests/test_mcp.py`
- Verify: `tests/test_cli.py`

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_mcp.py -v
uv run pytest tests/test_cli.py -v
```

Expected:

```text
Both commands pass.
```

- [ ] **Step 2: Run full tests**

Run:

```bash
uv run pytest -v
```

Expected:

```text
All tests pass.
```

- [ ] **Step 3: Verify CLI version still works**

Run:

```bash
uv run cheapy --version
```

Expected:

```text
0.1.0
```

- [ ] **Step 4: Verify no out-of-scope symbols were added**

Run:

```bash
rg -n "def search\\(|@app\\.command\\(\"search\"|mcp install|price_history|get_price_history" cheapy tests
```

Expected:

```text
No matches that add CLI search, MCP installer behavior, storage, or price-history tools.
```

- [ ] **Step 5: Verify working tree scope**

Run:

```bash
git status --short
```

Expected:

```text
Only intended Gate 5 files are modified or already committed. Unrelated pre-existing untracked docs may remain untracked.
```

---

## Implementation Notes

The `search_cheapest_flights` tool must return MCP client-visible structured data. The tests in `tests/test_mcp.py` intentionally inspect `CallToolResult.structured_content` or `CallToolResult.structuredContent` and validate it with `SearchResponseV1.model_validate(...)`. Do not replace the structured response with a prose summary.

The MCP tool function uses `asyncio.to_thread(search_exact, request)` because `search_exact` is synchronous and internally uses `asyncio.run()` for provider calls. Running it in a worker thread avoids nested event-loop errors under FastMCP's async server runtime.

Protocol cleanliness is tested through the MCP Python client. If `cheapy mcp` writes Typer text, health JSON, logs, or diagnostics to stdout, the client will fail to initialize or parse tool responses.

## Self-Review Checklist

- Spec coverage: Tasks cover dependency, server entrypoint, tool shape, top-level input fields, `search_exact`, structured `SearchResponseV1`, stdout cleanliness, and tests.
- Scope: No task adds CLI search, installers, live providers, expanded search, storage, round-trip behavior, resources, prompts, or Contract V1 changes.
- Type consistency: `SearchRequestV1`, `SearchResponseV1`, `PassengersV1`, `SearchMode`, `search_exact`, and `run_stdio_server` names match existing or planned files.
- Verification: Focused MCP/CLI tests, full test suite, version smoke, and scope grep are included.
