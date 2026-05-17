# Traveloka Return Selection Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Traveloka exact round-trip selection move past the no-op outbound click, capture return inventory, and read a final selected total without making raw partial round-trip offers comparable.

**Architecture:** Keep the existing Traveloka adapter/provider/normalizer boundaries. Add Traveloka-specific DOM activation and bounded transition guards inside `adapter.py`, then expand final-total parsing to selected Traveloka price surfaces. Provider changes are limited to safe partial-failure metadata.

**Tech Stack:** Python 3.11+, `uv`, pytest, CloakBrowser/Playwright-compatible page and locator objects, Cheapy Contract V1 models.

---

## Reviewed Inputs

- Spec: `docs/superpowers/specs/2026-05-15-cheapy-traveloka-return-selection-recovery-design.md`
- Subagent review result: PASS after patching failure taxonomy and final-total parsing scope.
- Relevant files:
  - Modify: `cheapy/providers/traveloka/adapter.py`
  - Modify: `cheapy/providers/traveloka/provider.py`
  - Modify: `tests/test_traveloka_adapter.py`
  - Modify: `tests/test_traveloka_provider.py`

## File Structure

- `cheapy/providers/traveloka/adapter.py`
  - Owns browser activation, capture state, transition waits, and final selected total reads.
  - Keep public dataclasses unchanged.
  - Add private helpers only; do not add Contract V1 fields.

- `cheapy/providers/traveloka/provider.py`
  - Add `outbound_selection_transition_unavailable` to safe partial failure types.
  - Keep existing metadata mapping; the new failure maps to non-retryable provider failed metadata.

- `tests/test_traveloka_adapter.py`
  - Extend fake locators/pages to model `locator.evaluate()`, URL/body transition markers, and selector collections.
  - Add focused offline tests for activation, transition taxonomy, and selected-total parsing.

- `tests/test_traveloka_provider.py`
  - Add provider-level regression for preserving the new safe failure type.

## Task 1: Replace Force Click With Traveloka DOM Activation

**Files:**
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `cheapy/providers/traveloka/adapter.py`

- [ ] **Step 1: Write failing activation tests**

In `tests/test_traveloka_adapter.py`, update the fake locator classes near the top so locators can record browser-side activation:

```python
class FakeLocator:
    def __init__(self) -> None:
        self.click_kwargs: list[dict[str, object]] = []
        self.evaluate_calls: list[dict[str, object]] = []
        self.evaluate_scripts: list[str] = []
        self.clicked = False

    def click(self, **kwargs: object) -> None:
        self.clicked = True
        self.click_kwargs.append(kwargs)

    def evaluate(self, script: str, **kwargs: object) -> None:
        self.clicked = True
        self.evaluate_scripts.append(script)
        self.evaluate_calls.append(kwargs)
```

Replace `EmittingFakeLocator` with this version so existing round-trip tests can emit responses from synthetic activation:

```python
class EmittingFakeLocator(FakeLocator):
    def __init__(self, on_click: object | None = None) -> None:
        super().__init__()
        self._on_click = on_click

    def click(self, **kwargs: object) -> None:
        super().click(**kwargs)
        if callable(self._on_click):
            self._on_click()

    def evaluate(self, script: str, **kwargs: object) -> None:
        super().evaluate(script, **kwargs)
        if callable(self._on_click):
            self._on_click()
```

Replace the two existing `_click_visible_option` tests with:

```python
def test_click_visible_option_dispatches_traveloka_activation_sequence() -> None:
    locator = FakeLocator()
    option = _visible_option(key="out-1", locator=locator)

    traveloka_adapter._click_visible_option(option, timeout_ms=3210)

    assert locator.click_kwargs == []
    assert locator.evaluate_calls == [{"timeout": 3210}]
    script = locator.evaluate_scripts[0]
    for event_name in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
        assert event_name in script


def test_click_visible_option_scrolls_and_caps_live_activation_timeout() -> None:
    locator = ScrollableFakeLocator()
    option = _visible_option(key="out-1", locator=locator)

    traveloka_adapter._click_visible_option(option, timeout_ms=45_000)

    assert locator.scroll_kwargs == [{"timeout": 10_000}]
    assert locator.evaluate_calls == [{"timeout": 10_000}]
    assert locator.click_kwargs == []
```

- [ ] **Step 2: Run activation tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_click_visible_option_dispatches_traveloka_activation_sequence tests/test_traveloka_adapter.py::test_click_visible_option_scrolls_and_caps_live_activation_timeout -v
```

Expected: both tests fail because `_click_visible_option()` still calls `click(force=True)` and does not call `evaluate()`.

- [ ] **Step 3: Implement synthetic activation**

In `cheapy/providers/traveloka/adapter.py`, add this constant near the other Traveloka selectors:

```python
TRAVELOKA_OPTION_ACTIVATION_SCRIPT = """
node => {
  const base = {bubbles: true, cancelable: true, composed: true, view: window};
  const pointer = (type, buttons) => {
    if (typeof PointerEvent === 'function') {
      return new PointerEvent(type, Object.assign({}, base, {
        button: 0,
        buttons,
        pointerType: 'mouse',
        isPrimary: true,
      }));
    }
    return new MouseEvent(type, Object.assign({}, base, {button: 0, buttons}));
  };
  node.dispatchEvent(pointer('pointerdown', 1));
  node.dispatchEvent(new MouseEvent(
    'mousedown',
    Object.assign({}, base, {button: 0, buttons: 1})
  ));
  node.dispatchEvent(pointer('pointerup', 0));
  node.dispatchEvent(new MouseEvent(
    'mouseup',
    Object.assign({}, base, {button: 0, buttons: 0})
  ));
  node.dispatchEvent(new MouseEvent(
    'click',
    Object.assign({}, base, {button: 0, buttons: 0})
  ));
}
"""
```

Replace `_click_visible_option()` with:

```python
def _click_visible_option(
    option: TravelokaVisibleOption,
    *,
    timeout_ms: int = VISIBLE_OPTION_CLICK_TIMEOUT_MS,
) -> None:
    click_timeout_ms = max(1, min(timeout_ms, VISIBLE_OPTION_CLICK_TIMEOUT_MS))
    scroll = getattr(option.locator, "scroll_into_view_if_needed", None)
    if scroll is not None:
        try:
            scroll(timeout=click_timeout_ms)
        except Exception:
            pass

    evaluate = getattr(option.locator, "evaluate", None)
    if evaluate is not None:
        try:
            evaluate(TRAVELOKA_OPTION_ACTIVATION_SCRIPT, timeout=click_timeout_ms)
        except TypeError:
            evaluate(TRAVELOKA_OPTION_ACTIVATION_SCRIPT)
        return

    option.locator.click(timeout=click_timeout_ms)  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run activation tests and verify pass**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_click_visible_option_dispatches_traveloka_activation_sequence tests/test_traveloka_adapter.py::test_click_visible_option_scrolls_and_caps_live_activation_timeout -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "fix: activate Traveloka inventory buttons via DOM events" -m "AI-Commit: GPT-5 Codex"
```

## Task 2: Add Outbound Transition Guard And Failure Taxonomy

**Files:**
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `cheapy/providers/traveloka/adapter.py`

- [ ] **Step 1: Extend page fakes for body text and URL state**

In `tests/test_traveloka_adapter.py`, update `FakePage.__init__()` and `goto()`:

```python
class FakePage:
    def __init__(self, responses: list[FakeResponse], content: str | None = None) -> None:
        self.responses = responses
        self.handlers: dict[str, object] = {}
        self.goto_urls: list[str] = []
        self.wait_calls = 0
        self.url = ""
        self._content = content or "<html><body>flight search</body></html>"

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.url = url
        self.goto_urls.append(url)
        handler = self.handlers["response"]
        for response in self.responses:
            handler(response)
```

Update `LocatorFakePage.locator()` so selector locators can be either a single locator or a collection:

```python
    def locator(self, selector: str) -> object:
        self.locator_calls.append(selector)
        if selector.startswith("button:has-text"):
            if not self.option_groups:
                return FakeLocatorCollection([])
            return FakeLocatorCollection(self.option_groups.pop(0))
        if selector in self.selector_locators:
            return self.selector_locators[selector]
        return EmptyFakeLocator()
```

- [ ] **Step 2: Write failing no-transition and post-transition timeout tests**

Add these tests near the existing round-trip partial tests:

```python
def test_round_trip_returns_partial_when_outbound_activation_does_not_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    outbound_click = EmittingFakeLocator()
    monkeypatch.setattr(
        traveloka_adapter,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(
                key="tv-1",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, traveloka_adapter.TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "outbound_selection_transition_unavailable"
    assert outbound_click.clicked is True


def test_round_trip_keeps_return_capture_timeout_after_outbound_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ],
        selector_locators={
            "body": TextFakeLocator(text="Your Flights\nChange departure flight")
        },
    )
    outbound_click = EmittingFakeLocator()
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(
                key="tv-1",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, traveloka_adapter.TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "return_capture_timeout"
    assert outbound_click.clicked is True
```

- [ ] **Step 3: Run transition tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_round_trip_returns_partial_when_outbound_activation_does_not_transition tests/test_traveloka_adapter.py::test_round_trip_keeps_return_capture_timeout_after_outbound_transition -v
```

Expected: the first test fails because no new failure type exists; the second may fail while the flow still treats all post-click timeouts the same.

- [ ] **Step 4: Implement bounded outbound transition wait**

In `cheapy/providers/traveloka/adapter.py`, add this constant near `VISIBLE_OPTION_CLICK_TIMEOUT_MS`:

```python
SELECTION_TRANSITION_TIMEOUT_MS = 10_000
```

Add these helpers near `_wait_after_return_selection()`:

```python
def _wait_for_outbound_selection_transition(
    state: _CaptureState,
    page: object,
    selected_key: str | None,
    deadline: float,
    *,
    poll_interval_seconds: float,
) -> bool:
    transition_deadline = min(
        deadline,
        monotonic() + (SELECTION_TRANSITION_TIMEOUT_MS / 1000),
    )
    while monotonic() < transition_deadline:
        if state.best_result is not None:
            return True
        if _outbound_selection_transitioned(
            page,
            selected_key,
            deadline=transition_deadline,
        ):
            return True
        remaining_ms = _remaining_timeout_ms(
            transition_deadline,
            raise_on_expired=False,
        )
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]
    return state.best_result is not None or _outbound_selection_transitioned(
        page,
        selected_key,
        deadline=transition_deadline,
    )


def _outbound_selection_transitioned(
    page: object,
    selected_key: str | None,
    *,
    deadline: float | None = None,
) -> bool:
    page_url = str(getattr(page, "url", ""))
    if selected_key and f"#SC{selected_key}" in page_url:
        return True
    body_text = _read_body_text(
        page,
        timeout_ms=250,
        deadline=deadline,
    )
    return "Change departure flight" in body_text


def _read_body_text(
    page: object,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> str:
    text_timeout_ms = _dom_operation_timeout_ms(
        timeout_ms=timeout_ms,
        deadline=deadline,
    )
    if text_timeout_ms is None:
        return ""
    try:
        return page.locator("body").inner_text(timeout=text_timeout_ms)  # type: ignore[attr-defined]
    except Exception:
        return ""
```

In `_search_selected_round_trip()`, replace the block immediately after outbound activation with:

```python
            state.reset()
            _click_visible_option(
                outbound_option,
                timeout_ms=_remaining_timeout_ms(deadline),
            )
            if not _wait_for_outbound_selection_transition(
                state,
                page,
                outbound_key,
                deadline,
                poll_interval_seconds=self._poll_interval_seconds,
            ):
                return _partial_round_trip_result(
                    outbound_capture,
                    "outbound_selection_transition_unavailable",
                )
            try:
                return_capture = _wait_for_capture(
                    state,
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                )
```

- [ ] **Step 5: Run transition tests and verify pass**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_round_trip_returns_partial_when_outbound_activation_does_not_transition tests/test_traveloka_adapter.py::test_round_trip_keeps_return_capture_timeout_after_outbound_transition -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "fix: classify Traveloka outbound no-transition failures" -m "AI-Commit: GPT-5 Codex"
```

## Task 3: Read Final Total From Scoped Traveloka Surfaces

**Files:**
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `cheapy/providers/traveloka/adapter.py`

- [ ] **Step 1: Write failing final-total parser tests**

Add these tests near the existing `_read_final_total` tests:

```python
def test_read_final_total_reads_live_label_total_and_ignores_addon() -> None:
    price_label = TextFakeLocator(text="+ USD 0.00/pax\nTotal USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [price_label]
            )
        },
    )

    result = traveloka_adapter._read_final_total(page, timeout_ms=456)

    assert result == (Decimal("239.68"), "USD")
    assert price_label.inner_text_kwargs == [{"timeout": 456}]


def test_read_final_total_reads_selected_summary_round_trip_price() -> None:
    selected_summary = TextFakeLocator(
        text=(
            "Departure SGN to BKK\n"
            "Return BKK to SGN\n"
            "Round-trip price USD 239.68/pax"
        )
    )
    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='bundle-summary-tray']": selected_summary},
    )

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_final_total_ignores_unselected_round_trip_card_price() -> None:
    unselected_card_price = TextFakeLocator(
        text="Round-trip price USD 999.00/pax\nChoose\nChoose"
    )
    selected_price = TextFakeLocator(text="+ USD 0.00/pax\nTotal USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [unselected_card_price, selected_price]
            )
        },
    )

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )
```

- [ ] **Step 2: Run final-total tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_read_final_total_reads_live_label_total_and_ignores_addon tests/test_traveloka_adapter.py::test_read_final_total_reads_selected_summary_round_trip_price tests/test_traveloka_adapter.py::test_read_final_total_ignores_unselected_round_trip_card_price -v
```

Expected: tests fail because `_read_final_total()` does not inspect these selectors and does not iterate label price collections.

- [ ] **Step 3: Implement scoped explicit-total parsing**

In `cheapy/providers/traveloka/adapter.py`, add these regex constants near the existing price regexes:

```python
_EXPLICIT_TOTAL_PRICE_RE = re.compile(
    r"\btotal\b\s*((?:USD|\$|VND|₫)\s*\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_EXPLICIT_SUMMARY_PRICE_RE = re.compile(
    r"\b(?:total|round-trip\s+price)\b\s*((?:USD|\$|VND|₫)\s*\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
```

Add these helpers above `_read_final_total()`:

```python
def _parse_explicit_price(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[Decimal, str] | None:
    normalized = " ".join(text.replace("\xa0", " ").split())
    match = pattern.search(normalized)
    if match is None:
        return None
    try:
        return _parse_visible_price(match.group(1))
    except Exception:
        return None


def _locator_texts(
    page: object,
    selector: str,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> list[str]:
    try:
        locators = page.locator(selector)  # type: ignore[attr-defined]
    except Exception:
        return []
    try:
        count = locators.count()
    except Exception:
        count = 1

    texts: list[str] = []
    for index in range(max(1, count)):
        locator = locators
        if count != 1:
            try:
                locator = locators.nth(index)
            except Exception:
                continue
        elif hasattr(locators, "first"):
            try:
                locator = locators.first()
            except Exception:
                locator = locators
        text_timeout_ms = _dom_operation_timeout_ms(
            timeout_ms=timeout_ms,
            deadline=deadline,
        )
        if text_timeout_ms is None:
            break
        try:
            texts.append(locator.inner_text(timeout=text_timeout_ms))
        except Exception:
            continue
    return texts
```

Replace `_read_final_total()` with:

```python
def _read_final_total(
    page: object,
    *,
    timeout_ms: int = 1000,
    deadline: float | None = None,
) -> tuple[Decimal, str] | None:
    timeout_ms = max(1, timeout_ms)
    total_selectors = (
        "[data-testid*='selected'][data-testid*='total']",
        "[data-testid*='final'][data-testid*='total']",
        "[data-testid*='checkout'][data-testid*='total']",
        "[aria-label*='selected' i][aria-label*='total' i]",
        "[aria-label*='final' i][aria-label*='total' i]",
        "[aria-label*='checkout' i][aria-label*='total' i]",
        "text=/selected\\s+(?:final\\s+)?total/i",
        "text=/final\\s+total/i",
        "text=/checkout\\s+total/i",
    )
    for selector in total_selectors:
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=timeout_ms,
            deadline=deadline,
        ):
            try:
                amount, currency = _parse_visible_price(text)
            except Exception:
                continue
            return amount, currency

    for text in _locator_texts(
        page,
        "[data-testid='label_fl_inventory_price']",
        timeout_ms=timeout_ms,
        deadline=deadline,
    ):
        parsed = _parse_explicit_price(text, _EXPLICIT_TOTAL_PRICE_RE)
        if parsed is not None:
            return parsed

    for selector in (
        "[data-testid='bundle-summary-tray']",
        "[data-testid='flight-summary-tray-routes-v2']",
        "[data-testid*='summary'][data-testid*='tray']",
    ):
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=timeout_ms,
            deadline=deadline,
        ):
            parsed = _parse_explicit_price(text, _EXPLICIT_SUMMARY_PRICE_RE)
            if parsed is not None:
                return parsed
    return None
```

- [ ] **Step 4: Run final-total tests and verify pass**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_read_final_total_reads_live_label_total_and_ignores_addon tests/test_traveloka_adapter.py::test_read_final_total_reads_selected_summary_round_trip_price tests/test_traveloka_adapter.py::test_read_final_total_ignores_unselected_round_trip_card_price -v
```

Expected: all three tests pass.

- [ ] **Step 5: Run existing final-total tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_read_final_total_prefers_explicit_selected_total_and_uses_bounded_timeout tests/test_traveloka_adapter.py::test_read_final_total_uses_fresh_remaining_deadline_for_each_read tests/test_traveloka_adapter.py::test_read_final_total_ignores_ambiguous_generic_total -v
```

Expected: all existing final-total tests still pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "fix: read Traveloka selected round-trip totals" -m "AI-Commit: GPT-5 Codex"
```

## Task 4: Add Return Selection Transition Guard

**Files:**
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `cheapy/providers/traveloka/adapter.py`

- [ ] **Step 1: Write failing return-transition tests**

Add these tests near the existing final-total and round-trip selection tests:

```python
def test_wait_for_return_selection_transition_recognizes_selected_summary() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
                text="Return BKK to SGN\nChange return flight"
            )
        },
    )

    assert traveloka_adapter._wait_for_return_selection_transition(
        page,
        deadline=traveloka_adapter.monotonic() + 1,
        poll_interval_seconds=0.001,
    ) is True


def test_wait_for_return_selection_transition_times_out_without_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = LocatorFakePage([], selector_locators={"body": TextFakeLocator(text="Choose")})
    monkeypatch.setattr(
        traveloka_adapter,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )

    assert traveloka_adapter._wait_for_return_selection_transition(
        page,
        deadline=traveloka_adapter.monotonic() + 1,
        poll_interval_seconds=0.001,
    ) is False
```

- [ ] **Step 2: Run return-transition tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_wait_for_return_selection_transition_recognizes_selected_summary tests/test_traveloka_adapter.py::test_wait_for_return_selection_transition_times_out_without_marker -v
```

Expected: tests fail because `_wait_for_return_selection_transition()` does not exist.

- [ ] **Step 3: Implement return-transition wait**

In `cheapy/providers/traveloka/adapter.py`, add:

```python
def _wait_for_return_selection_transition(
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
) -> bool:
    transition_deadline = min(
        deadline,
        monotonic() + (SELECTION_TRANSITION_TIMEOUT_MS / 1000),
    )
    while monotonic() < transition_deadline:
        if _return_selection_transitioned(page, deadline=transition_deadline):
            return True
        remaining_ms = _remaining_timeout_ms(
            transition_deadline,
            raise_on_expired=False,
        )
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]
    return _return_selection_transitioned(page, deadline=transition_deadline)


def _return_selection_transitioned(
    page: object,
    *,
    deadline: float | None = None,
) -> bool:
    for selector in (
        "[data-testid='flight-summary-container-1_selected']",
        "[data-testid='bundle-summary-tray']",
        "[data-testid='flight-summary-tray-routes-v2']",
    ):
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=250,
            deadline=deadline,
        ):
            if "Return" in text or "Change return flight" in text:
                return True
    return "Change return flight" in _read_body_text(
        page,
        timeout_ms=250,
        deadline=deadline,
    )
```

In `_search_selected_round_trip()`, replace the `_wait_after_return_selection()` call with:

```python
            if not _wait_for_return_selection_transition(
                page,
                deadline,
                poll_interval_seconds=self._poll_interval_seconds,
            ):
                return _partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )
```

Leave `_wait_after_return_selection()` in place only if tests still reference it; otherwise remove it after the full suite passes.

- [ ] **Step 4: Update round-trip adapter tests that need post-return markers**

For tests that expect final total reading after return click, add either a return marker to the fake page:

```python
selector_locators={
    "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
        text="Return BKK to SGN\nChange return flight"
    ),
    "[data-testid*='selected'][data-testid*='total']": TextFakeLocator(
        text="Selected final total USD 321.09"
    ),
}
```

or monkeypatch the transition helper in tests that are not about DOM markers:

```python
monkeypatch.setattr(
    traveloka_adapter,
    "_wait_for_return_selection_transition",
    lambda page_arg, deadline, **kwargs: True,
)
```

- [ ] **Step 5: Run return-transition and selected-round-trip tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_wait_for_return_selection_transition_recognizes_selected_summary tests/test_traveloka_adapter.py::test_wait_for_return_selection_transition_times_out_without_marker tests/test_traveloka_adapter.py::test_round_trip_selects_cheapest_visible_outbound_and_return tests/test_traveloka_adapter.py::test_round_trip_returns_partial_when_final_total_is_unavailable -v
```

Expected: all listed tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "fix: wait for Traveloka return selection state" -m "AI-Commit: GPT-5 Codex"
```

## Task 5: Preserve New Provider Partial Failure Type

**Files:**
- Modify: `tests/test_traveloka_provider.py`
- Modify: `cheapy/providers/traveloka/provider.py`

- [ ] **Step 1: Write failing provider test**

Add this test to `tests/test_traveloka_provider.py` near other partial-failure tests:

```python
def test_traveloka_provider_preserves_outbound_transition_failure_type() -> None:
    adapter = FakeAdapter(
        _capture(
            _payload(),
            partial_failure_type="outbound_selection_transition_unavailable",
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].retryable is False
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_round_trip",
        "failure_type": "outbound_selection_transition_unavailable",
    }
```

- [ ] **Step 2: Run provider test and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_provider.py::test_traveloka_provider_preserves_outbound_transition_failure_type -v
```

Expected: test fails because `_safe_failure_type()` maps the new value to `partial_failure`.

- [ ] **Step 3: Add the safe failure type**

In `cheapy/providers/traveloka/provider.py`, add the new value to `SAFE_PARTIAL_FAILURE_TYPES`:

```python
        "outbound_selection_transition_unavailable",
```

Do not add it to the timeout branch in `_partial_failure_metadata()`.

- [ ] **Step 4: Run provider test and verify pass**

Run:

```bash
uv run pytest tests/test_traveloka_provider.py::test_traveloka_provider_preserves_outbound_transition_failure_type -v
```

Expected: test passes.

- [ ] **Step 5: Commit Task 5**

```bash
git add cheapy/providers/traveloka/provider.py tests/test_traveloka_provider.py
git commit -m "fix: preserve Traveloka outbound transition failures" -m "AI-Commit: GPT-5 Codex"
```

## Task 6: Regression Sweep And Live Smoke

**Files:**
- Modify only if a regression test exposes a bug in the files already touched.

- [ ] **Step 1: Run focused Traveloka tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: all Traveloka tests pass.

- [ ] **Step 2: Run search aggregation tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: all search tests pass, including comparable/non-comparable ranking behavior.

- [ ] **Step 3: Run full offline suite**

Run:

```bash
uv run pytest -v
```

Expected: full suite passes. If failures appear outside Traveloka files, inspect before editing and do not revert unrelated user changes.

- [ ] **Step 4: Optional live Traveloka smoke**

Run the existing live-provider path for:

```text
origin: CXR
destination: BKK
departure_date: 2026-06-15
return_date: 2026-06-20
```

Expected:

- ideal: `SUCCESS` with exactly one comparable selected round-trip offer
- acceptable partial boundary:
  - `outbound_selection_transition_unavailable`
  - `return_capture_timeout`
  - `return_selection_unavailable`
  - `selected_return_binding_unavailable`
  - `final_round_trip_total_unavailable`
- not acceptable: no-op outbound activation reported as `return_capture_timeout`

- [ ] **Step 5: Final status**

Collect:

```bash
git status --short
```

Expected: only intentional Traveloka implementation/test changes are dirty, or the task commits are present and the remaining dirty files are pre-existing unrelated changes.
