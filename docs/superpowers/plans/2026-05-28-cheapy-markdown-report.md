# Cheapy Markdown Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Markdown presentation layer for Cheapy search results, with safe clickable fare links and unchanged Contract V1 structured output.

**Architecture:** Create a pure `cheapy.markdown_report` formatter that renders from `SearchRequestV1` and `SearchResponseV1`, then wire it into MCP human-readable content and opt-in CLI Markdown flags. Keep provider modules and Contract V1 models unchanged; every public link is revalidated before Markdown linking.

**Tech Stack:** Python 3.12+, Pydantic Contract V1 models, FastMCP `CallToolResult`, Typer CLI, pytest, uv.

---

## Commit Policy

The repository may have unrelated dirty worktree changes. Before each commit, run:

```bash
git diff --cached --name-status
```

Only files from the current task should be staged. Do not revert or reformat unrelated files.

Recommended commit body for AI commits:

```text
AI-Generated-By: GPT-5 Codex
```

## Reference Material

- Project instructions: `AGENTS.md`
- Cheapy skill: `.codex/skills/cheapy/SKILL.md`
- Design spec: `docs/superpowers/specs/2026-05-28-cheapy-markdown-report-design.md`
- Contract source of truth: `cheapy/models/contracts.py`
- Public URL validator: `cheapy/public_url_safety.py`
- MCP tool registration: `cheapy/mcp.py`
- CLI entrypoint: `cheapy/cli.py`
- Storage history shape: `cheapy/storage/sqlite.py`
- Existing MCP tests: `tests/test_mcp.py`
- Existing CLI tests: `tests/test_cli.py`

## Scope Check

This feature is one presentation/reporting surface. It creates one formatter module, updates MCP response content, and adds opt-in CLI Markdown flags. Do not add new Contract V1 fields, provider behavior, storage columns, live provider calls, booking deeplinks, Browserless, or raw provider URL passthrough.

## File Structure

Create:

- `cheapy/markdown_report.py`: pure Markdown rendering helpers for search reports, offer prices, provider statuses, warnings, and errors.
- `tests/test_markdown_report.py`: deterministic formatter tests with no provider/network calls.

Modify:

- `cheapy/mcp.py`: return MCP text content with the Markdown report while preserving `SearchResponseV1` structured content.
- `tests/test_mcp.py`: assert structured content still validates and text content contains the report.
- `cheapy/cli.py`: add opt-in `--markdown` to `history show` and `watchlist check`, plus safe history request reconstruction.
- `tests/test_cli.py`: assert default JSON remains unchanged and opt-in Markdown renders through the report helper.

## Task 0: Preflight

**Files:**
- Read: `AGENTS.md`
- Read: `.codex/skills/cheapy/SKILL.md`
- Read: `docs/superpowers/specs/2026-05-28-cheapy-markdown-report-design.md`
- Read: `cheapy/models/contracts.py`
- Read: `cheapy/mcp.py`
- Read: `cheapy/cli.py`

- [ ] **Step 1: Confirm branch and worktree**

Run:

```bash
git branch --show-current
git status --short
```

Expected: branch is `codex/local-sqlite-history-watchlist`. Record any existing dirty files and do not revert unrelated changes.

- [ ] **Step 2: Run focused baseline tests**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_mcp.py tests/test_cli.py -v
```

Expected: PASS. If a test fails before edits, record the exact failing test names and failure messages before proceeding.

## Task 1: Markdown Formatter

**Files:**
- Create: `tests/test_markdown_report.py`
- Create: `cheapy/markdown_report.py`

- [ ] **Step 1: Write failing formatter tests**

Create `tests/test_markdown_report.py` with:

```python
from __future__ import annotations

from typing import Any

from cheapy.markdown_report import render_offer_price, render_search_report
from cheapy.models import (
    CandidateFamily,
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
    Severity,
    WarningCode,
    WarningV1,
)


TRAVELOKA_URL = (
    "https://www.traveloka.com/en-en/flight/fulltwosearch?"
    "ap=CXR.SGN&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
)


def _request(**overrides: Any) -> SearchRequestV1:
    data: dict[str, Any] = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "search_mode": SearchMode.EXACT,
        "passengers": {
            "adults": 1,
            "children": 0,
            "infants_on_lap": 0,
            "infants_in_seat": 0,
        },
        "max_results": 5,
    }
    data.update(overrides)
    return SearchRequestV1.model_validate(data)


def _offer(**overrides: Any) -> FlightOfferV1:
    data: dict[str, Any] = {
        "offer_id": "traveloka:CXR-SGN:2026-07-10:1",
        "price_amount": 1_280_000.0,
        "currency": "VND",
        "comparable": True,
        "rank_within_currency": 1,
        "global_rank": 1,
        "provider": "traveloka",
        "requested_origin": "CXR",
        "requested_destination": "SGN",
        "actual_origin": "CXR",
        "actual_destination": "SGN",
        "nearby_origin_distance_km": None,
        "nearby_destination_distance_km": None,
        "requested_departure_date": "2026-07-10",
        "actual_departure_date": "2026-07-10",
        "departure_offset_days": 0,
        "requested_return_date": None,
        "actual_return_date": None,
        "return_offset_days": None,
        "legs": [
            FlightLegV1(
                origin="CXR",
                destination="SGN",
                departure_time="2026-07-10T08:15:00",
                arrival_time="2026-07-10T09:25:00",
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=70,
            )
        ],
        "total_duration_minutes": 70,
        "stops": 0,
        "flags": OfferFlagsV1(),
        "fare_details_status": "not_collected",
        "public_search_url": None,
    }
    data.update(overrides)
    return FlightOfferV1.model_validate(data)


def _provider_status(**overrides: Any) -> ProviderStatusV1:
    data: dict[str, Any] = {
        "provider_name": "traveloka",
        "capability": "exact_one_way",
        "status": ProviderStatusCode.SUCCESS,
        "planned_call_count": 1,
        "executed_call_count": 1,
        "succeeded_call_count": 1,
        "failed_call_count": 0,
        "duration_ms": 25,
        "warnings": [],
        "errors": [],
        "retryable": False,
    }
    data.update(overrides)
    return ProviderStatusV1.model_validate(data)


def _response(**overrides: Any) -> SearchResponseV1:
    offers = overrides.pop("offers", [_offer()])
    data: dict[str, Any] = {
        "schema_version": "1",
        "status": SearchStatus.SUCCESS,
        "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
        "offers": offers,
        "warnings": [],
        "errors": [],
        "provider_statuses": [_provider_status()],
        "search_plan": SearchPlanV1(
            search_mode=SearchMode.EXACT,
            planned_candidate_count=1,
            executed_candidate_count=1,
            planned_provider_call_count=1,
            executed_provider_call_count=1,
            candidate_count_by_family={CandidateFamily.EXACT: 1},
            provider_call_count_by_family={CandidateFamily.EXACT: 1},
            truncated=False,
            truncated_families=[],
            candidate_families=[CandidateFamily.EXACT],
        ),
        "mixed_currency": False,
        "currency_groups": [
            {
                "currency": currency,
                "offer_ids": [
                    offer.offer_id for offer in offers if offer.currency == currency
                ],
            }
            for currency in sorted({offer.currency for offer in offers})
        ],
        "currency_notes": [],
        "candidates": None,
    }
    data.update(overrides)
    return SearchResponseV1.model_validate(data)


def test_render_offer_price_links_valid_public_search_url() -> None:
    offer = _offer(
        price_amount=4_920_000.0,
        public_search_url=TRAVELOKA_URL,
    )

    assert render_offer_price(offer) == (
        f"[4,920,000 VND on Traveloka]({TRAVELOKA_URL})"
    )


def test_render_offer_price_plain_without_public_search_url() -> None:
    offer = _offer(public_search_url=None)

    assert render_offer_price(offer) == "1,280,000 VND on Traveloka"


def test_render_offer_price_plain_when_public_search_url_is_invalid() -> None:
    offer = _offer().model_copy(
        update={
            "public_search_url": "https://evil.example/search?token=secret-token"
        }
    )

    rendered = render_offer_price(offer)

    assert rendered == "1,280,000 VND on Traveloka"
    assert "evil.example" not in rendered
    assert "secret-token" not in rendered


def test_render_search_report_includes_summary_and_linked_best_offer() -> None:
    response = _response(
        offers=[
            _offer(
                price_amount=4_920_000.0,
                public_search_url=TRAVELOKA_URL,
            )
        ]
    )

    report = render_search_report(_request(), response)

    assert "## CXR -> SGN | 2026-07-10 | 1 adult | Economy" in report
    assert "| Status | success |" in report
    assert "### Best Offers" in report
    assert f"[4,920,000 VND on Traveloka]({TRAVELOKA_URL})" in report
    assert report.count(TRAVELOKA_URL) == 1
    assert "public_search_url" not in report


def test_render_search_report_handles_empty_offers() -> None:
    report = render_search_report(_request(), _response(offers=[]))

    assert "### Best Offers" in report
    assert "No offers returned." in report


def test_render_search_report_renders_status_messages_without_details() -> None:
    top_warning = WarningV1(
        code=WarningCode.LOCAL_STORAGE_FAILED,
        severity=Severity.WARNING,
        message_en="Local search history could not be saved.",
        details={
            "url": "https://internal.example/search",
            "token": "secret-token",
        },
        retryable=False,
    )
    nested_warning = WarningV1(
        code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
        severity=Severity.INFO,
        message_en="Fare details were not collected.",
        details={"headers": "secret-header"},
        retryable=False,
    )
    provider_error = ErrorV1(
        code=ErrorCode.PROVIDER_BLOCKED,
        severity=Severity.ERROR,
        message_en="Traveloka blocked the request.",
        details={"payload": "secret-payload"},
        retryable=True,
    )
    response = _response(
        status=SearchStatus.PARTIAL,
        warnings=[top_warning],
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.PARTIAL,
                planned_call_count=2,
                executed_call_count=1,
                warnings=[nested_warning],
                errors=[provider_error],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "| Traveloka | partial | 1/2 |" in report
    assert "local_storage_failed" in report
    assert "Local search history could not be saved." in report
    assert "fare_details_not_collected" in report
    assert "Fare details were not collected." in report
    assert "provider_blocked" in report
    assert "Traveloka blocked the request." in report
    assert "secret-token" not in report
    assert "secret-header" not in report
    assert "secret-payload" not in report
    assert "https://internal.example" not in report
    assert "headers" not in report
    assert "payload" not in report
```

- [ ] **Step 2: Run formatter tests to verify failure**

Run:

```bash
uv run pytest tests/test_markdown_report.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.markdown_report'`.

- [ ] **Step 3: Implement the Markdown formatter**

Create `cheapy/markdown_report.py` with:

```python
"""Markdown presentation helpers for Cheapy search results."""

from __future__ import annotations

from enum import Enum

from cheapy.models import (
    ErrorV1,
    FlightOfferV1,
    ProviderStatusV1,
    SearchRequestV1,
    SearchResponseV1,
    WarningV1,
)
from cheapy.public_url_safety import validate_public_search_url


def render_search_report(
    request: SearchRequestV1,
    response: SearchResponseV1,
) -> str:
    """Render a Markdown report for one normalized search response."""

    sections: list[str] = [
        _render_header(request),
        "",
        *_render_summary(request, response),
        "",
        *_render_best_offers(response),
    ]

    provider_status = _render_provider_status(response.provider_statuses)
    if provider_status:
        sections.extend(["", *provider_status])

    messages = _render_warning_error_messages(response)
    if messages:
        sections.extend(["", *messages])

    return "\n".join(sections).rstrip() + "\n"


def render_offer_price(offer: FlightOfferV1) -> str:
    """Render fare/provider text, linked only when the public URL is safe."""

    text = (
        f"{_format_amount(offer.price_amount)} {offer.currency} "
        f"on {_provider_label(offer.provider)}"
    )
    public_search_url = offer.public_search_url
    if isinstance(public_search_url, str):
        safe_url = validate_public_search_url(offer.provider, public_search_url)
        if safe_url is not None:
            return f"[{_escape_link_text(text)}]({safe_url})"
    return _escape_table_cell(text)


def _render_header(request: SearchRequestV1) -> str:
    return "## " + " | ".join(
        [
            f"{request.origin} -> {request.destination}",
            _request_dates(request),
            _passengers(request),
            "Economy",
        ]
    )


def _render_summary(
    request: SearchRequestV1,
    response: SearchResponseV1,
) -> list[str]:
    rows = [
        ("Status", _enum_value(response.status)),
        ("Offers", str(len(response.offers))),
        ("Search mode", _enum_value(request.search_mode)),
        ("Providers", _provider_summary(response)),
        ("Mixed currency", "yes" if response.mixed_currency else "no"),
    ]
    if response.currency_notes:
        rows.append(("Currency notes", "; ".join(response.currency_notes)))
    return _render_table(["Summary", "Value"], rows)


def _render_best_offers(response: SearchResponseV1) -> list[str]:
    lines = ["### Best Offers", ""]
    if not response.offers:
        lines.append("No offers returned.")
        return lines

    rows: list[tuple[str, str, str, str, str, str]] = []
    for index, offer in enumerate(response.offers, start=1):
        rows.append(
            (
                str(offer.global_rank or offer.rank_within_currency or index),
                render_offer_price(offer),
                f"{offer.actual_origin} -> {offer.actual_destination}",
                _offer_dates(offer),
                str(offer.stops),
                _duration(offer.total_duration_minutes),
            )
        )
    lines.extend(
        _render_table(["Rank", "Fare", "Route", "Dates", "Stops", "Duration"], rows)
    )
    return lines


def _render_provider_status(
    provider_statuses: list[ProviderStatusV1],
) -> list[str]:
    if not provider_statuses:
        return []

    rows: list[tuple[str, str, str, str]] = []
    for status in provider_statuses:
        notes = [
            f"succeeded: {status.succeeded_call_count}",
            f"failed: {status.failed_call_count}",
            f"retryable: {str(status.retryable).lower()}",
        ]
        notes.extend(_message_note(warning) for warning in status.warnings)
        notes.extend(_message_note(error) for error in status.errors)
        rows.append(
            (
                _provider_label(status.provider_name),
                _enum_value(status.status),
                f"{status.executed_call_count}/{status.planned_call_count}",
                "; ".join(notes),
            )
        )

    return ["### Provider Status", "", *_render_table(["Provider", "Status", "Calls", "Notes"], rows)]


def _render_warning_error_messages(response: SearchResponseV1) -> list[str]:
    rows: list[tuple[str, str, str, str, str]] = []
    rows.extend(
        _message_row("search warning", warning) for warning in response.warnings
    )
    rows.extend(_message_row("search error", error) for error in response.errors)
    for status in response.provider_statuses:
        provider = _provider_label(status.provider_name)
        rows.extend(
            _message_row(f"{provider} warning", warning)
            for warning in status.warnings
        )
        rows.extend(
            _message_row(f"{provider} error", error)
            for error in status.errors
        )
    if not rows:
        return []
    return [
        "### Warnings And Errors",
        "",
        *_render_table(["Scope", "Severity", "Code", "Message", "Retryable"], rows),
    ]


def _message_row(
    scope: str,
    message: WarningV1 | ErrorV1,
) -> tuple[str, str, str, str, str]:
    return (
        scope,
        _enum_value(message.severity),
        _enum_value(message.code),
        message.message_en,
        str(message.retryable).lower(),
    )


def _message_note(message: WarningV1 | ErrorV1) -> str:
    return f"{_enum_value(message.code)}: {message.message_en}"


def _render_table(
    headers: list[str],
    rows: list[tuple[str, ...]],
) -> list[str]:
    return [
        "| " + " | ".join(_escape_table_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *[
            "| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |"
            for row in rows
        ],
    ]


def _request_dates(request: SearchRequestV1) -> str:
    if request.return_date is None:
        return request.departure_date
    return f"{request.departure_date} to {request.return_date}"


def _offer_dates(offer: FlightOfferV1) -> str:
    if offer.actual_return_date is None:
        return offer.actual_departure_date
    return f"{offer.actual_departure_date} to {offer.actual_return_date}"


def _passengers(request: SearchRequestV1) -> str:
    passengers = request.passengers
    parts = [_count_label(passengers.adults, "adult")]
    if passengers.children:
        parts.append(_count_label(passengers.children, "child", plural="children"))
    if passengers.infants_on_lap:
        parts.append(_count_label(passengers.infants_on_lap, "lap infant"))
    if passengers.infants_in_seat:
        parts.append(_count_label(passengers.infants_in_seat, "seat infant"))
    return ", ".join(parts)


def _count_label(count: int, singular: str, *, plural: str | None = None) -> str:
    label = singular if count == 1 else plural or f"{singular}s"
    return f"{count} {label}"


def _provider_summary(response: SearchResponseV1) -> str:
    if not response.provider_statuses:
        return "none"
    return ", ".join(
        f"{status.provider_name}: {_enum_value(status.status)}"
        for status in response.provider_statuses
    )


def _provider_label(provider: str) -> str:
    return provider.replace("_", " ").title()


def _format_amount(amount: float) -> str:
    if float(amount).is_integer():
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"


def _duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _escape_table_cell(value: object) -> str:
    text = " ".join(str(value).split())
    return text.replace("|", "\\|")


def _escape_link_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
```

- [ ] **Step 4: Run formatter tests**

Run:

```bash
uv run pytest tests/test_markdown_report.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit formatter task**

Run:

```bash
git add cheapy/markdown_report.py tests/test_markdown_report.py
git diff --cached --name-status
git commit -m "feat: add markdown search report formatter" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
A	cheapy/markdown_report.py
A	tests/test_markdown_report.py
```

## Task 2: MCP Markdown Content

**Files:**
- Modify: `cheapy/mcp.py`
- Modify: `tests/test_mcp.py`

- [ ] **Step 1: Add failing MCP text-content assertions**

In `tests/test_mcp.py`, update `test_mcp_search_tool_returns_structured_contract_response` after structured assertions:

```python
    text = _text_content(result)
    assert "## CXR -> SGN | 2026-07-10 to 2026-07-15 | 1 adult | Economy" in text
    assert f"[1,280,000 VND on Traveloka]({public_search_url})" in text
    assert text.count(public_search_url) == 1
```

Add this test below it:

```python
def test_mcp_search_tool_keeps_structured_response_when_report_render_fails(
    monkeypatch: Any,
) -> None:
    def fake_search_with_storage(request: Any) -> SearchWithStorageResult:
        response = SearchResponseV1.model_validate(
            {
                "schema_version": "1",
                "status": "success",
                "request_id": (
                    "search:one_way:CXR:SGN:2026-07-10:none:"
                    "exact:1:0:0:0:5"
                ),
                "offers": [],
                "warnings": [],
                "errors": [],
                "provider_statuses": [],
                "search_plan": {
                    "search_mode": "exact",
                    "planned_candidate_count": 1,
                    "executed_candidate_count": 1,
                    "planned_provider_call_count": 1,
                    "executed_provider_call_count": 1,
                    "candidate_count_by_family": {"exact": 1},
                    "provider_call_count_by_family": {"exact": 1},
                    "truncated": False,
                    "truncated_families": [],
                    "candidate_families": ["exact"],
                },
                "mixed_currency": False,
                "currency_groups": [],
                "currency_notes": [],
                "candidates": None,
            }
        )
        return SearchWithStorageResult(
            response=response,
            search_run_id=1,
            storage_enabled=True,
            storage_warning=None,
        )

    def raise_render_error(request: Any, response: Any) -> str:
        raise RuntimeError("secret renderer path")

    monkeypatch.setattr("cheapy.mcp.search_with_storage", fake_search_with_storage)
    monkeypatch.setattr("cheapy.mcp.render_search_report", raise_render_error)
    tool = _mcp_tool()

    result = asyncio.run(
        tool.run(
            {
                "schema_version": "1",
                "origin": "CXR",
                "destination": "SGN",
                "departure_date": "2026-07-10",
            },
            convert_result=True,
        )
    )

    payload = _structured_content(result)
    SearchResponseV1.model_validate(payload)
    text = _text_content(result)
    assert "## Cheapy flight search results" in text
    assert "secret renderer path" not in text
```

- [ ] **Step 2: Run MCP tests to verify failure**

Run:

```bash
uv run pytest tests/test_mcp.py::test_mcp_search_tool_returns_structured_contract_response tests/test_mcp.py::test_mcp_search_tool_keeps_structured_response_when_report_render_fails -v
```

Expected: FAIL because `cheapy.mcp` does not yet return Markdown text content or expose `render_search_report`.

- [ ] **Step 3: Wire Markdown content into MCP**

Modify imports in `cheapy/mcp.py`:

```python
from mcp import types
from mcp.server.fastmcp import FastMCP
```

Add the formatter import:

```python
from cheapy.markdown_report import render_search_report
```

Keep the tool return annotation as `SearchResponseV1` so FastMCP keeps the existing structured output schema. Replace the end of `search_cheapest_flights` with:

```python
        result = await asyncio.to_thread(search_with_storage, request)
        markdown = _safe_render_search_report(request, result.response)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=markdown)],
            structuredContent=result.response.model_dump(mode="json"),
            isError=False,
        )
```

Add this helper near `_request_payload`:

```python
def _safe_render_search_report(
    request: SearchRequestV1,
    response: SearchResponseV1,
) -> str:
    try:
        return render_search_report(request, response)
    except Exception:
        return (
            "## Cheapy flight search results\n\n"
            "Structured results are available in the MCP response.\n"
        )
```

- [ ] **Step 4: Run MCP tests**

Run:

```bash
uv run pytest tests/test_mcp.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit MCP task**

Run:

```bash
git add cheapy/mcp.py tests/test_mcp.py
git diff --cached --name-status
git commit -m "feat: return markdown report in mcp content" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
M	cheapy/mcp.py
M	tests/test_mcp.py
```

## Task 3: CLI Markdown Flags

**Files:**
- Modify: `cheapy/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI Markdown tests**

In `tests/test_cli.py`, add this test after `test_history_show_json_keeps_public_search_url_out_of_observations`:

```python
def test_history_show_markdown_prints_search_report(tmp_path, monkeypatch) -> None:
    public_search_url = (
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
        "ap=CXR.SGN&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
    )
    request = SearchRequestV1.model_validate(
        {
            "schema_version": "1",
            "origin": "CXR",
            "destination": "SGN",
            "departure_date": "2026-07-10",
            "return_date": None,
            "max_results": 5,
            "passengers": {
                "adults": 2,
                "children": 1,
                "infants_on_lap": 0,
                "infants_in_seat": 0,
            },
        }
    )
    response = _cli_response(
        offers=[
            _offer(
                offer_id="traveloka:CXR-SGN:2026-07-10:1",
                provider="traveloka",
                price_amount=4_920_000.0,
                public_search_url=public_search_url,
            )
        ],
        provider_statuses=[_provider_status(provider_name="traveloka")],
    )
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, request, response)

    result = runner.invoke(app, ["history", "show", str(run_id), "--markdown"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.startswith("## CXR -> SGN | 2026-07-10 | 2 adults, 1 child | Economy")
    assert f"[4,920,000 VND on Traveloka]({public_search_url})" in result.stdout
    assert result.stdout.count(public_search_url) == 1
    assert "public_search_url" not in result.stdout
```

Add this test after `test_watchlist_check_runs_search_records_check_and_prints_decision`:

```python
def test_watchlist_check_markdown_prints_search_report(
    tmp_path,
    monkeypatch,
) -> None:
    public_search_url = (
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
        "ap=CXR.SGN&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
    )
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        watchlist = storage.add_watchlist(
            conn,
            name="CXR SGN",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            return_date=None,
            max_price_amount=5_000_000.0,
            currency="VND",
            max_stops=0,
            max_results=5,
        )

    fresh_run_id = None

    def fake_search_with_storage(request):
        nonlocal fresh_run_id
        fresh_response = _cli_response(
            offers=[
                _offer(
                    offer_id="traveloka:CXR-SGN:2026-07-10:1",
                    provider="traveloka",
                    price_amount=4_920_000.0,
                    public_search_url=public_search_url,
                )
            ],
            provider_statuses=[_provider_status(provider_name="traveloka")],
        )
        with storage.open_database() as conn:
            fresh_run_id = storage.insert_search_snapshot(conn, request, fresh_response)
        return SearchWithStorageResult(
            response=fresh_response,
            search_run_id=fresh_run_id,
            storage_enabled=True,
            storage_warning=None,
        )

    monkeypatch.setattr("cheapy.cli.search_with_storage", fake_search_with_storage)

    result = runner.invoke(
        app,
        ["watchlist", "check", str(watchlist["id"]), "--markdown"],
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.startswith("## CXR -> SGN | 2026-07-10 | 1 adult | Economy")
    assert f"[4,920,000 VND on Traveloka]({public_search_url})" in result.stdout
    assert '"decision"' not in result.stdout
    assert fresh_run_id is not None
```

- [ ] **Step 2: Run CLI Markdown tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_history_show_markdown_prints_search_report tests/test_cli.py::test_watchlist_check_markdown_prints_search_report -v
```

Expected: FAIL because the `--markdown` options do not exist.

- [ ] **Step 3: Add CLI Markdown helpers and options**

Modify imports in `cheapy/cli.py`:

```python
from cheapy.markdown_report import render_search_report
from cheapy.models import ProviderStatusCode, SearchRequestV1, SearchResponseV1
```

Add this helper near `_watchlist_check_rationale`:

```python
def _safe_render_search_report(
    request: SearchRequestV1,
    response: SearchResponseV1,
) -> str:
    try:
        return render_search_report(request, response)
    except Exception:
        return (
            "## Cheapy flight search results\n\n"
            "Structured results are available in the JSON response.\n"
        )


def _history_request(payload: dict[str, Any]) -> SearchRequestV1:
    search_run = payload["search_run"]
    passengers = json.loads(search_run["passengers_json"])
    return SearchRequestV1.model_validate(
        {
            "schema_version": search_run["schema_version"],
            "origin": search_run["origin"],
            "destination": search_run["destination"],
            "departure_date": search_run["departure_date"],
            "return_date": search_run["return_date"],
            "search_mode": search_run["search_mode"],
            "passengers": passengers,
            "max_results": search_run["max_results"],
        }
    )
```

Update `history_show` signature:

```python
def history_show(
    run_id: int = typer.Argument(..., help="Search run id."),
    markdown: bool = typer.Option(
        False,
        "--markdown",
        help="Print a Markdown search report.",
    ),
) -> None:
```

After the `payload is None` block and before `_json_echo({"status": "ok", **payload})`, add:

```python
    if markdown:
        try:
            request = _history_request(payload)
            response = SearchResponseV1.model_validate(payload["response"])
        except (KeyError, TypeError, ValueError, ValidationError):
            _history_storage_error_exit()
        typer.echo(_safe_render_search_report(request, response), nl=False)
        return
```

Update `watchlist_check` signature:

```python
def watchlist_check(
    watchlist_id: int = typer.Argument(..., help="Watchlist id."),
    markdown: bool = typer.Option(
        False,
        "--markdown",
        help="Print a Markdown search report.",
    ),
) -> None:
```

Before the final `_json_echo(...)` in `watchlist_check`, add:

```python
    if markdown:
        typer.echo(_safe_render_search_report(request, result.response), nl=False)
        return
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit CLI task**

Run:

```bash
git add cheapy/cli.py tests/test_cli.py
git diff --cached --name-status
git commit -m "feat: add markdown cli reports" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
M	cheapy/cli.py
M	tests/test_cli.py
```

## Task 4: Final Verification

**Files:**
- Verify: `cheapy/markdown_report.py`
- Verify: `cheapy/mcp.py`
- Verify: `cheapy/cli.py`
- Verify: `tests/test_markdown_report.py`
- Verify: `tests/test_mcp.py`
- Verify: `tests/test_cli.py`

- [ ] **Step 1: Run targeted verification**

Run:

```bash
uv run pytest tests/test_markdown_report.py tests/test_mcp.py tests/test_cli.py tests/test_contracts.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS. If live-marked tests are skipped by default, that is acceptable.

- [ ] **Step 3: Check for raw URL and Browserless regressions in touched code**

Run:

```bash
rg -n "browserless|transport_deeplink|/api/v2/flight/search|public_search_url" cheapy/markdown_report.py cheapy/mcp.py cheapy/cli.py tests/test_markdown_report.py tests/test_mcp.py tests/test_cli.py
```

Expected:

- `browserless`, `transport_deeplink`, and `/api/v2/flight/search` do not appear in touched report/MCP/CLI implementation.
- `public_search_url` appears only in tests and in formatter link-validation logic, never as rendered report text.

- [ ] **Step 4: Confirm final worktree state**

Run:

```bash
git status --short --branch
git log --oneline -5
```

Expected: implementation commits are on `codex/local-sqlite-history-watchlist`, with only intentional files changed.
