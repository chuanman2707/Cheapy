# Cheapy Traveloka Safe Browser Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the Traveloka selected round-trip browser workflow duration while preserving conservative default behavior and selected-final-total correctness.

**Architecture:** Keep the existing browser-first provider flow. Add three small helper units: safe phase timing, final-total selector tiering, and opt-in stable visible-option early proceed. The adapter remains the only component that drives Traveloka UI state; provider and Contract V1 schemas stay unchanged.

**Tech Stack:** Python 3.12, pytest, uv, cloakbrowser/Playwright-style sync browser API, Cheapy Contract V1 models.

---

## File Structure

- Create `cheapy/providers/traveloka/timing.py` for safe phase timing dataclasses and recorder.
- Modify `cheapy/providers/traveloka/adapter.py` for timing integration, final-total selector cache, stable-option sampler, environment flag parsing, and selected round-trip flow integration.
- Create `scripts/benchmark_traveloka_browser_optimization.py` for the paired conservative/fast 10-route benchmark.
- Modify `tests/test_traveloka_adapter.py` for unit and adapter integration coverage.
- Create `tests/test_traveloka_benchmark.py` for non-live benchmark script behavior.
- Run existing `tests/test_traveloka_normalizer.py`, `tests/test_traveloka_provider.py`, and `tests/test_search.py` unchanged to prove no Contract V1 or provider behavior regression.

## Task 1: Safe Phase Timing Recorder

**Files:**
- Create: `cheapy/providers/traveloka/timing.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Test: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing timing recorder tests**

Add these imports near the existing Traveloka adapter imports in `tests/test_traveloka_adapter.py`:

```python
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder
```

Add these tests near the Traveloka result dataclass tests:

```python
def test_phase_recorder_records_safe_phase_without_sensitive_metadata() -> None:
    now_values = iter([10.0, 10.125])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))

    with recorder.phase("initial_navigation"):
        pass

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "initial_navigation"
    assert record.duration_ms == 125
    assert record.success is True
    assert record.failure_type is None
    assert record.count is None
    assert not hasattr(record, "url")
    assert not hasattr(record, "headers")
    assert not hasattr(record, "payload")
    assert not hasattr(record, "cookies")


def test_phase_recorder_records_safe_failure_type() -> None:
    now_values = iter([20.0, 20.25])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))
    error = traveloka_adapter.TravelokaProviderError(
        failure_type="navigation_failed",
        message_en="Traveloka navigation failed at https://example.invalid/path",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
    )

    with pytest.raises(traveloka_adapter.TravelokaProviderError):
        with recorder.phase("initial_navigation"):
            raise error

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "initial_navigation"
    assert record.duration_ms == 250
    assert record.success is False
    assert record.failure_type == "navigation_failed"
    assert "example.invalid" not in str(record)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_phase_recorder_records_safe_phase_without_sensitive_metadata tests/test_traveloka_adapter.py::test_phase_recorder_records_safe_failure_type -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.providers.traveloka.timing'`.

- [ ] **Step 3: Implement timing helper**

Create `cheapy/providers/traveloka/timing.py` with this content:

```python
"""Safe phase timing helpers for the Traveloka browser adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic


Clock = Callable[[], float]


@dataclass(frozen=True)
class TravelokaPhaseTiming:
    phase: str
    duration_ms: int
    success: bool
    failure_type: str | None = None
    count: int | None = None


class TravelokaPhaseRecorder:
    """Records phase names and durations without URLs, headers, cookies, or payloads."""

    def __init__(self, *, clock: Clock = monotonic) -> None:
        self._clock = clock
        self._records: list[TravelokaPhaseTiming] = []

    @property
    def records(self) -> tuple[TravelokaPhaseTiming, ...]:
        return tuple(self._records)

    @contextmanager
    def phase(self, phase: str, *, count: int | None = None) -> Iterator[None]:
        started_at = self._clock()
        try:
            yield
        except Exception as exc:
            self.record(
                phase,
                started_at=started_at,
                success=False,
                failure_type=_safe_failure_type(exc),
                count=count,
            )
            raise
        else:
            self.record(phase, started_at=started_at, success=True, count=count)

    def record(
        self,
        phase: str,
        *,
        started_at: float,
        success: bool,
        failure_type: str | None = None,
        count: int | None = None,
    ) -> None:
        duration_ms = max(0, round((self._clock() - started_at) * 1000))
        self._records.append(
            TravelokaPhaseTiming(
                phase=_safe_token(phase),
                duration_ms=duration_ms,
                success=success,
                failure_type=_safe_token(failure_type) if failure_type else None,
                count=count,
            )
        )


def _safe_failure_type(exc: Exception) -> str:
    value = getattr(exc, "failure_type", None)
    if isinstance(value, str) and value:
        return _safe_token(value)
    return _safe_token(type(exc).__name__)


def _safe_token(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in value.strip().lower()
    )
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "unknown"
```

- [ ] **Step 4: Expose recorder on adapter without changing Contract V1**

In `cheapy/providers/traveloka/adapter.py`, add this import:

```python
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder, TravelokaPhaseTiming
```

Extend `TravelokaAdapter.__init__`:

```python
        self._phase_recorder = TravelokaPhaseRecorder(clock=monotonic)
```

Add this property on `TravelokaAdapter`:

```python
    @property
    def phase_timings(self) -> tuple[TravelokaPhaseTiming, ...]:
        return self._phase_recorder.records
```

- [ ] **Step 5: Run timing tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_phase_recorder_records_safe_phase_without_sensitive_metadata tests/test_traveloka_adapter.py::test_phase_recorder_records_safe_failure_type -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cheapy/providers/traveloka/timing.py cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "feat: add safe Traveloka phase timing recorder"
```

## Task 2: Ordered Final-Total Selector Strategy

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Test: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing final-total tier tests**

Add these tests near the existing `_read_final_total` tests in `tests/test_traveloka_adapter.py`:

```python
def test_read_final_total_cached_summary_selector_cannot_outrank_selected_total() -> None:
    selected_total = TextFakeLocator(text="Selected final total USD 321.09")
    summary_total = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    cache = traveloka_adapter._FinalTotalSelectorCache(
        tier="summary",
        selector="#flight-search-result",
    )
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_total,
            "#flight-search-result": summary_total,
        },
    )

    result = traveloka_adapter._read_final_total(
        page,
        timeout_ms=456,
        selector_cache=cache,
    )

    assert result == (Decimal("321.09"), "USD")
    assert selected_total.inner_text_kwargs == [{"timeout": 456}]
    assert summary_total.inner_text_kwargs == []
    assert cache.tier == "selected_total"
    assert cache.selector == "[data-testid*='selected'][data-testid*='total']"


def test_read_final_total_cached_label_selector_cannot_outrank_summary_total() -> None:
    summary_total = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    label_total = TextFakeLocator(text="Total USD 111.00/pax")
    cache = traveloka_adapter._FinalTotalSelectorCache(
        tier="global_label",
        selector="[data-testid='label_fl_inventory_price']",
    )
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='bundle-summary-tray']": summary_total,
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [label_total]
            ),
        },
    )

    result = traveloka_adapter._read_final_total(
        page,
        timeout_ms=456,
        selector_cache=cache,
    )

    assert result == (Decimal("239.68"), "USD")
    assert summary_total.inner_text_kwargs == [{"timeout": 456}]
    assert label_total.inner_text_kwargs == []
    assert cache.tier == "summary"
    assert cache.selector == "[data-testid='bundle-summary-tray']"


def test_read_final_total_cached_selector_reorders_only_inside_same_tier() -> None:
    final_total = TextFakeLocator(text="Final total USD 321.09")
    selected_total = TextFakeLocator(text="Selected total unavailable")
    cache = traveloka_adapter._FinalTotalSelectorCache(
        tier="selected_total",
        selector="[data-testid*='final'][data-testid*='total']",
    )
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_total,
            "[data-testid*='final'][data-testid*='total']": final_total,
        },
    )

    result = traveloka_adapter._read_final_total(
        page,
        timeout_ms=456,
        selector_cache=cache,
    )

    assert result == (Decimal("321.09"), "USD")
    assert final_total.inner_text_kwargs == [{"timeout": 456}]
    assert selected_total.inner_text_kwargs == []
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_read_final_total_cached_summary_selector_cannot_outrank_selected_total tests/test_traveloka_adapter.py::test_read_final_total_cached_label_selector_cannot_outrank_summary_total tests/test_traveloka_adapter.py::test_read_final_total_cached_selector_reorders_only_inside_same_tier -v
```

Expected: FAIL with `AttributeError: module 'cheapy.providers.traveloka.adapter' has no attribute '_FinalTotalSelectorCache'`.

- [ ] **Step 3: Add selector tiers and cache**

In `cheapy/providers/traveloka/adapter.py`, add this dataclass near `TravelokaVisibleOption`:

```python
@dataclass
class _FinalTotalSelectorCache:
    tier: str | None = None
    selector: str | None = None

    def record(self, *, tier: str, selector: str) -> None:
        self.tier = tier
        self.selector = selector
```

Replace the local selector tuples in `_read_final_total` and `_final_total_texts` with module-level constants:

```python
_FINAL_TOTAL_SELECTED_TIER = "selected_total"
_FINAL_TOTAL_SUMMARY_TIER = "summary"
_FINAL_TOTAL_GLOBAL_LABEL_TIER = "global_label"

_FINAL_TOTAL_SELECTED_SELECTORS: tuple[tuple[str, bool], ...] = (
    ("[data-testid*='selected'][data-testid*='total']", False),
    ("[data-testid*='final'][data-testid*='total']", True),
    ("[data-testid*='checkout'][data-testid*='total']", True),
    ("[aria-label*='selected' i][aria-label*='total' i]", False),
    ("[aria-label*='final' i][aria-label*='total' i]", True),
    ("[aria-label*='checkout' i][aria-label*='total' i]", True),
    ("text=/selected\\s+(?:final\\s+)?total/i", False),
    ("text=/final\\s+total/i", False),
    ("text=/checkout\\s+total/i", False),
)
_FINAL_TOTAL_SUMMARY_SELECTORS: tuple[str, ...] = (
    "#flight-search-result",
    "[data-testid='bundle-summary-tray']",
    "[data-testid*='bundle-summary']",
    "[data-testid*='selected'][data-testid*='summary']",
    "[data-testid*='summary'][data-testid*='tray']",
    "[aria-label*='selected' i][aria-label*='summary' i]",
    "[aria-label*='summary' i][aria-label*='tray' i]",
)
_FINAL_TOTAL_GLOBAL_LABEL_SELECTOR = "[data-testid='label_fl_inventory_price']"
```

Add selector ordering helpers:

```python
def _ordered_final_total_selector_items(
    *,
    tier: str,
    selectors: tuple[tuple[str, bool], ...],
    selector_cache: _FinalTotalSelectorCache | None,
) -> tuple[tuple[str, bool], ...]:
    if selector_cache is None or selector_cache.tier != tier:
        return selectors
    cached = selector_cache.selector
    if cached is None:
        return selectors
    cached_items = tuple(item for item in selectors if item[0] == cached)
    if not cached_items:
        return selectors
    remaining = tuple(item for item in selectors if item[0] != cached)
    return (*cached_items, *remaining)


def _ordered_final_total_selectors(
    *,
    tier: str,
    selectors: tuple[str, ...],
    selector_cache: _FinalTotalSelectorCache | None,
) -> tuple[str, ...]:
    if selector_cache is None or selector_cache.tier != tier:
        return selectors
    cached = selector_cache.selector
    if cached is None or cached not in selectors:
        return selectors
    return (cached, *(selector for selector in selectors if selector != cached))
```

- [ ] **Step 4: Wire cache into final-total reads**

Change `_read_final_total` signature:

```python
def _read_final_total(
    page: object,
    *,
    timeout_ms: int = 1000,
    deadline: float | None = None,
    before_texts: Iterable[str] = (),
    selector_cache: _FinalTotalSelectorCache | None = None,
) -> tuple[Decimal, str] | None:
```

Inside `_read_final_total`, preserve the tier order exactly:

```python
    for selector, allow_price_only in _ordered_final_total_selector_items(
        tier=_FINAL_TOTAL_SELECTED_TIER,
        selectors=_FINAL_TOTAL_SELECTED_SELECTORS,
        selector_cache=selector_cache,
    ):
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=timeout_ms,
            deadline=deadline,
        ):
            if _normalized_text_key(text) in before_text_keys:
                continue
            parsed = _parse_selected_total_price(
                text,
                allow_price_only=allow_price_only,
            )
            if parsed is not None:
                if selector_cache is not None:
                    selector_cache.record(
                        tier=_FINAL_TOTAL_SELECTED_TIER,
                        selector=selector,
                    )
                return parsed

    all_summary_texts: list[tuple[str, str]] = []
    for selector in _ordered_final_total_selectors(
        tier=_FINAL_TOTAL_SUMMARY_TIER,
        selectors=_FINAL_TOTAL_SUMMARY_SELECTORS,
        selector_cache=selector_cache,
    ):
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=timeout_ms,
            deadline=deadline,
        ):
            if _normalized_text_key(text) not in before_text_keys:
                all_summary_texts.append((selector, text))
    for selector, text in all_summary_texts:
        parsed = _parse_explicit_price(text, _EXPLICIT_ROUND_TRIP_PRICE_RE)
        if parsed is not None:
            if selector_cache is not None:
                selector_cache.record(tier=_FINAL_TOTAL_SUMMARY_TIER, selector=selector)
            return parsed
    for selector, text in all_summary_texts:
        parsed = _parse_explicit_price(text, _EXPLICIT_SUMMARY_PRICE_RE)
        if parsed is not None:
            if selector_cache is not None:
                selector_cache.record(tier=_FINAL_TOTAL_SUMMARY_TIER, selector=selector)
            return parsed

    label_totals: list[tuple[Decimal, str]] = []
    for text in _locator_texts(
        page,
        _FINAL_TOTAL_GLOBAL_LABEL_SELECTOR,
        timeout_ms=timeout_ms,
        deadline=deadline,
    ):
        if _normalized_text_key(text) in before_text_keys:
            continue
        label_totals.extend(_parse_explicit_prices(text, _EXPLICIT_TOTAL_PRICE_RE))
    if len(label_totals) == 1:
        if selector_cache is not None:
            selector_cache.record(
                tier=_FINAL_TOTAL_GLOBAL_LABEL_TIER,
                selector=_FINAL_TOTAL_GLOBAL_LABEL_SELECTOR,
            )
        return label_totals[0]
    return None
```

Update `_final_total_texts` to iterate `_FINAL_TOTAL_SELECTED_SELECTORS`, `_FINAL_TOTAL_SUMMARY_SELECTORS`, and `_FINAL_TOTAL_GLOBAL_LABEL_SELECTOR`.

Update `_wait_for_final_total`:

```python
    selector_cache = _FinalTotalSelectorCache()
```

Pass `selector_cache=selector_cache` into each `_read_final_total` call inside the polling loop.

- [ ] **Step 5: Run final-total tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_read_final_total_cached_summary_selector_cannot_outrank_selected_total tests/test_traveloka_adapter.py::test_read_final_total_cached_label_selector_cannot_outrank_summary_total tests/test_traveloka_adapter.py::test_read_final_total_cached_selector_reorders_only_inside_same_tier tests/test_traveloka_adapter.py::test_round_trip_rejects_stale_summary_total_after_return_transition tests/test_traveloka_adapter.py::test_round_trip_reads_live_flight_search_result_summary_total -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "fix: preserve Traveloka final total selector precedence"
```

## Task 3: Stable Bindable Visible-Option Sampler

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Test: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing sampler tests**

Add these tests near the visible-option helper tests in `tests/test_traveloka_adapter.py`:

```python
def test_stable_option_sampler_requires_payload_id_and_500ms_dwell() -> None:
    now_values = iter([0.0, 0.25, 0.5])
    sampler = traveloka_adapter._StableVisibleOptionSampler(
        clock=lambda: next(now_values),
        dwell_seconds=0.5,
    )
    payload = {
        "data": {
            "searchResults": [{"id": "out-1"}],
        }
    }
    option = _visible_option(key="out-1", price_amount=Decimal("120.00"))

    assert sampler.sample([option], payload) is None
    assert sampler.sample([option], payload) is None
    decision = sampler.sample([option], payload)

    assert decision is not None
    assert decision.option is option
    assert decision.bound_key == "out-1"


def test_stable_option_sampler_ignores_locator_identity_changes() -> None:
    now_values = iter([0.0, 0.25, 0.5])
    sampler = traveloka_adapter._StableVisibleOptionSampler(
        clock=lambda: next(now_values),
        dwell_seconds=0.5,
    )
    payload = {"data": {"searchResults": [{"id": "out-1"}]}}

    assert sampler.sample(
        [_visible_option(key="out-1", price_amount=Decimal("120.00"), locator=FakeLocator())],
        payload,
    ) is None
    assert sampler.sample(
        [_visible_option(key="out-1", price_amount=Decimal("120.00"), locator=FakeLocator())],
        payload,
    ) is None
    decision = sampler.sample(
        [_visible_option(key="out-1", price_amount=Decimal("120.00"), locator=FakeLocator())],
        payload,
    )

    assert decision is not None
    assert decision.bound_key == "out-1"


def test_stable_option_sampler_restarts_when_price_changes() -> None:
    now_values = iter([0.0, 0.25, 0.5, 0.75])
    sampler = traveloka_adapter._StableVisibleOptionSampler(
        clock=lambda: next(now_values),
        dwell_seconds=0.5,
    )
    payload = {"data": {"searchResults": [{"id": "out-1"}]}}

    assert sampler.sample([_visible_option(key="out-1", price_amount=Decimal("120.00"))], payload) is None
    assert sampler.sample([_visible_option(key="out-1", price_amount=Decimal("110.00"))], payload) is None
    assert sampler.sample([_visible_option(key="out-1", price_amount=Decimal("110.00"))], payload) is None
    decision = sampler.sample([_visible_option(key="out-1", price_amount=Decimal("110.00"))], payload)

    assert decision is not None
    assert decision.bound_key == "out-1"


def test_stable_option_sampler_rejects_unbindable_or_idless_payload() -> None:
    sampler = traveloka_adapter._StableVisibleOptionSampler(clock=lambda: 0.0)

    assert sampler.sample(
        [_visible_option(key="missing", price_amount=Decimal("120.00"))],
        {"data": {"searchResults": [{"id": "out-1"}]}},
    ) is None
    assert sampler.sample(
        [_visible_option(key="out-1", price_amount=Decimal("120.00"))],
        {"data": {"searchResults": [{"price": {"amount": "120.00"}}]}},
    ) is None
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_stable_option_sampler_requires_payload_id_and_500ms_dwell tests/test_traveloka_adapter.py::test_stable_option_sampler_ignores_locator_identity_changes tests/test_traveloka_adapter.py::test_stable_option_sampler_restarts_when_price_changes tests/test_traveloka_adapter.py::test_stable_option_sampler_rejects_unbindable_or_idless_payload -v
```

Expected: FAIL with `AttributeError: module 'cheapy.providers.traveloka.adapter' has no attribute '_StableVisibleOptionSampler'`.

- [ ] **Step 3: Implement sampler**

In `cheapy/providers/traveloka/adapter.py`, add:

```python
import os
```

Add constants near the existing timeout constants:

```python
TRAVELOKA_FAST_STABLE_OPTIONS_ENV = "TRAVELOKA_FAST_STABLE_OPTIONS"
STABLE_VISIBLE_OPTION_DWELL_SECONDS = 0.5
```

Add dataclasses and sampler near visible-option helpers:

```python
@dataclass(frozen=True)
class _StableVisibleOptionDecision:
    option: TravelokaVisibleOption
    bound_key: str


class _StableVisibleOptionSampler:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        dwell_seconds: float = STABLE_VISIBLE_OPTION_DWELL_SECONDS,
    ) -> None:
        self._clock = clock
        self._dwell_seconds = dwell_seconds
        self._current_key: tuple[str, Decimal, str | None] | None = None
        self._first_seen_at: float | None = None
        self._sample_count = 0

    def sample(
        self,
        options: Iterable[TravelokaVisibleOption],
        payload: dict[str, object],
    ) -> _StableVisibleOptionDecision | None:
        if not _explicit_payload_item_ids(payload):
            self._reset()
            return None
        option = _cheapest_visible_option(options)
        if option is None:
            self._reset()
            return None
        bound_key = _bind_visible_option_to_payload(option, payload)
        if bound_key is None:
            self._reset()
            return None
        stable_key = (bound_key, option.price_amount, option.currency)
        now = self._clock()
        if stable_key != self._current_key:
            self._current_key = stable_key
            self._first_seen_at = now
            self._sample_count = 1
            return None
        self._sample_count += 1
        if (
            self._sample_count >= 2
            and self._first_seen_at is not None
            and now - self._first_seen_at >= self._dwell_seconds
        ):
            return _StableVisibleOptionDecision(option=option, bound_key=bound_key)
        return None

    def _reset(self) -> None:
        self._current_key = None
        self._first_seen_at = None
        self._sample_count = 0


def _fast_stable_options_enabled() -> bool:
    return os.environ.get(TRAVELOKA_FAST_STABLE_OPTIONS_ENV) == "1"
```

- [ ] **Step 4: Run sampler tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_stable_option_sampler_requires_payload_id_and_500ms_dwell tests/test_traveloka_adapter.py::test_stable_option_sampler_ignores_locator_identity_changes tests/test_traveloka_adapter.py::test_stable_option_sampler_restarts_when_price_changes tests/test_traveloka_adapter.py::test_stable_option_sampler_rejects_unbindable_or_idless_payload -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "feat: add Traveloka stable visible option sampler"
```

## Task 4: Integrate Timing And Opt-In Early Proceed

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Test: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing integration tests**

Add these tests near the selected round-trip adapter tests:

```python
def test_round_trip_default_waits_conservatively_for_capture_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage([])
    captures = [
        traveloka_adapter._CaptureWaitOutcome(
            capture=traveloka_adapter.TravelokaCaptureResult(
                payload={"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}},
                source_path="/api/v2/flight/search/initial",
                search_completed=True,
            )
        ),
        traveloka_adapter._CaptureWaitOutcome(
            capture=traveloka_adapter.TravelokaCaptureResult(
                payload={"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}},
                source_path="/api/v2/flight/search/poll",
                search_completed=True,
            )
        ),
    ]
    seen_fast_flags: list[bool] = []
    options = [
        [_visible_option(key="out-1")],
        [_visible_option(key="ret-1")],
    ]

    def wait_for_capture_outcome(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
        fast_stable_options: bool,
    ) -> object:
        seen_fast_flags.append(fast_stable_options)
        return captures.pop(0)

    monkeypatch.delenv(traveloka_adapter.TRAVELOKA_FAST_STABLE_OPTIONS_ENV, raising=False)
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_capture_outcome",
        wait_for_capture_outcome,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_read_final_total",
        lambda page_arg, **kwargs: (Decimal("321.09"), "USD"),
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, traveloka_adapter.TravelokaSelectedRoundTripResult)
    assert seen_fast_flags == [False, False]


def test_round_trip_fast_flag_enables_stable_capture_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage([])
    captures = [
        traveloka_adapter._CaptureWaitOutcome(
            capture=traveloka_adapter.TravelokaCaptureResult(
                payload={"data": {"meta": {"searchCompleted": False}, "searchResults": [{"id": "out-1"}]}},
                source_path="/api/v2/flight/search/initial",
                search_completed=False,
            ),
            early_option=_visible_option(key="out-1", locator=EmittingFakeLocator()),
            early_bound_key="out-1",
        ),
        traveloka_adapter._CaptureWaitOutcome(
            capture=traveloka_adapter.TravelokaCaptureResult(
                payload={"data": {"meta": {"searchCompleted": False}, "searchResults": [{"id": "ret-1"}]}},
                source_path="/api/v2/flight/search/poll",
                search_completed=False,
            ),
            early_option=_visible_option(key="ret-1", locator=EmittingFakeLocator()),
            early_bound_key="ret-1",
        ),
    ]
    seen_fast_flags: list[bool] = []

    def wait_for_capture_outcome(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
        fast_stable_options: bool,
    ) -> object:
        seen_fast_flags.append(fast_stable_options)
        return captures.pop(0)

    monkeypatch.setenv(traveloka_adapter.TRAVELOKA_FAST_STABLE_OPTIONS_ENV, "1")
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_capture_outcome",
        wait_for_capture_outcome,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_read_final_total",
        lambda page_arg, **kwargs: (Decimal("321.09"), "USD"),
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, traveloka_adapter.TravelokaSelectedRoundTripResult)
    assert result.selected_outbound_key == "out-1"
    assert result.selected_return_key == "ret-1"
    assert seen_fast_flags == [True, True]


def test_wait_for_capture_outcome_returns_early_stable_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [{"id": "out-1"}],
        }
    }
    state = traveloka_adapter._CaptureState()
    state.best_result = traveloka_adapter.TravelokaCaptureResult(
        payload=payload,
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
    )
    page = FakePage([])
    current_time = {"value": 0.0}

    def now() -> float:
        return current_time["value"]

    def wait_for_timeout(milliseconds: int) -> None:
        page.wait_calls += 1
        current_time["value"] += milliseconds / 1000

    page.wait_for_timeout = wait_for_timeout
    monkeypatch.setattr(traveloka_adapter, "monotonic", now)
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(key="out-1", price_amount=Decimal("120.00"))
        ],
    )

    outcome = traveloka_adapter._wait_for_capture_outcome(
        state,
        page,
        deadline=1.0,
        poll_interval_seconds=0.25,
        fast_stable_options=True,
    )

    assert outcome.capture.payload == payload
    assert outcome.capture.timed_out is False
    assert outcome.early_option is not None
    assert outcome.early_bound_key == "out-1"
```

- [ ] **Step 2: Run integration tests to verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_round_trip_default_waits_conservatively_for_capture_completion tests/test_traveloka_adapter.py::test_round_trip_fast_flag_enables_stable_capture_outcome tests/test_traveloka_adapter.py::test_wait_for_capture_outcome_returns_early_stable_option -v
```

Expected: FAIL with `AttributeError` for `_CaptureWaitOutcome` or `_wait_for_capture_outcome`.

- [ ] **Step 3: Add capture outcome helper**

In `cheapy/providers/traveloka/adapter.py`, add:

```python
@dataclass(frozen=True)
class _CaptureWaitOutcome:
    capture: TravelokaCaptureResult
    early_option: TravelokaVisibleOption | None = None
    early_bound_key: str | None = None
```

Add helper functions near `_wait_for_capture`:

```python
def _wait_for_capture_outcome(
    state: _CaptureState,
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
    fast_stable_options: bool,
) -> _CaptureWaitOutcome:
    if not fast_stable_options:
        return _CaptureWaitOutcome(
            capture=_wait_for_capture(
                state,
                page,
                deadline,
                poll_interval_seconds=poll_interval_seconds,
            )
        )

    sampler = _StableVisibleOptionSampler(clock=monotonic)
    while not state.completed and monotonic() < deadline:
        if state.best_result is not None and _search_result_count(state.best_result.payload) > 0:
            decision = sampler.sample(
                _visible_options_from_page(page, deadline=deadline),
                state.best_result.payload,
            )
            if decision is not None:
                return _CaptureWaitOutcome(
                    capture=state.best_result,
                    early_option=decision.option,
                    early_bound_key=decision.bound_key,
                )
        remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        if wait_ms <= 0:
            break
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]

    if state.best_result is None:
        raise _timeout_error()
    if state.completed:
        return _CaptureWaitOutcome(capture=state.best_result)
    return _CaptureWaitOutcome(
        capture=TravelokaCaptureResult(
            payload=state.best_result.payload,
            source_path=state.best_result.source_path,
            search_completed=state.best_result.search_completed,
            timed_out=True,
            partial_failure_type=state.best_result.partial_failure_type,
        )
    )
```

Keep `_wait_for_capture` as the conservative compatibility wrapper:

```python
def _wait_for_capture(
    state: _CaptureState,
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
) -> TravelokaCaptureResult:
    return _wait_for_capture_outcome(
        state,
        page,
        deadline,
        poll_interval_seconds=poll_interval_seconds,
        fast_stable_options=False,
    ).capture
```

- [ ] **Step 4: Integrate outcomes into selected round-trip flow**

In `TravelokaAdapter.__init__`, store the flag at construction time:

```python
        self._fast_stable_options = _fast_stable_options_enabled()
```

Replace the outbound `_wait_for_capture(...)` call in `_search_selected_round_trip` with:

```python
                outbound_outcome = _wait_for_capture_outcome(
                    state,
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                    fast_stable_options=self._fast_stable_options,
                )
                outbound_capture = outbound_outcome.capture
```

Use the early option when present:

```python
            if outbound_outcome.early_option is not None:
                outbound_option = outbound_outcome.early_option
                outbound_key = outbound_outcome.early_bound_key
            else:
                outbound_option = _cheapest_visible_option(
                    _visible_options_from_page(page, deadline=deadline)
                )
                if outbound_option is None:
                    return _partial_round_trip_result(
                        outbound_capture,
                        "outbound_selection_unavailable",
                    )
                outbound_key = _bind_visible_option_to_payload(
                    outbound_option,
                    outbound_capture.payload,
                )
            if outbound_key is None:
                return _partial_round_trip_result(
                    outbound_capture,
                    "selected_outbound_binding_unavailable",
                )
```

Apply the same pattern for return capture and return option selection, returning `return_selection_unavailable` or `selected_return_binding_unavailable` exactly as the existing flow does.

- [ ] **Step 5: Add timing phase wrappers**

Wrap major `_search` and `_search_selected_round_trip` phases with `self._phase_recorder.phase(...)`. Use these exact phase names:

```python
"browser_launch"
"context_page_setup"
"initial_navigation"
"outbound_capture_wait"
"outbound_visible_option_discovery"
"outbound_binding"
"outbound_click_transition"
"return_capture_wait"
"return_visible_option_discovery"
"return_binding"
"return_click_transition"
"final_total_read"
"cleanup"
```

For phases where the code already calls helper functions, wrap the smallest existing block. Example for final total:

```python
            with self._phase_recorder.phase("final_total_read"):
                final_total = _wait_for_final_total(
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                    before_texts=before_final_total_texts,
                )
```

In `finally`, record cleanup:

```python
        finally:
            with self._phase_recorder.phase("cleanup"):
                _close_quietly(context)
                _close_quietly(browser)
```

- [ ] **Step 6: Run integration tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_round_trip_default_waits_conservatively_for_capture_completion tests/test_traveloka_adapter.py::test_round_trip_fast_flag_enables_stable_capture_outcome tests/test_traveloka_adapter.py::test_wait_for_capture_outcome_returns_early_stable_option tests/test_traveloka_adapter.py::test_round_trip_selects_cheapest_visible_outbound_and_return -v
```

Expected: PASS.

- [ ] **Step 7: Run Traveloka adapter suite**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "feat: add opt-in Traveloka stable option early proceed"
```

## Task 5: Benchmark Script

**Files:**
- Create: `scripts/benchmark_traveloka_browser_optimization.py`
- Create: `tests/test_traveloka_benchmark.py`

- [ ] **Step 1: Write failing benchmark tests**

Create `tests/test_traveloka_benchmark.py`:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

from cheapy.models import ProviderResult, ProviderStatusCode


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "benchmark_traveloka_browser_optimization.py"
)


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location(
        "benchmark_traveloka_browser_optimization",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_traveloka_benchmark_route_matrix_matches_spec() -> None:
    module = _load_benchmark_module()

    assert [
        (route.origin, route.destination, route.departure_date, route.return_date)
        for route in module.ROUTE_MATRIX
    ] == [
        ("CXR", "BKK", "2026-06-10", "2026-06-15"),
        ("SGN", "BKK", "2026-06-12", "2026-06-17"),
        ("HAN", "BKK", "2026-06-13", "2026-06-18"),
        ("CXR", "SGN", "2026-06-20", "2026-06-25"),
        ("SGN", "SIN", "2026-07-01", "2026-07-06"),
        ("HAN", "SIN", "2026-07-03", "2026-07-08"),
        ("DAD", "KUL", "2026-07-05", "2026-07-10"),
        ("SGN", "TPE", "2026-07-07", "2026-07-12"),
        ("SGN", "HKG", "2026-07-09", "2026-07-14"),
        ("SGN", "NRT", "2026-07-11", "2026-07-18"),
    ]


def test_traveloka_benchmark_sets_fast_mode_env(monkeypatch) -> None:
    module = _load_benchmark_module()
    seen_env_values: list[str | None] = []

    class FakeAdapter:
        phase_timings = ()

    class FakeProvider:
        async def search_exact_round_trip(self, request):
            seen_env_values.append(
                module.os.environ.get("TRAVELOKA_FAST_STABLE_OPTIONS")
            )
            return ProviderResult(
                provider_name="traveloka",
                capability="exact_round_trip",
                status=ProviderStatusCode.SUCCESS,
                offers=[],
                warnings=[],
                errors=[],
                duration_ms=1,
                retryable=False,
            )

    def provider_factory(timeout_seconds: float):
        return FakeProvider(), FakeAdapter()

    route = module.RouteCase("CXR", "BKK", "2026-06-10", "2026-06-15")

    module.asyncio.run(
        module.run_mode(
            route,
            mode="conservative",
            timeout_seconds=45.0,
            provider_factory=provider_factory,
        )
    )
    module.asyncio.run(
        module.run_mode(
            route,
            mode="fast",
            timeout_seconds=45.0,
            provider_factory=provider_factory,
        )
    )

    assert seen_env_values == ["0", "1"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_benchmark.py -v
```

Expected: FAIL with `FileNotFoundError` for `scripts/benchmark_traveloka_browser_optimization.py`.

- [ ] **Step 3: Implement benchmark script**

Create directory `scripts` if it does not exist, then create `scripts/benchmark_traveloka_browser_optimization.py`:

```python
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from statistics import mean, median
from time import perf_counter
from typing import Callable

from cheapy.models import ProviderResult, ProviderStatusCode
from cheapy.providers.base import ProviderExactRoundTripRequest
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.provider import TravelokaProvider


TRAVELOKA_FAST_STABLE_OPTIONS_ENV = "TRAVELOKA_FAST_STABLE_OPTIONS"
TRANSIENT_FAILURE_TYPES = {
    "blocked",
    "browser_unavailable",
    "navigation_failed",
    "rate_limited",
    "timeout",
    "transport_error",
}


@dataclass(frozen=True)
class RouteCase:
    origin: str
    destination: str
    departure_date: str
    return_date: str


ROUTE_MATRIX = (
    RouteCase("CXR", "BKK", "2026-06-10", "2026-06-15"),
    RouteCase("SGN", "BKK", "2026-06-12", "2026-06-17"),
    RouteCase("HAN", "BKK", "2026-06-13", "2026-06-18"),
    RouteCase("CXR", "SGN", "2026-06-20", "2026-06-25"),
    RouteCase("SGN", "SIN", "2026-07-01", "2026-07-06"),
    RouteCase("HAN", "SIN", "2026-07-03", "2026-07-08"),
    RouteCase("DAD", "KUL", "2026-07-05", "2026-07-10"),
    RouteCase("SGN", "TPE", "2026-07-07", "2026-07-12"),
    RouteCase("SGN", "HKG", "2026-07-09", "2026-07-14"),
    RouteCase("SGN", "NRT", "2026-07-11", "2026-07-18"),
)


ProviderFactory = Callable[[float], tuple[TravelokaProvider, TravelokaAdapter]]


def make_provider(timeout_seconds: float) -> tuple[TravelokaProvider, TravelokaAdapter]:
    adapter = TravelokaAdapter(timeout_seconds=timeout_seconds)
    return TravelokaProvider(adapter=adapter, timeout_seconds=timeout_seconds), adapter


async def run_mode(
    route: RouteCase,
    *,
    mode: str,
    timeout_seconds: float,
    provider_factory: ProviderFactory = make_provider,
) -> dict[str, object]:
    os.environ[TRAVELOKA_FAST_STABLE_OPTIONS_ENV] = "1" if mode == "fast" else "0"
    provider, adapter = provider_factory(timeout_seconds)
    request = ProviderExactRoundTripRequest(
        origin=route.origin,
        destination=route.destination,
        departure_date=route.departure_date,
        return_date=route.return_date,
    )
    started_at = perf_counter()
    result = await provider.search_exact_round_trip(request)
    wall_duration_ms = round((perf_counter() - started_at) * 1000)
    failure_types = _failure_types(result)
    return {
        "route": asdict(route),
        "mode": mode,
        "status": result.status.value,
        "duration_ms": result.duration_ms,
        "wall_duration_ms": wall_duration_ms,
        "offer_count": len(result.offers),
        "failure_types": failure_types,
        "transient": any(value in TRANSIENT_FAILURE_TYPES for value in failure_types),
        "phase_timings": [asdict(record) for record in adapter.phase_timings],
    }


async def run_route_pair(
    route: RouteCase,
    *,
    timeout_seconds: float,
    provider_factory: ProviderFactory = make_provider,
) -> dict[str, object]:
    conservative = await run_mode(
        route,
        mode="conservative",
        timeout_seconds=timeout_seconds,
        provider_factory=provider_factory,
    )
    fast = await run_mode(
        route,
        mode="fast",
        timeout_seconds=timeout_seconds,
        provider_factory=provider_factory,
    )
    return {"route": asdict(route), "conservative": conservative, "fast": fast}


async def run_benchmark(
    *,
    timeout_seconds: float,
    iterations: int,
    concurrency: int,
) -> dict[str, object]:
    semaphore = asyncio.Semaphore(concurrency)
    routes = [route for _ in range(iterations) for route in ROUTE_MATRIX]

    async def guarded(route: RouteCase) -> dict[str, object]:
        async with semaphore:
            return await run_route_pair(route, timeout_seconds=timeout_seconds)

    route_pairs = await asyncio.gather(*(guarded(route) for route in routes))
    return {"summary": summarize(route_pairs), "route_pairs": route_pairs}


def summarize(route_pairs: list[dict[str, object]]) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    for pair in route_pairs:
        runs.append(pair["conservative"])  # type: ignore[arg-type]
        runs.append(pair["fast"])  # type: ignore[arg-type]
    durations = [int(run["duration_ms"]) for run in runs]
    failure_counter: Counter[str] = Counter()
    success_count = 0
    partial_or_failure_count = 0
    for run in runs:
        if run["status"] == ProviderStatusCode.SUCCESS.value:
            success_count += 1
        else:
            partial_or_failure_count += 1
        failure_counter.update(str(value) for value in run["failure_types"])
    return {
        "success_count": success_count,
        "partial_or_failure_count": partial_or_failure_count,
        "failure_types": dict(sorted(failure_counter.items())),
        "average_duration_ms": round(mean(durations), 2) if durations else 0,
        "p50_duration_ms": round(median(durations), 2) if durations else 0,
        "p95_duration_ms": _percentile(durations, 95),
        "phase_timing_breakdown": _phase_breakdown(runs),
    }


def _failure_types(result: ProviderResult) -> list[str]:
    values: list[str] = []
    for error in result.errors:
        value = error.details.get("failure_type")
        if isinstance(value, str) and value:
            values.append(value)
    return values


def _percentile(values: list[int], percentile: int) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = round((percentile / 100) * (len(ordered) - 1))
    return float(ordered[index])


def _phase_breakdown(runs: list[dict[str, object]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[int]] = {}
    for run in runs:
        for record in run["phase_timings"]:
            if not isinstance(record, dict):
                continue
            phase = record.get("phase")
            duration_ms = record.get("duration_ms")
            if isinstance(phase, str) and isinstance(duration_ms, int):
                grouped.setdefault(phase, []).append(duration_ms)
    return {
        phase: {
            "average_duration_ms": round(mean(values), 2),
            "p50_duration_ms": round(median(values), 2),
            "p95_duration_ms": _percentile(values, 95),
        }
        for phase, values in sorted(grouped.items())
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Traveloka conservative and fast browser modes.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than 0")
    if args.iterations <= 0:
        parser.error("--iterations must be greater than 0")
    if args.concurrency <= 0:
        parser.error("--concurrency must be greater than 0")
    if args.concurrency != 1:
        parser.error(
            "--concurrency must be 1 because TRAVELOKA_FAST_STABLE_OPTIONS is process-wide"
        )
    return args


def main() -> None:
    args = parse_args()
    report = asyncio.run(
        run_benchmark(
            timeout_seconds=args.timeout_seconds,
            iterations=args.iterations,
            concurrency=args.concurrency,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run benchmark tests**

Run:

```bash
uv run pytest tests/test_traveloka_benchmark.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/benchmark_traveloka_browser_optimization.py tests/test_traveloka_benchmark.py
git commit -m "feat: add Traveloka browser optimization benchmark"
```

## Task 6: Regression Verification

**Files:**
- Modify only if a verification failure proves a defect in files changed by Tasks 1-5.

- [ ] **Step 1: Run targeted Traveloka tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS. If a Traveloka test fails, fix only the changed helper or adapter code that caused the regression, then rerun this command.

- [ ] **Step 2: Run search integration tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS. If search ranking or provider aggregation fails, inspect whether a Contract V1 field changed. The correct fix is to preserve existing provider output shape, not to update the contract.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS, with opt-in live tests still skipped unless `CHEAPY_RUN_LIVE_TESTS=1`.

- [ ] **Step 4: Run optional live benchmark**

Run:

```bash
uv run python scripts/benchmark_traveloka_browser_optimization.py \
  --timeout-seconds 45 \
  --iterations 1 \
  --concurrency 1
```

Expected: JSON report on stdout with `summary`, `route_pairs`, per-mode `failure_types`, and `phase_timing_breakdown`. Transient Traveloka live failures remain visible in the report.

- [ ] **Step 5: Check acceptance criteria manually from benchmark report**

Confirm these facts from the JSON report:

```text
1. Conservative mode still runs with TRAVELOKA_FAST_STABLE_OPTIONS=0.
2. Fast mode runs with TRAVELOKA_FAST_STABLE_OPTIONS=1.
3. Every comparable Traveloka success has exactly one offer.
4. Comparable Traveloka success offers have comparable=true, rank_within_currency=1, and global_rank=1.
5. Fast mode does not introduce a new non-transient failure type on route pairs where conservative mode succeeds.
6. Phase timing breakdown shows where duration changed.
```

- [ ] **Step 6: Commit verification fixes if needed**

If Tasks 1-5 already pass all commands, no code commit is needed in this step. If a regression fix was required, commit it:

```bash
git add cheapy/providers/traveloka/adapter.py cheapy/providers/traveloka/timing.py scripts/benchmark_traveloka_browser_optimization.py tests/test_traveloka_adapter.py tests/test_traveloka_benchmark.py
git commit -m "fix: stabilize Traveloka browser optimization regressions"
```

## Implementation Guardrails

- Do not change Contract V1 models.
- Do not make raw Traveloka round-trip partial offers comparable.
- Do not add HTTP replay, cookies, login, captcha solving, proxying, retries, or persistent browser state.
- Keep `TRAVELOKA_FAST_STABLE_OPTIONS=1` as the only activation path for early proceed.
- Keep stale final-total filtering via `before_texts`.
- Keep `SAFE_PARTIAL_FAILURE_TYPES` in `cheapy/providers/traveloka/provider.py` unchanged unless an existing safe type is accidentally missing from the codebase.
- Keep benchmark retries at zero; report live failures instead of hiding them.

## Final Verification Commands

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
uv run pytest tests/test_search.py -v
uv run pytest -v
uv run python scripts/benchmark_traveloka_browser_optimization.py --timeout-seconds 45 --iterations 1 --concurrency 1
```
