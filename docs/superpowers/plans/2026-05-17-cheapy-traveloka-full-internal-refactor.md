# Cheapy Traveloka Full Internal Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Traveloka provider internals into explicit session, workflow, normalization, and test ownership boundaries while preserving Contract V1 behavior.

**Architecture:** Keep `TravelokaProvider` as the only `ProviderResult` boundary and make `TravelokaAdapter` a sync facade over `session.py` and `workflow.py`. Split `normalizer.py` into a `normalization/` package, migrate tests by module ownership, and require a fallback-removal catalog for every deleted parser or DOM fallback.

**Tech Stack:** Python 3.12, pytest, uv, CloakBrowser/Playwright-style sync browser API, Cheapy Contract V1 Pydantic models.

---

## File Structure

- Create `cheapy/providers/traveloka/session.py`: browser/context/page lifecycle, response handler registration, navigation, phase timing, cleanup.
- Create `cheapy/providers/traveloka/workflow.py`: exact one-way and selected round-trip workflow orchestration.
- Modify `cheapy/providers/traveloka/adapter.py`: facade that validates config and delegates to workflow functions.
- Create `cheapy/providers/traveloka/normalization/__init__.py`: package marker and entrypoint exports.
- Create `cheapy/providers/traveloka/normalization/entrypoints.py`: `normalize_payload` and `normalize_selected_round_trip`.
- Create `cheapy/providers/traveloka/normalization/payloads.py`: payload item discovery and list-at-path helpers.
- Create `cheapy/providers/traveloka/normalization/canonical.py`: Traveloka `searchResults` canonicalization.
- Create `cheapy/providers/traveloka/normalization/legs.py`: segment-to-`FlightLegV1` parsing.
- Create `cheapy/providers/traveloka/normalization/routes.py`: route/date validation, stops, total duration.
- Create `cheapy/providers/traveloka/normalization/selected.py`: selected round-trip item lookup and offer creation.
- Create `cheapy/providers/traveloka/normalization/ranking.py`: comparable and non-comparable offer ranking.
- Create `cheapy/providers/traveloka/normalization/errors.py`: normalizer-local `ErrorV1` factories.
- Modify `cheapy/providers/traveloka/normalizer.py`: temporary compatibility shim after entrypoints move.
- Modify `cheapy/providers/traveloka/provider.py`: import normalizer entrypoints from the new package after compatibility tests pass.
- Create `tests/traveloka/`: module-owned Traveloka test package.
- Move/split `tests/test_traveloka_adapter.py`: into module-owned test files.
- Move/split `tests/test_traveloka_normalizer.py`: into normalization-owned test files.
- Move `tests/test_traveloka_provider.py`: to `tests/traveloka/test_provider.py`.
- Create `scripts/benchmark_traveloka_live_matrix.py`: opt-in live matrix runner and JSONL result recorder.
- Create `tests/traveloka/test_live_matrix_script.py`: offline tests for live matrix record formatting.

## Fallback Removal Catalog Format

Every task that deletes a parser or DOM fallback must include a catalog entry in
that task's commit message or review notes. Use this exact shape:

```markdown
Fallback removal catalog:
- Removed path: `module.function` or selector/branch name
- Old coverage: `tests/path.py::test_name`
- Runtime-path analysis: why current one-way or selected round-trip flow does not need it
- Replacement coverage: `tests/path.py::test_name`, or a concrete sentence such as "No replacement because current selected flow uses only inventory-card payload IDs."
- Public behavior safety: offers, partial results, failure types, and sensitive-data safety preserved
```

If any field cannot be filled with concrete evidence, move the fallback into the
new module boundary instead of deleting it.

## Live Matrix Record Format

The live matrix script must write JSON Lines. Each line must include exactly
these keys:

```json
{
  "run_label": "baseline",
  "origin": "SGN",
  "destination": "BKK",
  "departure_date": "2026-06-12",
  "return_date": "2026-06-17",
  "status": "success",
  "offer_count": 1,
  "comparable_offer_count": 1,
  "failure_types": [],
  "duration_ms": 25000
}
```

`run_label` must be either `baseline` or `refactored`.

## Task 1: Baseline And Guardrails

**Files:**
- Read: `docs/superpowers/specs/2026-05-17-cheapy-traveloka-full-internal-refactor-design.md`
- Read: `cheapy/providers/traveloka/adapter.py`
- Read: `cheapy/providers/traveloka/normalizer.py`
- Read: `tests/test_traveloka_adapter.py`
- Read: `tests/test_traveloka_normalizer.py`
- Read: `tests/test_traveloka_provider.py`

- [ ] **Step 1: Confirm only expected dirty files exist**

Run:

```bash
git status --short
```

Expected: only pre-existing unrelated deletions may appear before implementation starts:

```text
 D Cheapy_PROJECT_STARTER_PROMPT.md
 D "cheapy verdict report.md"
```

If additional implementation files are dirty, inspect them before editing and do not overwrite user changes.

- [ ] **Step 2: Run Traveloka baseline tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS. Stop before refactoring if this command fails.

- [ ] **Step 3: Run search integration baseline**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS. Stop before refactoring if this command fails.

- [ ] **Step 4: Commit nothing**

No files should change in this task. If `git status --short` shows changes made by this task, inspect and revert only those changes.

## Task 2: Browser Session Abstraction

**Files:**
- Create: `cheapy/providers/traveloka/session.py`
- Create: `tests/traveloka/test_session.py`
- Modify only if imports require it: `tests/traveloka/__init__.py`

- [ ] **Step 1: Write failing session tests**

Create `tests/traveloka/test_session.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

import pytest

from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka import session as traveloka_session
from cheapy.providers.traveloka.errors import TravelokaProviderError
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


class FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.goto_calls: list[dict[str, object]] = []

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append(
            {"url": url, "wait_until": wait_until, "timeout": timeout}
        )


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    def new_context(self, **kwargs: object) -> FakeContext:
        assert kwargs == {"locale": "en-US"}
        return self.context

    def close(self) -> None:
        self.closed = True


def test_open_browser_session_sets_up_page_and_cleans_up() -> None:
    page = FakePage()
    context = FakeContext(page)
    browser = FakeBrowser(context)
    launches: list[dict[str, object]] = []

    def launch_browser(**kwargs: object) -> FakeBrowser:
        launches.append(kwargs)
        return browser

    recorder = TravelokaPhaseRecorder(clock=monotonic)

    with traveloka_session.open_browser_session(
        _request(),
        base_url="https://www.traveloka.com",
        timeout_seconds=5.0,
        launch_browser=launch_browser,
        phase_recorder=recorder,
    ) as opened:
        assert opened.page is page
        assert opened.state.best_result is None
        assert opened.deadline > monotonic()
        assert "response" in page.handlers
        assert page.goto_calls
        assert "airportFrom=SGN" in page.goto_calls[0]["url"]

    assert context.closed is True
    assert browser.closed is True
    assert launches and launches[0]["headless"] is True
    assert {record.phase for record in recorder.records} >= {
        "browser_launch",
        "context_page_setup",
        "initial_navigation",
        "cleanup",
    }


def test_open_browser_session_maps_launch_failure_to_provider_error() -> None:
    def fail_launch(**kwargs: object) -> object:
        raise RuntimeError("browser not installed")

    with pytest.raises(TravelokaProviderError) as exc_info:
        with traveloka_session.open_browser_session(
            _request(),
            base_url="https://www.traveloka.com",
            timeout_seconds=1.0,
            launch_browser=fail_launch,
            phase_recorder=TravelokaPhaseRecorder(clock=monotonic),
        ):
            raise AssertionError("session should not open")

    assert exc_info.value.failure_type == "browser_unavailable"
    assert exc_info.value.exception_type == "RuntimeError"
```

- [ ] **Step 2: Run session tests and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_session.py -v
```

Expected: FAIL with `ImportError` or `ModuleNotFoundError` because `cheapy.providers.traveloka.session` does not exist.

- [ ] **Step 3: Implement `session.py`**

Create `cheapy/providers/traveloka/session.py`:

```python
"""Browser session lifecycle for the Traveloka research provider."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import urls as traveloka_urls
from cheapy.providers.traveloka.browser_helpers import close_quietly, remaining_timeout_ms
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
BrowserLauncher = Callable[..., object]


@dataclass(frozen=True)
class TravelokaBrowserSession:
    page: object
    state: traveloka_capture.CaptureState
    deadline: float


@contextmanager
def open_browser_session(
    request: ProviderRequest,
    *,
    base_url: str,
    timeout_seconds: float,
    launch_browser: BrowserLauncher,
    phase_recorder: TravelokaPhaseRecorder,
) -> Iterator[TravelokaBrowserSession]:
    browser: object | None = None
    context: object | None = None
    state = traveloka_capture.CaptureState()
    deadline = monotonic() + timeout_seconds
    try:
        try:
            with phase_recorder.phase("browser_launch"):
                browser = launch_browser(
                    headless=True,
                    timeout=remaining_timeout_ms(deadline),
                )
        except Exception as exc:
            if traveloka_errors.is_timeout_exception(exc):
                raise traveloka_errors.timeout_error(type(exc).__name__) from None
            raise traveloka_errors.browser_unavailable_error(
                type(exc).__name__
            ) from None

        with phase_recorder.phase("context_page_setup"):
            remaining_timeout_ms(deadline)
            context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
            remaining_timeout_ms(deadline)
            page = context.new_page()  # type: ignore[attr-defined]
            remaining_timeout_ms(deadline)
            page.on("response", state.handle_response)  # type: ignore[attr-defined]

        with phase_recorder.phase("initial_navigation"):
            remaining_timeout_ms(deadline)
            page.goto(  # type: ignore[attr-defined]
                traveloka_urls.build_full_search_url(request, base_url=base_url),
                wait_until="domcontentloaded",
                timeout=remaining_timeout_ms(deadline),
            )

        yield TravelokaBrowserSession(page=page, state=state, deadline=deadline)
    finally:
        with phase_recorder.phase("cleanup"):
            close_quietly(context)
            close_quietly(browser)
```

- [ ] **Step 4: Run session tests**

Run:

```bash
uv run pytest tests/traveloka/test_session.py -v
```

Expected: PASS.

- [ ] **Step 5: Run existing adapter tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: PASS because `adapter.py` has not changed yet.

- [ ] **Step 6: Commit session abstraction**

Run:

```bash
git add cheapy/providers/traveloka/session.py tests/traveloka/test_session.py
git commit -m "refactor: add Traveloka browser session boundary" -m "Model: GPT-5 Codex"
```

## Task 3: Exact One-Way Workflow Extraction

**Files:**
- Create: `cheapy/providers/traveloka/workflow.py`
- Create: `tests/traveloka/test_workflow.py`
- Modify: `cheapy/providers/traveloka/adapter.py`

- [ ] **Step 1: Write failing one-way workflow test**

Create `tests/traveloka/test_workflow.py` with this starting content:

```python
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka import workflow as traveloka_workflow
from cheapy.providers.traveloka.capture import CaptureState
from cheapy.providers.traveloka.results import TravelokaCaptureResult
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


@dataclass(frozen=True)
class FakeSession:
    page: object
    state: CaptureState
    deadline: float


class FakePage:
    def __init__(self) -> None:
        self.content_calls = 0

    def content(self) -> str:
        self.content_calls += 1
        return "<html></html>"


def test_search_exact_one_way_waits_for_outbound_capture(monkeypatch) -> None:
    page = FakePage()
    state = CaptureState()
    expected = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    captured: dict[str, object] = {}

    @contextmanager
    def fake_open_session(*args: object, **kwargs: object):
        captured["open_args"] = args
        captured["open_kwargs"] = kwargs
        yield FakeSession(page=page, state=state, deadline=123.0)

    def fake_wait_for_capture(
        state_arg: CaptureState,
        page_arg: object,
        deadline_arg: float,
        *,
        poll_interval_seconds: float,
    ) -> TravelokaCaptureResult:
        captured["state"] = state_arg
        captured["page"] = page_arg
        captured["deadline"] = deadline_arg
        captured["poll_interval_seconds"] = poll_interval_seconds
        return expected

    monkeypatch.setattr(traveloka_workflow, "open_browser_session", fake_open_session)
    monkeypatch.setattr(
        traveloka_workflow.traveloka_capture,
        "wait_for_capture",
        fake_wait_for_capture,
    )

    result = traveloka_workflow.search_exact_one_way(
        _one_way_request(),
        base_url="https://www.traveloka.com",
        timeout_seconds=5.0,
        poll_interval_seconds=0.25,
        launch_browser=lambda **kwargs: object(),
        phase_recorder=TravelokaPhaseRecorder(),
    )

    assert result is expected
    assert captured["state"] is state
    assert captured["page"] is page
    assert captured["deadline"] == 123.0
    assert captured["poll_interval_seconds"] == 0.25
```

- [ ] **Step 2: Run the one-way workflow test and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_workflow.py::test_search_exact_one_way_waits_for_outbound_capture -v
```

Expected: FAIL because `cheapy.providers.traveloka.workflow` does not exist.

- [ ] **Step 3: Implement one-way workflow**

Create `cheapy/providers/traveloka/workflow.py`:

```python
"""High-level Traveloka browser workflows."""

from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
from cheapy.providers.traveloka.session import BrowserLauncher, open_browser_session
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def search_exact_one_way(
    request: ProviderExactOneWayRequest,
    *,
    base_url: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    launch_browser: BrowserLauncher,
    phase_recorder: TravelokaPhaseRecorder,
) -> TravelokaCaptureResult:
    with open_browser_session(
        request,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        launch_browser=launch_browser,
        phase_recorder=phase_recorder,
    ) as session:
        try:
            with phase_recorder.phase("outbound_capture_wait"):
                return traveloka_capture.wait_for_capture(
                    session.state,
                    session.page,
                    session.deadline,
                    poll_interval_seconds=poll_interval_seconds,
                )
        except traveloka_errors.TravelokaProviderError as exc:
            if exc.failure_type == "timeout":
                traveloka_errors.raise_blocked_if_terminal_page(
                    session.page.content()  # type: ignore[attr-defined]
                )
            raise
```

- [ ] **Step 4: Run workflow test**

Run:

```bash
uv run pytest tests/traveloka/test_workflow.py::test_search_exact_one_way_waits_for_outbound_capture -v
```

Expected: PASS.

- [ ] **Step 5: Delegate `TravelokaAdapter._search` to workflow**

In `cheapy/providers/traveloka/adapter.py`, import workflow:

```python
from cheapy.providers.traveloka import workflow as traveloka_workflow
```

Replace `_search` body with:

```python
    def _search(self, request: ProviderRequest) -> TravelokaCaptureResult:
        try:
            return traveloka_workflow.search_exact_one_way(
                request,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                poll_interval_seconds=self._poll_interval_seconds,
                launch_browser=self._launch_browser,
                phase_recorder=self._phase_recorder,
            )
        except traveloka_errors.TravelokaProviderError:
            raise
        except Exception as exc:
            if traveloka_errors.is_timeout_exception(exc):
                raise traveloka_errors.timeout_error(type(exc).__name__) from None
            raise traveloka_errors.navigation_failed_error(type(exc).__name__) from None
```

Keep the adapter-level unexpected exception mapping so public behavior stays unchanged.

- [ ] **Step 6: Run one-way and adapter tests**

Run:

```bash
uv run pytest tests/traveloka/test_workflow.py::test_search_exact_one_way_waits_for_outbound_capture tests/test_traveloka_adapter.py::test_adapter_captures_completed_initial_fare_payload tests/test_traveloka_adapter.py::test_adapter_raises_timeout_when_no_fare_payload_arrives -v
```

Expected: PASS.

- [ ] **Step 7: Run full adapter tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit one-way workflow extraction**

Run:

```bash
git add cheapy/providers/traveloka/workflow.py cheapy/providers/traveloka/adapter.py tests/traveloka/test_workflow.py
git commit -m "refactor: extract Traveloka one-way workflow" -m "Model: GPT-5 Codex"
```

## Task 4: Selected Round-Trip Workflow Extraction

**Files:**
- Modify: `cheapy/providers/traveloka/workflow.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/traveloka/test_workflow.py`

- [ ] **Step 1: Add selected workflow success test**

Append to `tests/traveloka/test_workflow.py`:

```python
from decimal import Decimal

from cheapy.providers.traveloka.inventory import TravelokaVisibleOption


class ClickableLocator:
    def __init__(self) -> None:
        self.clicked = False

    def evaluate(self, script: str, **kwargs: object) -> None:
        self.clicked = True

    def click(self, **kwargs: object) -> None:
        self.clicked = True


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def test_search_selected_round_trip_builds_selected_result(monkeypatch) -> None:
    page = FakePage()
    state = CaptureState()
    outbound = TravelokaCaptureResult(
        payload={"data": {"searchResults": [{"id": "out-1"}]}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    returning = TravelokaCaptureResult(
        payload={"data": {"searchResults": [{"id": "ret-1"}]}},
        source_path="/api/v2/flight/search/poll",
        search_completed=True,
    )
    captures = [outbound, returning]
    options = [
        TravelokaVisibleOption(
            key="out-1",
            airline_name="VJ",
            departure_time_text=None,
            arrival_time_text=None,
            route_text=None,
            price_amount=Decimal("100"),
            currency="USD",
            locator=ClickableLocator(),
        ),
        TravelokaVisibleOption(
            key="ret-1",
            airline_name="VJ",
            departure_time_text=None,
            arrival_time_text=None,
            route_text=None,
            price_amount=Decimal("200"),
            currency="USD",
            locator=ClickableLocator(),
        ),
    ]

    @contextmanager
    def fake_open_session(*args: object, **kwargs: object):
        yield FakeSession(page=page, state=state, deadline=123.0)

    def fake_wait_for_capture(*args: object, **kwargs: object) -> TravelokaCaptureResult:
        return captures.pop(0)

    def fake_visible_options_from_page(*args: object, **kwargs: object) -> list[TravelokaVisibleOption]:
        return [options.pop(0)]

    monkeypatch.setattr(traveloka_workflow, "open_browser_session", fake_open_session)
    monkeypatch.setattr(
        traveloka_workflow.traveloka_capture,
        "wait_for_capture",
        fake_wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_inventory,
        "visible_options_from_page",
        fake_visible_options_from_page,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_selection,
        "wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_selection,
        "wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_totals,
        "final_total_texts",
        lambda *args, **kwargs: (),
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_totals,
        "wait_for_final_total",
        lambda *args, **kwargs: (Decimal("321.09"), "USD"),
    )

    result = traveloka_workflow.search_selected_round_trip(
        _round_trip_request(),
        base_url="https://www.traveloka.com",
        timeout_seconds=5.0,
        poll_interval_seconds=0.25,
        launch_browser=lambda **kwargs: object(),
        phase_recorder=TravelokaPhaseRecorder(),
    )

    assert result.selected_outbound_key == "out-1"
    assert result.selected_return_key == "ret-1"
    assert result.final_total_amount == Decimal("321.09")
    assert result.final_total_currency == "USD"
```

- [ ] **Step 2: Run selected workflow success test and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_workflow.py::test_search_selected_round_trip_builds_selected_result -v
```

Expected: FAIL because `search_selected_round_trip` is missing.

- [ ] **Step 3: Move selected round-trip workflow into `workflow.py`**

Move the body of `TravelokaAdapter._search_selected_round_trip` from
`cheapy/providers/traveloka/adapter.py` into a new function:

The new function signature is:

```python
def search_selected_round_trip(
    request: ProviderExactRoundTripRequest,
    *,
    base_url: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    launch_browser: BrowserLauncher,
    phase_recorder: TravelokaPhaseRecorder,
) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
```

Apply these replacements inside the moved code:

- replace browser/context/page setup with `with open_browser_session(request, base_url=base_url, timeout_seconds=timeout_seconds, launch_browser=launch_browser, phase_recorder=phase_recorder) as session:`
- replace `page` with `session.page`
- replace `state` with `session.state`
- replace `deadline` with `session.deadline`
- replace `self._phase_recorder` with `phase_recorder`
- replace `self._poll_interval_seconds` with `poll_interval_seconds`
- remove the `finally` cleanup block because `session.py` owns cleanup

Add these imports to `workflow.py`:

```python
from dataclasses import dataclass

from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
from cheapy.providers.traveloka.browser_helpers import read_body_text, remaining_timeout_ms
from cheapy.providers.traveloka.results import partial_round_trip_result
```

Add this dataclass near the top of `workflow.py` and use it to hold selected
workflow stage state when it makes the moved code clearer:

```python
@dataclass(frozen=True)
class RoundTripWorkflowState:
    outbound_capture: TravelokaCaptureResult
    return_capture: TravelokaCaptureResult | None = None
    selected_outbound_key: str | None = None
    selected_return_key: str | None = None
```

- [ ] **Step 4: Delegate adapter round-trip method to workflow**

Replace `TravelokaAdapter._search_selected_round_trip` with:

```python
    def _search_selected_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        try:
            return traveloka_workflow.search_selected_round_trip(
                request,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                poll_interval_seconds=self._poll_interval_seconds,
                launch_browser=self._launch_browser,
                phase_recorder=self._phase_recorder,
            )
        except traveloka_errors.TravelokaProviderError:
            raise
        except Exception as exc:
            if traveloka_errors.is_timeout_exception(exc):
                raise traveloka_errors.timeout_error(type(exc).__name__) from None
            raise traveloka_errors.navigation_failed_error(type(exc).__name__) from None
```

- [ ] **Step 5: Run selected workflow success test**

Run:

```bash
uv run pytest tests/traveloka/test_workflow.py::test_search_selected_round_trip_builds_selected_result -v
```

Expected: PASS.

- [ ] **Step 6: Run selected round-trip adapter regression tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_round_trip_selects_cheapest_visible_outbound_and_return tests/test_traveloka_adapter.py::test_round_trip_returns_partial_when_final_total_is_unavailable tests/test_traveloka_adapter.py::test_round_trip_returns_return_capture_partial_when_return_capture_is_timed_out -v
```

Expected: PASS.

- [ ] **Step 7: Run full Traveloka adapter and workflow tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/traveloka/test_workflow.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit selected workflow extraction**

Run:

```bash
git add cheapy/providers/traveloka/workflow.py cheapy/providers/traveloka/adapter.py tests/traveloka/test_workflow.py
git commit -m "refactor: extract Traveloka selected workflow" -m "Model: GPT-5 Codex"
```

## Task 5: Adapter Facade Tightening

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/traveloka/test_workflow.py`
- Create: `tests/traveloka/test_adapter.py`

- [ ] **Step 1: Write adapter facade delegation tests**

Create `tests/traveloka/test_adapter.py`:

```python
from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka import workflow as traveloka_workflow
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.results import TravelokaCaptureResult


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def _capture() -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )


def test_adapter_delegates_one_way_to_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_search_exact_one_way(request: object, **kwargs: object) -> TravelokaCaptureResult:
        seen["request"] = request
        seen["kwargs"] = kwargs
        return _capture()

    monkeypatch.setattr(
        traveloka_workflow,
        "search_exact_one_way",
        fake_search_exact_one_way,
    )

    adapter = TravelokaAdapter(
        base_url="https://example.test",
        timeout_seconds=7,
        poll_interval_seconds=0.5,
        launch_browser=lambda **kwargs: object(),
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.search_completed is True
    assert seen["request"] == _one_way_request()
    assert seen["kwargs"]["base_url"] == "https://example.test"
    assert seen["kwargs"]["timeout_seconds"] == 7
    assert seen["kwargs"]["poll_interval_seconds"] == 0.5


def test_adapter_delegates_round_trip_to_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_search_selected_round_trip(request: object, **kwargs: object) -> TravelokaCaptureResult:
        seen["request"] = request
        seen["kwargs"] = kwargs
        return _capture()

    monkeypatch.setattr(
        traveloka_workflow,
        "search_selected_round_trip",
        fake_search_selected_round_trip,
    )

    adapter = TravelokaAdapter(timeout_seconds=7, launch_browser=lambda **kwargs: object())

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert result.search_completed is True
    assert seen["request"] == _round_trip_request()
    assert seen["kwargs"]["timeout_seconds"] == 7
```

- [ ] **Step 2: Run adapter facade tests**

Run:

```bash
uv run pytest tests/traveloka/test_adapter.py -v
```

Expected: PASS after Tasks 3 and 4.

- [ ] **Step 3: Remove now-unused adapter imports**

In `cheapy/providers/traveloka/adapter.py`, remove imports that are now used
only by `workflow.py` or `session.py`, including:

```python
from time import monotonic as _monotonic
from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
from cheapy.providers.traveloka.browser_helpers import close_quietly, read_body_text, remaining_timeout_ms
from cheapy.providers.traveloka.results import partial_round_trip_result
```

Keep imports needed by the facade:

```python
from typing import Callable

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import urls as traveloka_urls
from cheapy.providers.traveloka import workflow as traveloka_workflow
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder, TravelokaPhaseTiming
```

- [ ] **Step 4: Verify adapter size and behavior**

Run:

```bash
wc -l cheapy/providers/traveloka/adapter.py
uv run pytest tests/traveloka/test_adapter.py tests/test_traveloka_adapter.py -v
```

Expected: adapter line count is meaningfully smaller than the pre-refactor 395 lines, and tests PASS.

- [ ] **Step 5: Commit facade tightening**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py tests/traveloka/test_adapter.py
git commit -m "refactor: slim Traveloka adapter facade" -m "Model: GPT-5 Codex"
```

## Task 6: Normalization Package Entry Points

**Files:**
- Create: `cheapy/providers/traveloka/normalization/__init__.py`
- Create: `cheapy/providers/traveloka/normalization/entrypoints.py`
- Modify: `cheapy/providers/traveloka/provider.py`
- Create: `tests/traveloka/test_normalization_entrypoints.py`

- [ ] **Step 1: Write failing normalization package test**

Create `tests/traveloka/test_normalization_entrypoints.py`:

```python
from __future__ import annotations

from cheapy.providers.traveloka.normalization.entrypoints import (
    normalize_payload,
    normalize_selected_round_trip,
)


def test_normalization_entrypoints_are_importable() -> None:
    assert callable(normalize_payload)
    assert callable(normalize_selected_round_trip)
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_entrypoints.py -v
```

Expected: FAIL because `cheapy.providers.traveloka.normalization` does not exist.

- [ ] **Step 3: Create temporary entrypoint package**

Create `cheapy/providers/traveloka/normalization/__init__.py`:

```python
"""Traveloka payload normalization package."""

from cheapy.providers.traveloka.normalization.entrypoints import (
    normalize_payload,
    normalize_selected_round_trip,
)

__all__ = ["normalize_payload", "normalize_selected_round_trip"]
```

Create `cheapy/providers/traveloka/normalization/entrypoints.py`:

```python
"""Public Traveloka normalization entrypoints."""

from __future__ import annotations

from cheapy.providers.traveloka.normalizer import (
    normalize_payload,
    normalize_selected_round_trip,
)

__all__ = ["normalize_payload", "normalize_selected_round_trip"]
```

- [ ] **Step 4: Update provider to import new entrypoints**

In `cheapy/providers/traveloka/provider.py`, replace:

```python
from cheapy.providers.traveloka.normalizer import (
    normalize_payload,
    normalize_selected_round_trip,
)
```

with:

```python
from cheapy.providers.traveloka.normalization import (
    normalize_payload,
    normalize_selected_round_trip,
)
```

- [ ] **Step 5: Run normalization and provider tests**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_entrypoints.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit entrypoint package**

Run:

```bash
git add cheapy/providers/traveloka/normalization tests/traveloka/test_normalization_entrypoints.py cheapy/providers/traveloka/provider.py
git commit -m "refactor: add Traveloka normalization package entrypoints" -m "Model: GPT-5 Codex"
```

## Task 7: Extract Normalization Ranking And Errors

**Files:**
- Create: `cheapy/providers/traveloka/normalization/ranking.py`
- Create: `cheapy/providers/traveloka/normalization/errors.py`
- Modify: `cheapy/providers/traveloka/normalizer.py`
- Create: `tests/traveloka/test_normalization_ranking.py`
- Create: `tests/traveloka/test_normalization_errors.py`

- [ ] **Step 1: Write failing ranking and error ownership tests**

Create `tests/traveloka/test_normalization_ranking.py`:

```python
from __future__ import annotations

from cheapy.models import FlightOfferV1, OfferFlagsV1
from cheapy.providers.traveloka.normalization.ranking import rank_offers


def _offer(*, comparable: bool, price: float, offer_id: str) -> FlightOfferV1:
    return FlightOfferV1(
        offer_id=offer_id,
        price_amount=price,
        currency="USD",
        comparable=comparable,
        rank_within_currency=1 if comparable else None,
        global_rank=1 if comparable else None,
        provider="traveloka",
        requested_origin="SGN",
        requested_destination="BKK",
        actual_origin="SGN",
        actual_destination="BKK",
        nearby_origin_distance_km=None,
        nearby_destination_distance_km=None,
        requested_departure_date="2026-07-10",
        actual_departure_date="2026-07-10",
        departure_offset_days=0,
        requested_return_date=None,
        actual_return_date=None,
        return_offset_days=None,
        legs=[],
        total_duration_minutes=0,
        stops=0,
        flags=OfferFlagsV1(
            uses_flexible_departure_date=False,
            uses_flexible_return_date=False,
        ),
        fare_details_status="not_collected",
    )


def test_rank_offers_clears_rank_for_non_comparable_offer() -> None:
    ranked = rank_offers([_offer(comparable=False, price=10, offer_id="b")])

    assert ranked[0].comparable is False
    assert ranked[0].rank_within_currency is None
    assert ranked[0].global_rank is None
```

Create `tests/traveloka/test_normalization_errors.py`:

```python
from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka.normalization.errors import parse_error


def test_parse_error_uses_safe_exception_type_only() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )

    error = parse_error(3, request, ValueError("contains raw payload"))

    assert error.details["provider"] == "traveloka"
    assert error.details["failure_type"] == "parse_error"
    assert error.details["item_index"] == 3
    assert error.details["exception_type"] == "ValueError"
    assert "raw payload" not in error.message_en
```

- [ ] **Step 2: Run ownership tests and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_ranking.py tests/traveloka/test_normalization_errors.py -v
```

Expected: FAIL because `normalization.ranking` and `normalization.errors` are missing.

- [ ] **Step 3: Extract ranking functions**

Create `cheapy/providers/traveloka/normalization/ranking.py` by moving
`_rank_offers` from `normalizer.py` and renaming it to `rank_offers`.

The exported function must keep this signature:

```python
def rank_offers(
    offers: list[FlightOfferV1],
    *,
    sort_non_comparable: bool = False,
) -> list[FlightOfferV1]:
```

In `normalizer.py`, import and call it:

```python
from cheapy.providers.traveloka.normalization.ranking import rank_offers
```

Replace `_rank_offers(` with `rank_offers(` and delete the old `_rank_offers`.

- [ ] **Step 4: Extract normalizer error factories**

Create `cheapy/providers/traveloka/normalization/errors.py` by moving these
functions from `normalizer.py` and removing the leading underscore from their
public names:

- `_currency_unavailable_error` -> `currency_unavailable_error`
- `_return_details_unavailable_error` -> `return_details_unavailable_error`
- `_selected_round_trip_error` -> `selected_round_trip_error`
- `_parse_error` -> `parse_error`
- `_capability_for_request` -> `capability_for_request`
- `_error` -> `normalization_error`

Keep constants in `errors.py`:

```python
PROVIDER_NAME = "traveloka"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
```

In `normalizer.py`, import:

```python
from cheapy.providers.traveloka.normalization.errors import (
    currency_unavailable_error,
    parse_error,
    return_details_unavailable_error,
    selected_round_trip_error,
)
```

Replace calls:

```text
_currency_unavailable_error(index, request) -> currency_unavailable_error(index, request)
_return_details_unavailable_error(index, request) -> return_details_unavailable_error(index, request)
_selected_round_trip_error(failure_type, request) -> selected_round_trip_error(failure_type, request)
_parse_error(index, request, exc) -> parse_error(index, request, exc)
```

Delete the moved functions from `normalizer.py`.

- [ ] **Step 5: Run ranking, error, and normalizer tests**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_ranking.py tests/traveloka/test_normalization_errors.py tests/test_traveloka_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit ranking and error extraction**

Run:

```bash
git add cheapy/providers/traveloka/normalization/ranking.py cheapy/providers/traveloka/normalization/errors.py cheapy/providers/traveloka/normalizer.py tests/traveloka/test_normalization_ranking.py tests/traveloka/test_normalization_errors.py
git commit -m "refactor: extract Traveloka normalization ranking and errors" -m "Model: GPT-5 Codex"
```

## Task 8: Extract Payload Discovery And Canonicalization

**Files:**
- Create: `cheapy/providers/traveloka/normalization/payloads.py`
- Create: `cheapy/providers/traveloka/normalization/canonical.py`
- Modify: `cheapy/providers/traveloka/normalizer.py`
- Create: `tests/traveloka/test_normalization_payloads.py`
- Create: `tests/traveloka/test_normalization_canonical.py`

- [ ] **Step 1: Write failing payload and canonical tests**

Create `tests/traveloka/test_normalization_payloads.py`:

```python
from __future__ import annotations

from cheapy.providers.traveloka.normalization.payloads import itinerary_items


def test_itinerary_items_prefers_data_search_results() -> None:
    payload = {
        "data": {
            "searchResults": [
                {
                    "id": "tv-1",
                    "fare": {
                        "display": {
                            "currencyValue": {"currency": "USD", "amount": "12345"},
                            "numOfDecimalPoint": "2",
                        }
                    },
                    "connectingFlightRoutes": [],
                }
            ]
        }
    }

    items = itinerary_items(payload)

    assert len(items) == 1
```

Create `tests/traveloka/test_normalization_canonical.py`:

```python
from __future__ import annotations

from cheapy.providers.traveloka.normalization.canonical import canonical_search_result


def test_canonical_search_result_maps_minor_unit_price() -> None:
    item = {
        "id": "tv-1",
        "fare": {
            "display": {
                "currencyValue": {"currency": "USD", "amount": "12345"},
                "numOfDecimalPoint": "2",
            }
        },
        "connectingFlightRoutes": [],
    }

    canonical = canonical_search_result(item)

    assert getattr(canonical, "payload")["id"] == "tv-1"
    assert getattr(canonical, "payload")["price"] == {
        "currency": "USD",
        "amount": 123.45,
    }
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_payloads.py tests/traveloka/test_normalization_canonical.py -v
```

Expected: FAIL because the new modules do not expose these functions.

- [ ] **Step 3: Extract canonicalization**

Create `cheapy/providers/traveloka/normalization/canonical.py` by moving these
items from `normalizer.py`:

- `_TravelokaSearchResultItem`
- `_canonical_search_result` -> `canonical_search_result`
- `_traveloka_search_result_price`
- `_traveloka_price_at_path`
- `_minor_units_amount`
- `_traveloka_search_result_segments`
- `_canonical_search_result_segment`
- `_traveloka_datetime`

Keep private helper names private except `canonical_search_result`.

In `normalizer.py`, import:

```python
from cheapy.providers.traveloka.normalization.canonical import (
    _TravelokaSearchResultItem,
    canonical_search_result,
)
```

Replace `_canonical_search_result(` with `canonical_search_result(`.

- [ ] **Step 4: Extract payload discovery**

Create `cheapy/providers/traveloka/normalization/payloads.py` by moving these
functions from `normalizer.py`:

- `_itinerary_items` -> `itinerary_items`
- `_list_at_path` -> `list_at_path`
- `_recursive_offer_items`
- `_is_offer_like`

Import canonicalization in `payloads.py`:

```python
from cheapy.providers.traveloka.normalization.canonical import canonical_search_result
```

In `normalizer.py`, import:

```python
from cheapy.providers.traveloka.normalization.payloads import itinerary_items
```

Replace `_itinerary_items(` with `itinerary_items(` and delete the moved
payload functions from `normalizer.py`.

- [ ] **Step 5: Run payload, canonical, and normalizer tests**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_payloads.py tests/traveloka/test_normalization_canonical.py tests/test_traveloka_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit payload and canonical extraction**

Run:

```bash
git add cheapy/providers/traveloka/normalization/payloads.py cheapy/providers/traveloka/normalization/canonical.py cheapy/providers/traveloka/normalizer.py tests/traveloka/test_normalization_payloads.py tests/traveloka/test_normalization_canonical.py
git commit -m "refactor: extract Traveloka payload canonicalization" -m "Model: GPT-5 Codex"
```

## Task 9: Extract Legs And Routes

**Files:**
- Create: `cheapy/providers/traveloka/normalization/legs.py`
- Create: `cheapy/providers/traveloka/normalization/routes.py`
- Modify: `cheapy/providers/traveloka/normalizer.py`
- Create: `tests/traveloka/test_normalization_legs.py`
- Create: `tests/traveloka/test_normalization_routes.py`

- [ ] **Step 1: Write failing legs and routes tests**

Create `tests/traveloka/test_normalization_legs.py`:

```python
from __future__ import annotations

from cheapy.providers.traveloka.normalization.legs import normalize_leg


def test_normalize_leg_maps_required_segment_fields() -> None:
    leg = normalize_leg(
        {
            "origin": "SGN",
            "destination": "BKK",
            "departureTime": "2026-07-10T09:00:00",
            "arrivalTime": "2026-07-10T10:35:00",
            "airlineCode": "VJ",
            "flightNumber": "VJ801",
            "durationMinutes": 95,
        }
    )

    assert leg.origin == "SGN"
    assert leg.destination == "BKK"
    assert leg.duration_minutes == 95
```

Create `tests/traveloka/test_normalization_routes.py`:

```python
from __future__ import annotations

import pytest

from cheapy.models import FlightLegV1
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka.normalization.routes import validate_route


def _leg(origin: str, destination: str) -> FlightLegV1:
    return FlightLegV1(
        origin=origin,
        destination=destination,
        departure_time="2026-07-10T09:00:00",
        arrival_time="2026-07-10T10:35:00",
        airline_code="VJ",
        flight_number="VJ801",
        duration_minutes=95,
    )


def test_validate_route_accepts_one_way_chain() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )

    route = validate_route(request, [_leg("SGN", "BKK")])

    assert route.outbound_end_index == 0
    assert route.return_start_index is None
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_legs.py tests/traveloka/test_normalization_routes.py -v
```

Expected: FAIL because legs and routes modules do not exist.

- [ ] **Step 3: Extract leg parsing**

Create `cheapy/providers/traveloka/normalization/legs.py` by moving these
functions from `normalizer.py` and removing the leading underscore from the
entrypoint:

- `_normalize_leg` -> `normalize_leg`
- `_duration_minutes`
- `_required_value`
- `_string_value`
- `_iso_datetime`

In `normalizer.py`, import:

```python
from cheapy.providers.traveloka.normalization.legs import normalize_leg
```

Replace `_normalize_leg(` with `normalize_leg(`.

- [ ] **Step 4: Extract route validation**

Create `cheapy/providers/traveloka/normalization/routes.py` by moving these
items from `normalizer.py` and removing leading underscores from entrypoints:

- `_ValidatedRoute` -> `ValidatedRoute`
- `_validate_route` -> `validate_route`
- `_raw_round_trip_outbound_legs` -> `raw_round_trip_outbound_legs`
- `_validate_exact_candidate_dates` -> `validate_exact_candidate_dates`
- `_chain_end_index`
- `_total_duration_minutes` -> `total_duration_minutes`
- `_stops` -> `stops`
- `_date_offset` -> `date_offset`
- `_requested_origin` -> `requested_origin`
- `_requested_destination` -> `requested_destination`
- `_requested_departure_date` -> `requested_departure_date`
- `_requested_return_date` -> `requested_return_date`

In `normalizer.py`, import these names from `routes.py` and replace the old
private calls. Keep behavior unchanged.

- [ ] **Step 5: Run legs, routes, and normalizer tests**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_legs.py tests/traveloka/test_normalization_routes.py tests/test_traveloka_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit legs and routes extraction**

Run:

```bash
git add cheapy/providers/traveloka/normalization/legs.py cheapy/providers/traveloka/normalization/routes.py cheapy/providers/traveloka/normalizer.py tests/traveloka/test_normalization_legs.py tests/traveloka/test_normalization_routes.py
git commit -m "refactor: extract Traveloka normalization legs and routes" -m "Model: GPT-5 Codex"
```

## Task 10: Extract Selected Normalization And Final Shim

**Files:**
- Create: `cheapy/providers/traveloka/normalization/selected.py`
- Modify: `cheapy/providers/traveloka/normalization/entrypoints.py`
- Modify: `cheapy/providers/traveloka/normalizer.py`
- Create: `tests/traveloka/test_normalization_selected.py`

- [ ] **Step 1: Write failing selected normalization test**

Create `tests/traveloka/test_normalization_selected.py` by moving
`tests/test_traveloka_normalizer.py::test_normalize_selected_round_trip_uses_final_total_and_marks_comparable`
into this file and updating imports to:

```python
from cheapy.providers.traveloka.normalization.selected import normalize_selected_round_trip
```

Keep its helper payloads and assertions unchanged. The moved test must still
assert:

```python
assert offer.price_amount == 321.09
assert offer.comparable is True
assert offer.actual_return_date == "2026-07-17"
```

- [ ] **Step 2: Run selected normalization test and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_selected.py -v
```

Expected: FAIL because `normalization.selected.normalize_selected_round_trip` is missing.

- [ ] **Step 3: Extract selected normalization**

Create `cheapy/providers/traveloka/normalization/selected.py` by moving these
items from `normalizer.py`:

- `normalize_selected_round_trip`
- `_SelectedLegItem`
- `_selected_leg_item`
- `_selected_failure_fallback`
- `_valid_selected_total`

Import dependencies from the modules extracted in Tasks 7-9. Keep
`normalize_selected_round_trip` as the public entrypoint.

- [ ] **Step 4: Move `normalize_payload` into `normalization/entrypoints.py`**

Move `normalize_payload` from `normalizer.py` into
`cheapy/providers/traveloka/normalization/entrypoints.py`. Import dependencies
from the extracted modules and from `normalization.selected`.

After the move, replace `normalizer.py` with this shim:

```python
"""Compatibility imports for Traveloka normalization entrypoints."""

from __future__ import annotations

from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload
from cheapy.providers.traveloka.normalization.selected import (
    normalize_selected_round_trip,
)

__all__ = ["normalize_payload", "normalize_selected_round_trip"]
```

- [ ] **Step 5: Run normalization tests**

Run:

```bash
uv run pytest tests/traveloka/test_normalization_selected.py tests/traveloka/test_normalization_entrypoints.py tests/test_traveloka_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 6: Verify `normalizer.py` is a shim**

Run:

```bash
wc -l cheapy/providers/traveloka/normalizer.py
```

Expected: fewer than 20 lines.

- [ ] **Step 7: Commit selected normalization and shim**

Run:

```bash
git add cheapy/providers/traveloka/normalization/selected.py cheapy/providers/traveloka/normalization/entrypoints.py cheapy/providers/traveloka/normalizer.py tests/traveloka/test_normalization_selected.py tests/traveloka/test_normalization_entrypoints.py tests/test_traveloka_normalizer.py
git commit -m "refactor: split Traveloka normalization entrypoints" -m "Model: GPT-5 Codex"
```

## Task 11: Test Suite Split And Fallback Catalog

**Files:**
- Modify/move: `tests/test_traveloka_adapter.py`
- Modify/move: `tests/test_traveloka_normalizer.py`
- Modify/move: `tests/test_traveloka_provider.py`
- Create/modify: `tests/traveloka/test_capture.py`
- Create/modify: `tests/traveloka/test_inventory.py`
- Create/modify: `tests/traveloka/test_activation.py`
- Create/modify: `tests/traveloka/test_selection.py`
- Create/modify: `tests/traveloka/test_totals.py`
- Create/modify: `tests/traveloka/test_provider.py`

- [ ] **Step 1: Move provider tests**

Run:

```bash
git mv tests/test_traveloka_provider.py tests/traveloka/test_provider.py
```

Run:

```bash
uv run pytest tests/traveloka/test_provider.py -v
```

Expected: PASS.

- [ ] **Step 2: Move capture tests out of adapter test file**

Move these test functions from `tests/test_traveloka_adapter.py` into
`tests/traveloka/test_capture.py` with their required fake classes:

- `test_adapter_captures_completed_initial_fare_payload`
- `test_adapter_captures_completed_poll_fare_payload`
- `test_adapter_keeps_non_empty_payload_when_completion_frame_is_empty`
- `test_capture_state_preserves_partial_failure_type_when_completion_upgrades_prior_result`
- `test_traveloka_capture_state_lives_in_capture_module`
- `test_adapter_uses_empty_completion_payload_when_no_offers_were_seen`
- `test_adapter_returns_partial_payload_when_timeout_happens_after_offers`
- `test_adapter_preserves_partial_failure_type_when_returning_timed_out_partial_copy`
- `test_adapter_raises_timeout_when_no_fare_payload_arrives`
- `test_adapter_ignores_non_fare_endpoints`
- `test_adapter_ignores_supported_path_from_non_traveloka_host`
- `test_adapter_rejects_unsupported_json_on_fare_endpoint`
- `test_adapter_rejects_invalid_json_from_fare_endpoint`
- `test_adapter_maps_fare_endpoint_http_status`

Run:

```bash
uv run pytest tests/traveloka/test_capture.py tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 3: Move inventory, activation, selection, and totals tests**

Move the following groups from `tests/test_traveloka_adapter.py`:

- inventory tests from `test_cheapest_visible_option_returns_none_for_empty_options` through `test_visible_options_from_page_uses_fresh_remaining_deadline_for_each_read` into `tests/traveloka/test_inventory.py`
- totals tests from `test_read_final_total_prefers_explicit_selected_total_and_uses_bounded_timeout` through `test_read_final_total_ignores_ambiguous_generic_total` into `tests/traveloka/test_totals.py`
- selection tests from `test_wait_for_return_selection_transition_recognizes_selected_summary` through `test_outbound_transition_accepts_exact_selected_fragment_from_different_baseline` into `tests/traveloka/test_selection.py`
- activation tests `test_click_visible_option_dispatches_traveloka_activation_sequence` and `test_click_visible_option_scrolls_and_caps_live_activation_timeout` into `tests/traveloka/test_activation.py`

Run:

```bash
uv run pytest tests/traveloka/test_inventory.py tests/traveloka/test_totals.py tests/traveloka/test_selection.py tests/traveloka/test_activation.py tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 4: Move normalization tests**

Move remaining non-selected normalization tests from
`tests/test_traveloka_normalizer.py` into the module-owned normalization files
created in Tasks 7-10:

- payload discovery tests into `tests/traveloka/test_normalization_payloads.py`
- Traveloka `searchResults` canonicalization tests into `tests/traveloka/test_normalization_canonical.py`
- route/date validation tests into `tests/traveloka/test_normalization_routes.py`
- selected round-trip tests into `tests/traveloka/test_normalization_selected.py`
- ranking and mixed currency tests into `tests/traveloka/test_normalization_ranking.py`
- entrypoint integration tests into `tests/traveloka/test_normalization_entrypoints.py`

Run:

```bash
uv run pytest tests/traveloka/test_normalization_*.py -v
```

Expected: PASS.

- [ ] **Step 5: Catalog every fallback deletion before deleting it**

Before deleting any parser or DOM fallback, add a catalog entry to the commit
message body. Use the exact format from this plan. Example for a deletion that
is allowed:

```markdown
Fallback removal catalog:
- Removed path: `inventory._visible_options_from_legacy_buttons`
- Old coverage: `tests/test_traveloka_adapter.py::test_legacy_button_fallback_name`
- Runtime-path analysis: current selected round-trip flow discovers only `[data-testid^='flight-inventory-card-container-']` cards and binds explicit payload IDs from those cards.
- Replacement coverage: `tests/traveloka/test_inventory.py::test_visible_options_from_page_discovers_live_inventory_cards`
- Public behavior safety: selected round-trip partial failure remains `outbound_selection_unavailable` or `return_selection_unavailable`; no public error details include page text or selectors.
```

If the catalog cannot be completed, keep the fallback and move it to the new
owning module.

- [ ] **Step 6: Remove empty legacy test files**

If `tests/test_traveloka_adapter.py`, `tests/test_traveloka_normalizer.py`, or
`tests/test_traveloka_provider.py` become empty after the split, delete them.
If they still contain adapter facade or compatibility tests, leave them in place
until those tests have a module-owned destination.

Run:

```bash
uv run pytest tests/test_traveloka_* tests/traveloka -v
```

Expected: PASS. If a glob has no matches because files were fully moved, run:

```bash
uv run pytest tests/traveloka -v
```

Expected: PASS.

- [ ] **Step 7: Commit test split and fallback catalog**

Run:

```bash
git add tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py tests/traveloka
git commit -m "test: split Traveloka tests by module ownership" -m "Model: GPT-5 Codex" -m "Fallback removal catalog: no fallback behavior removed in this commit unless entries are listed above."
```

If fallback behavior was removed, replace the final commit body line with the
completed catalog entries.

## Task 12: Live Matrix Recorder

**Files:**
- Create: `scripts/benchmark_traveloka_live_matrix.py`
- Create: `tests/traveloka/test_live_matrix_script.py`

- [ ] **Step 1: Write failing live matrix record-format test**

Create `tests/traveloka/test_live_matrix_script.py`:

```python
from __future__ import annotations

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactRoundTripRequest, ProviderResult
from scripts.benchmark_traveloka_live_matrix import matrix_record


def test_matrix_record_contains_required_fields() -> None:
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-12",
        return_date="2026-06-17",
    )
    result = ProviderResult(
        provider_name="traveloka",
        capability="exact_round_trip",
        status=ProviderStatusCode.SUCCESS,
        offers=[],
        warnings=[],
        errors=[],
        duration_ms=123,
        retryable=False,
    )

    record = matrix_record(
        run_label="baseline",
        request=request,
        result=result,
        duration_ms=456,
    )

    assert record == {
        "run_label": "baseline",
        "origin": "SGN",
        "destination": "BKK",
        "departure_date": "2026-06-12",
        "return_date": "2026-06-17",
        "status": "success",
        "offer_count": 0,
        "comparable_offer_count": 0,
        "failure_types": [],
        "duration_ms": 456,
    }
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_live_matrix_script.py -v
```

Expected: FAIL because `scripts/benchmark_traveloka_live_matrix.py` does not exist.

- [ ] **Step 3: Create live matrix script**

Create `scripts/benchmark_traveloka_live_matrix.py`:

```python
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from cheapy.providers.base import ProviderExactRoundTripRequest, ProviderResult
from cheapy.providers.traveloka.provider import create_provider


ROUTES = (
    ("SGN", "BKK", "2026-06-12", "2026-06-17"),
    ("HAN", "SIN", "2026-07-03", "2026-07-08"),
    ("DAD", "KUL", "2026-07-05", "2026-07-10"),
    ("SGN", "HKG", "2026-07-09", "2026-07-14"),
    ("SGN", "NRT", "2026-07-11", "2026-07-18"),
)


def matrix_record(
    *,
    run_label: str,
    request: ProviderExactRoundTripRequest,
    result: ProviderResult,
    duration_ms: int,
) -> dict[str, object]:
    if run_label not in {"baseline", "refactored"}:
        raise ValueError("run_label must be baseline or refactored")
    return {
        "run_label": run_label,
        "origin": request.origin,
        "destination": request.destination,
        "departure_date": request.departure_date,
        "return_date": request.return_date,
        "status": result.status.value,
        "offer_count": len(result.offers),
        "comparable_offer_count": sum(1 for offer in result.offers if offer.comparable),
        "failure_types": sorted(
            {
                str(error.details.get("failure_type"))
                for error in result.errors
                if error.details.get("failure_type") is not None
            }
        ),
        "duration_ms": duration_ms,
    }


async def run_matrix(*, run_label: str) -> list[dict[str, object]]:
    provider = create_provider()
    records: list[dict[str, object]] = []
    for origin, destination, departure_date, return_date in ROUTES:
        request = ProviderExactRoundTripRequest(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
        )
        started = perf_counter()
        result = await provider.search_exact_round_trip(request)
        records.append(
            matrix_record(
                run_label=run_label,
                request=request,
                result=result,
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
            )
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-label", choices=("baseline", "refactored"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = asyncio.run(run_matrix(run_label=args.run_label))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run live matrix script test**

Run:

```bash
uv run pytest tests/traveloka/test_live_matrix_script.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit live matrix recorder**

Run:

```bash
git add scripts/benchmark_traveloka_live_matrix.py tests/traveloka/test_live_matrix_script.py
git commit -m "test: add Traveloka live matrix recorder" -m "Model: GPT-5 Codex"
```

## Task 13: Final Verification

**Files:**
- Verify only

- [ ] **Step 1: Run Traveloka test suite**

Run one of these commands depending on whether old root Traveloka files remain:

```bash
uv run pytest tests/test_traveloka_* tests/traveloka -v
```

If the old root files were deleted, run:

```bash
uv run pytest tests/traveloka -v
```

Expected: PASS.

- [ ] **Step 2: Run search tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full offline test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 4: Run opt-in live smoke**

Run only after the offline suite passes:

```bash
CHEAPY_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_traveloka.py -v
```

Expected: PASS, SKIP is acceptable only if the environment deliberately does not set `CHEAPY_RUN_LIVE_TESTS=1`.

- [ ] **Step 5: Run baseline or refactored live matrix**

For a pre-refactor baseline run:

```bash
CHEAPY_RUN_LIVE_TESTS=1 uv run python scripts/benchmark_traveloka_live_matrix.py --run-label baseline --output .tmp/traveloka-live-baseline.jsonl
```

For a post-refactor run:

```bash
CHEAPY_RUN_LIVE_TESTS=1 uv run python scripts/benchmark_traveloka_live_matrix.py --run-label refactored --output .tmp/traveloka-live-refactored.jsonl
```

Expected: the command writes JSONL records with the required fields. A Traveloka
site-level failure is acceptable only if it is represented as structured
provider errors and does not leak sensitive details.

- [ ] **Step 6: Inspect final git diff**

Run:

```bash
git status --short
git log --oneline -n 8
```

Expected: only intentional implementation files are changed. Pre-existing
unrelated deletions must remain unstaged unless the user explicitly asked to
handle them.

## Plan Self-Review

- Spec coverage: session boundary is covered by Tasks 2-5; normalization split
  is covered by Tasks 6-10; test ownership split and fallback catalog are
  covered by Task 11; live recording format and matrix are covered by Task 12;
  final offline and live verification are covered by Task 13.
- Placeholder scan: this plan contains no unfinished implementation steps.
- Type consistency: `provider.py` remains the only `ProviderResult` owner;
  workflow functions return `TravelokaCaptureResult` or
  `TravelokaSelectedRoundTripResult`; normalization functions return
  offers/errors.
