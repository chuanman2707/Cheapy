# Cheapy Traveloka Provider Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Traveloka provider internals into focused modules while preserving selected round-trip behavior and removing legacy fallback debt.

**Architecture:** Keep `TravelokaAdapter` as the browser workflow facade and move cohesive helper groups into `results.py`, `errors.py`, `browser_helpers.py`, `urls.py`, `capture.py`, `inventory.py`, `activation.py`, `selection.py`, and `totals.py`. Migrate selected round-trip tests to the current inventory-card fixture shape before deleting the legacy button fallback.

**Tech Stack:** Python 3.12, pytest, uv, CloakBrowser/Playwright-style sync browser API, Cheapy Contract V1 models.

---

## File Structure

- Create `cheapy/providers/traveloka/results.py`: result dataclasses and partial round-trip result helper.
- Create `cheapy/providers/traveloka/errors.py`: `TravelokaProviderError`, safe provider-local error factories, terminal block detection.
- Create `cheapy/providers/traveloka/browser_helpers.py`: shared deadline, bounded DOM read, body text, and quiet cleanup helpers.
- Create `cheapy/providers/traveloka/urls.py`: Traveloka URL/date/passenger query helpers.
- Create `cheapy/providers/traveloka/capture.py`: first-party response filtering, fare payload validation, capture state, capture wait.
- Create `cheapy/providers/traveloka/inventory.py`: current inventory-card discovery, visible option dataclass, cheapest option, key binding.
- Create `cheapy/providers/traveloka/activation.py`: Traveloka card activation script and click helper.
- Create `cheapy/providers/traveloka/selection.py`: outbound and return transition waits.
- Create `cheapy/providers/traveloka/totals.py`: final selected total parsing, selector tiers, stale text guard.
- Modify `cheapy/providers/traveloka/adapter.py`: remove helper bodies and keep browser workflow only.
- Modify `cheapy/providers/traveloka/provider.py`: import result contracts from `results.py` and errors from `errors.py`.
- Modify `cheapy/providers/traveloka/normalizer.py`: import `TravelokaSelectedRoundTripResult` from `results.py`.
- Modify `tests/test_traveloka_adapter.py`: migrate direct helper imports, split expectations by owning module, migrate selected round-trip fixtures off legacy button fallback.
- Modify `tests/test_traveloka_normalizer.py`: update result import path.
- Modify `tests/test_traveloka_provider.py`: update result/error import paths.
- Modify `README.md` and `README.vi.md`: correct Traveloka browser/timeout documentation.

## Task 1: Baseline And Result Contracts

**Files:**
- Create: `cheapy/providers/traveloka/results.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `cheapy/providers/traveloka/provider.py`
- Modify: `cheapy/providers/traveloka/normalizer.py`
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `tests/test_traveloka_normalizer.py`
- Modify: `tests/test_traveloka_provider.py`

- [ ] **Step 1: Run the current Traveloka baseline**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS before refactor starts. If this fails, stop and inspect the failing test before moving code.

- [ ] **Step 2: Write the failing result contract import test**

In `tests/test_traveloka_adapter.py`, add this import near the other Traveloka imports:

```python
from cheapy.providers.traveloka import results as traveloka_results
```

Add this test near the existing Traveloka result dataclass tests:

```python
def test_traveloka_result_contracts_live_in_results_module() -> None:
    capture = traveloka_results.TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    partial = traveloka_results.partial_round_trip_result(
        capture,
        "return_capture_timeout",
    )
    selected = traveloka_results.TravelokaSelectedRoundTripResult(
        outbound_payload={"data": {"searchResults": [{"id": "out-1"}]}},
        return_payload={"data": {"searchResults": [{"id": "ret-1"}]}},
        selected_outbound_key="out-1",
        selected_return_key="ret-1",
        final_total_amount=Decimal("123.45"),
        final_total_currency="USD",
        source_paths=("/api/v2/flight/search/initial", "/api/v2/flight/search/poll"),
    )

    assert partial.partial_failure_type == "return_capture_timeout"
    assert partial.payload == capture.payload
    assert selected.final_total_currency == "USD"
```

- [ ] **Step 3: Run the new result contract test and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_result_contracts_live_in_results_module -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.providers.traveloka.results'`.

- [ ] **Step 4: Create result contracts module**

Create `cheapy/providers/traveloka/results.py`:

```python
"""Provider-local result contracts for the Traveloka research provider."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TravelokaCaptureResult:
    payload: dict[str, object]
    source_path: str
    search_completed: bool
    timed_out: bool = False
    partial_failure_type: str | None = None


@dataclass(frozen=True)
class TravelokaSelectedRoundTripResult:
    outbound_payload: dict[str, object]
    return_payload: dict[str, object]
    selected_outbound_key: str | None
    selected_return_key: str | None
    final_total_amount: Decimal
    final_total_currency: str
    source_paths: tuple[str, ...]
    timed_out: bool = False


def partial_round_trip_result(
    capture: TravelokaCaptureResult,
    failure_type: str,
) -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload=capture.payload,
        source_path=capture.source_path,
        search_completed=capture.search_completed,
        timed_out=capture.timed_out,
        partial_failure_type=failure_type,
    )
```

- [ ] **Step 5: Update imports and remove duplicate dataclasses**

In `cheapy/providers/traveloka/adapter.py`, remove the two result dataclasses and add:

```python
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
    partial_round_trip_result,
)
```

Replace every `_partial_round_trip_result(` call with `partial_round_trip_result(`.
Delete the old `_partial_round_trip_result` function from `adapter.py`.

In `cheapy/providers/traveloka/provider.py`, replace result imports from `adapter.py` with:

```python
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
```

In `cheapy/providers/traveloka/normalizer.py`, replace the selected result import with:

```python
from cheapy.providers.traveloka.results import TravelokaSelectedRoundTripResult
```

In tests, update result class references incrementally:

```python
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
```

- [ ] **Step 6: Run result contract tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_result_contracts_live_in_results_module tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit result contract extraction**

Run:

```bash
git add cheapy/providers/traveloka/results.py cheapy/providers/traveloka/adapter.py cheapy/providers/traveloka/provider.py cheapy/providers/traveloka/normalizer.py tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py
git commit -m "refactor: extract Traveloka result contracts" -m "Model: GPT-5 Codex"
```

## Task 2: Error Factories And Browser Helpers

**Files:**
- Create: `cheapy/providers/traveloka/errors.py`
- Create: `cheapy/providers/traveloka/browser_helpers.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing ownership tests**

In `tests/test_traveloka_adapter.py`, add:

```python
from cheapy.providers.traveloka import browser_helpers
from cheapy.providers.traveloka import errors as traveloka_errors
```

Add these tests near the phase/error tests:

```python
def test_traveloka_error_factories_live_in_errors_module() -> None:
    timeout_error = traveloka_errors.timeout_error("PlaywrightTimeoutError")
    blocked_error = traveloka_errors.blocked_error(403)

    assert isinstance(timeout_error, traveloka_errors.TravelokaProviderError)
    assert timeout_error.failure_type == "timeout"
    assert timeout_error.exception_type == "PlaywrightTimeoutError"
    assert blocked_error.failure_type == "blocked"
    assert blocked_error.http_status_code == 403
    assert "http" not in blocked_error.message_en.lower()


def test_browser_helpers_keep_deadline_and_dom_reads_together() -> None:
    deadline = traveloka_adapter.monotonic() + 10

    assert browser_helpers.remaining_timeout_ms(deadline) > 0
    assert browser_helpers.dom_operation_timeout_ms(
        timeout_ms=250,
        deadline=deadline,
    ) <= 250
```

- [ ] **Step 2: Run ownership tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_error_factories_live_in_errors_module tests/test_traveloka_adapter.py::test_browser_helpers_keep_deadline_and_dom_reads_together -v
```

Expected: FAIL with missing `errors` and `browser_helpers` modules.

- [ ] **Step 3: Create `errors.py`**

Create `cheapy/providers/traveloka/errors.py` by moving the existing `TravelokaProviderError`, `_is_timeout_exception`, `_timeout_error`, `_browser_unavailable_error`, `_navigation_failed_error`, `_blocked_error`, `_rate_limited_error`, `_transport_error`, `_invalid_json_error`, `_unsupported_response_error`, and `_raise_blocked_if_terminal_page` bodies from `adapter.py`.

Rename moved factory functions to public module-local names without the leading underscore:

```python
timeout_error
browser_unavailable_error
navigation_failed_error
blocked_error
rate_limited_error
transport_error
invalid_json_error
unsupported_response_error
raise_blocked_if_terminal_page
is_timeout_exception
```

Use this import header:

```python
"""Provider-local errors for the Traveloka research provider."""

from __future__ import annotations

from cheapy.models import ErrorCode
```

Keep factory return values identical to current adapter behavior.

- [ ] **Step 4: Create `browser_helpers.py`**

Create `cheapy/providers/traveloka/browser_helpers.py` by moving the existing `_close_quietly`, `_remaining_timeout_ms`, `_dom_operation_timeout_ms`, `_locator_texts`, and `_read_body_text` bodies from `adapter.py`.

Rename moved functions to public module-local names:

```python
close_quietly
remaining_timeout_ms
dom_operation_timeout_ms
locator_texts
read_body_text
```

Use this import header:

```python
"""Shared browser helper functions for the Traveloka research provider."""

from __future__ import annotations

from time import monotonic

from cheapy.providers.traveloka.errors import timeout_error
```

Inside `remaining_timeout_ms`, call `timeout_error()` where the old adapter helper called `_timeout_error()`.

- [ ] **Step 5: Update adapter imports and call sites**

In `cheapy/providers/traveloka/adapter.py`, import:

```python
from cheapy.providers.traveloka.browser_helpers import (
    close_quietly,
    dom_operation_timeout_ms,
    locator_texts,
    read_body_text,
    remaining_timeout_ms,
)
from cheapy.providers.traveloka.errors import (
    TravelokaProviderError,
    blocked_error,
    browser_unavailable_error,
    invalid_json_error,
    is_timeout_exception,
    navigation_failed_error,
    raise_blocked_if_terminal_page,
    rate_limited_error,
    timeout_error,
    transport_error,
    unsupported_response_error,
)
```

Replace call sites:

```text
_remaining_timeout_ms -> remaining_timeout_ms
_dom_operation_timeout_ms -> dom_operation_timeout_ms
_locator_texts -> locator_texts
_read_body_text -> read_body_text
_close_quietly -> close_quietly
_timeout_error -> timeout_error
_browser_unavailable_error -> browser_unavailable_error
_navigation_failed_error -> navigation_failed_error
_blocked_error -> blocked_error
_rate_limited_error -> rate_limited_error
_transport_error -> transport_error
_invalid_json_error -> invalid_json_error
_unsupported_response_error -> unsupported_response_error
_raise_blocked_if_terminal_page -> raise_blocked_if_terminal_page
_is_timeout_exception -> is_timeout_exception
```

Delete the moved definitions from `adapter.py`.

- [ ] **Step 6: Run extracted error/helper tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_error_factories_live_in_errors_module tests/test_traveloka_adapter.py::test_browser_helpers_keep_deadline_and_dom_reads_together tests/test_traveloka_adapter.py::test_adapter_maps_browser_launch_timeout_to_timeout tests/test_traveloka_adapter.py::test_adapter_blocks_terminal_captcha_page_when_no_payload_arrives -v
```

Expected: PASS.

- [ ] **Step 7: Commit error/helper extraction**

Run:

```bash
git add cheapy/providers/traveloka/errors.py cheapy/providers/traveloka/browser_helpers.py cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "refactor: extract Traveloka errors and browser helpers" -m "Model: GPT-5 Codex"
```

## Task 3: URL And Capture Modules

**Files:**
- Create: `cheapy/providers/traveloka/urls.py`
- Create: `cheapy/providers/traveloka/capture.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing URL and capture module tests**

In `tests/test_traveloka_adapter.py`, add:

```python
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import urls as traveloka_urls
```

Add these tests near the existing URL and capture tests:

```python
def test_traveloka_urls_module_builds_full_search_url() -> None:
    url = traveloka_urls.build_full_search_url(
        _round_trip_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    params = parse_qs(urlparse(url).query)
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026.17-7-2026"]


def test_traveloka_capture_state_lives_in_capture_module() -> None:
    state = traveloka_capture.CaptureState()
    response = FakeResponse(
        url="https://www.traveloka.com/api/v2/flight/search/initial",
        payload={"data": {"meta": {"searchCompleted": True}, "searchResults": []}},
    )

    state.handle_response(response)

    assert state.completed is True
    assert state.best_result is not None
    assert state.best_result.source_path == "/api/v2/flight/search/initial"
```

- [ ] **Step 2: Run URL/capture tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_urls_module_builds_full_search_url tests/test_traveloka_adapter.py::test_traveloka_capture_state_lives_in_capture_module -v
```

Expected: FAIL with missing `urls` and `capture` modules.

- [ ] **Step 3: Create `urls.py`**

Create `cheapy/providers/traveloka/urls.py` by moving `DEFAULT_BASE_URL`, `build_full_search_url`, `_traveloka_date`, and `_passenger_spec` from `adapter.py`.

Use this header and rename private helpers:

```python
"""Traveloka URL helpers."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)


DEFAULT_BASE_URL = "https://www.traveloka.com/en-en/flight/fulltwosearch"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
```

Keep `build_full_search_url` behavior identical. Rename helper calls inside it to `traveloka_date` and `passenger_spec`.

Delete the legacy `build_search_url` wrapper from `adapter.py`. Update tests that mention `build_search_url` by removing compatibility expectations rather than preserving the wrapper.

- [ ] **Step 4: Create `capture.py`**

Create `cheapy/providers/traveloka/capture.py` by moving:

```text
INITIAL_SEARCH_PATH
POLL_SEARCH_PATH
SUPPORTED_FARE_PATHS
CaptureState
_is_supported_fare_payload
_is_traveloka_first_party_url
_search_result_count
_search_completed
_explicit_payload_item_ids
_payload_list_at_path
_explicit_item_id
_wait_for_capture
_wait_for_conservative_capture_result
_capture_result_after_wait
```

Rename public helpers used by other modules:

```text
_wait_for_capture -> wait_for_capture
_explicit_payload_item_ids -> explicit_payload_item_ids
_is_traveloka_first_party_url -> is_traveloka_first_party_url
```

Use imports:

```python
from collections.abc import Mapping
from time import monotonic
from urllib.parse import urlparse

from cheapy.providers.traveloka.browser_helpers import remaining_timeout_ms
from cheapy.providers.traveloka.errors import (
    blocked_error,
    invalid_json_error,
    rate_limited_error,
    timeout_error,
    transport_error,
    unsupported_response_error,
)
from cheapy.providers.traveloka.results import TravelokaCaptureResult
```

- [ ] **Step 5: Update adapter imports and call sites**

In `adapter.py`, import:

```python
from cheapy.providers.traveloka.capture import CaptureState, wait_for_capture
from cheapy.providers.traveloka.urls import DEFAULT_BASE_URL, build_full_search_url
```

Replace:

```text
_CaptureState -> CaptureState
_wait_for_capture -> wait_for_capture
```

Delete moved URL and capture definitions from `adapter.py`.

- [ ] **Step 6: Run URL and capture tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_urls_module_builds_full_search_url tests/test_traveloka_adapter.py::test_traveloka_capture_state_lives_in_capture_module tests/test_traveloka_adapter.py::test_adapter_captures_completed_initial_fare_payload tests/test_traveloka_adapter.py::test_adapter_ignores_supported_path_from_non_traveloka_host tests/test_traveloka_adapter.py::test_adapter_rejects_invalid_json_from_fare_endpoint -v
```

Expected: PASS.

- [ ] **Step 7: Commit URL and capture extraction**

Run:

```bash
git add cheapy/providers/traveloka/urls.py cheapy/providers/traveloka/capture.py cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "refactor: extract Traveloka URL and capture modules" -m "Model: GPT-5 Codex"
```

## Task 4: Migrate Tests Off Legacy Button Fallback

**Files:**
- Modify: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Identify tests that still depend on legacy button discovery**

Run:

```bash
rg -n "button:has-text|LEGACY_CHOOSE_BUTTON_SELECTOR|_visible_options_from_legacy_buttons|option_groups" tests/test_traveloka_adapter.py
```

Expected: output includes `LocatorFakePage.locator` and selected round-trip tests that use `option_groups`.

- [ ] **Step 2: Add current inventory-card fixture helper**

In `tests/test_traveloka_adapter.py`, add this helper near `_visible_option`:

```python
def _inventory_card_option(
    *,
    key: str,
    amount: str = "100.00",
    currency: str = "USD",
    airline: str = "Traveloka Air",
    on_click: object | None = None,
) -> LiveTravelokaCardLocator:
    button = TextFakeLocator(
        text="Choose",
        attrs={"data-testid": "flight-inventory-card-button", "role": "button"},
        on_click=on_click,
    )
    return LiveTravelokaCardLocator(
        container_id=key,
        text=f"{airline}\nSGN - BKK\n{currency} {amount}",
        button=button,
    )
```

- [ ] **Step 3: Rewrite selected round-trip tests to use inventory-card fixtures**

For selected round-trip tests that currently pass `option_groups=[[...], [...]]`, replace legacy `_visible_option(...)` locators with `_inventory_card_option(...)` card locators.

For example, in `test_round_trip_rejects_preexisting_return_marker_without_transition`, use:

```python
option_groups = [
    [_inventory_card_option(key="out-1", on_click=outbound_click)],
    [_inventory_card_option(key="ret-1", on_click=return_click)],
]
```

Keep each test's existing assertions for result type, selected keys, partial failure type, wait calls, and final total behavior.

- [ ] **Step 4: Run selected round-trip behavior tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_round_trip_rejects_preexisting_return_marker_without_transition tests/test_traveloka_adapter.py::test_round_trip_default_helpers_bind_locator_attributes_and_select_final_total tests/test_traveloka_adapter.py::test_round_trip_selects_cheapest_visible_outbound_and_return tests/test_traveloka_adapter.py::test_round_trip_returns_partial_when_outbound_selection_is_unavailable tests/test_traveloka_adapter.py::test_round_trip_returns_partial_when_return_selection_is_unavailable -v
```

Expected: PASS with tests using current inventory-card fixtures.

- [ ] **Step 5: Commit fixture migration**

Run:

```bash
git add tests/test_traveloka_adapter.py
git commit -m "test: migrate Traveloka selection fixtures to inventory cards" -m "Model: GPT-5 Codex"
```

## Task 5: Inventory And Activation Modules

**Files:**
- Create: `cheapy/providers/traveloka/inventory.py`
- Create: `cheapy/providers/traveloka/activation.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing inventory and activation ownership tests**

In `tests/test_traveloka_adapter.py`, add:

```python
from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import inventory as traveloka_inventory
```

Add:

```python
def test_traveloka_inventory_module_owns_visible_option_contract() -> None:
    option = traveloka_inventory.TravelokaVisibleOption(
        key="out-1",
        airline_name="Traveloka Air",
        departure_time_text=None,
        arrival_time_text=None,
        route_text=None,
        price_amount=Decimal("10.00"),
        currency="USD",
        locator=FakeLocator(),
    )

    assert traveloka_inventory.cheapest_visible_option([option]) == option


def test_traveloka_activation_module_clicks_visible_option() -> None:
    locator = ScrollableFakeLocator()
    option = traveloka_inventory.TravelokaVisibleOption(
        key="out-1",
        airline_name=None,
        departure_time_text=None,
        arrival_time_text=None,
        route_text=None,
        price_amount=Decimal("10.00"),
        currency="USD",
        locator=locator,
    )

    traveloka_activation.click_visible_option(option, timeout_ms=1000)

    assert locator.evaluate_scripts == [traveloka_activation.TRAVELOKA_OPTION_ACTIVATION_SCRIPT]
    assert locator.scroll_kwargs[0]["timeout"] == 1000
```

- [ ] **Step 2: Run ownership tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_inventory_module_owns_visible_option_contract tests/test_traveloka_adapter.py::test_traveloka_activation_module_clicks_visible_option -v
```

Expected: FAIL with missing `inventory` and `activation` modules.

- [ ] **Step 3: Create `inventory.py`**

Create `cheapy/providers/traveloka/inventory.py` by moving:

```text
INVENTORY_CARD_TEST_ID_PREFIX
INVENTORY_CARD_SELECTOR
INVENTORY_CARD_BUTTON_SELECTOR
TravelokaVisibleOption
_cheapest_visible_option
_visible_option_key_rank
_parse_visible_price
_bind_visible_option_to_payload
_visible_options_from_page
_visible_options_from_inventory_cards
_selection_action_from_card
_first_locator
_visible_option_from_text
_stable_key_from_locator
_stable_key_from_attribute
_locator_attribute
_stable_key_from_text
_price_amount_near_marker
```

Rename public functions:

```text
_cheapest_visible_option -> cheapest_visible_option
_parse_visible_price -> parse_visible_price
_bind_visible_option_to_payload -> bind_visible_option_to_payload
_visible_options_from_page -> visible_options_from_page
```

Use imports:

```python
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
import re

from cheapy.providers.traveloka.browser_helpers import dom_operation_timeout_ms
from cheapy.providers.traveloka.capture import explicit_payload_item_ids
```

Do not move `LEGACY_CHOOSE_BUTTON_SELECTOR` or `_visible_options_from_legacy_buttons`. Delete them from `adapter.py`.

- [ ] **Step 4: Create `activation.py`**

Create `cheapy/providers/traveloka/activation.py` by moving `VISIBLE_OPTION_CLICK_TIMEOUT_MS`, `TRAVELOKA_OPTION_ACTIVATION_SCRIPT`, and `_click_visible_option`.

Rename `_click_visible_option` to `click_visible_option`.

Use imports:

```python
from cheapy.providers.traveloka.inventory import TravelokaVisibleOption
```

- [ ] **Step 5: Update adapter imports and call sites**

In `adapter.py`, import:

```python
from cheapy.providers.traveloka.activation import click_visible_option
from cheapy.providers.traveloka.inventory import (
    bind_visible_option_to_payload,
    cheapest_visible_option,
    visible_options_from_page,
)
```

Replace call sites:

```text
_click_visible_option -> click_visible_option
_cheapest_visible_option -> cheapest_visible_option
_bind_visible_option_to_payload -> bind_visible_option_to_payload
_visible_options_from_page -> visible_options_from_page
```

Delete moved inventory and activation definitions from `adapter.py`.

- [ ] **Step 6: Run inventory and activation tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_inventory_module_owns_visible_option_contract tests/test_traveloka_adapter.py::test_traveloka_activation_module_clicks_visible_option tests/test_traveloka_adapter.py::test_visible_options_from_page_discovers_live_inventory_cards tests/test_traveloka_adapter.py::test_click_visible_option_dispatches_traveloka_activation_sequence tests/test_traveloka_adapter.py::test_round_trip_selects_cheapest_visible_outbound_and_return -v
```

Expected: PASS.

- [ ] **Step 7: Prove legacy fallback is gone**

Run:

```bash
rg -n "LEGACY_CHOOSE_BUTTON_SELECTOR|_visible_options_from_legacy_buttons|button:has-text" cheapy/providers/traveloka tests/test_traveloka_adapter.py
```

Expected: no provider matches. Any remaining test match must be in a deleted test hunk or a comment-free fixture check that should be removed before committing.

- [ ] **Step 8: Commit inventory and activation extraction**

Run:

```bash
git add cheapy/providers/traveloka/inventory.py cheapy/providers/traveloka/activation.py cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "refactor: extract Traveloka inventory and activation" -m "Model: GPT-5 Codex"
```

## Task 6: Selection And Totals Modules

**Files:**
- Create: `cheapy/providers/traveloka/selection.py`
- Create: `cheapy/providers/traveloka/totals.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing selection and totals ownership tests**

In `tests/test_traveloka_adapter.py`, add:

```python
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
```

Add:

```python
def test_traveloka_totals_module_reads_final_total() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='checkout'][data-testid*='total']": FakeLocatorCollection(
                [TextFakeLocator(text="Checkout total USD 321.09")]
            )
        },
    )

    assert traveloka_totals.read_final_total(page) == (Decimal("321.09"), "USD")


def test_traveloka_selection_module_detects_return_transition() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": FakeLocatorCollection(
                [TextFakeLocator(text="Return\nChange return flight")]
            )
        },
    )

    assert traveloka_selection.return_selection_transitioned(page) is True
```

- [ ] **Step 2: Run ownership tests and verify failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_totals_module_reads_final_total tests/test_traveloka_adapter.py::test_traveloka_selection_module_detects_return_transition -v
```

Expected: FAIL with missing `selection` and `totals` modules.

- [ ] **Step 3: Create `totals.py`**

Create `cheapy/providers/traveloka/totals.py` by moving final-total constants, regexes, `_FinalTotalSelectorCache`, `_parse_explicit_price`, `_parse_explicit_prices`, `_parse_selected_total_price`, `_parse_summary_total_price`, `_ordered_final_total_selector_items`, `_ordered_final_total_selectors`, `_read_final_total`, `_final_total_texts`, `_wait_for_final_total`, and `_normalized_text_key`.

Rename public functions:

```text
_read_final_total -> read_final_total
_final_total_texts -> final_total_texts
_wait_for_final_total -> wait_for_final_total
_normalized_text_key -> normalized_text_key
```

Use imports:

```python
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
import re

from cheapy.providers.traveloka.browser_helpers import locator_texts, remaining_timeout_ms
from cheapy.providers.traveloka.inventory import parse_visible_price
```

- [ ] **Step 4: Create `selection.py`**

Create `cheapy/providers/traveloka/selection.py` by moving `_wait_for_return_selection_transition`, `_return_selection_marker_texts`, `_return_selection_transitioned`, `_return_selection_marker_text`, `_return_selection_marker_key`, `_wait_for_outbound_selection_transition`, `_capture_looks_like_new_inventory`, `_outbound_selection_transitioned`, and `_selected_url_fragment`.

Rename public functions:

```text
_wait_for_return_selection_transition -> wait_for_return_selection_transition
_return_selection_transitioned -> return_selection_transitioned
_wait_for_outbound_selection_transition -> wait_for_outbound_selection_transition
```

Use imports:

```python
from collections.abc import Iterable
from time import monotonic
from urllib.parse import urlparse

from cheapy.providers.traveloka.browser_helpers import (
    locator_texts,
    read_body_text,
    remaining_timeout_ms,
)
from cheapy.providers.traveloka.capture import explicit_payload_item_ids
from cheapy.providers.traveloka.results import TravelokaCaptureResult
from cheapy.providers.traveloka.totals import normalized_text_key
```

- [ ] **Step 5: Update adapter imports and call sites**

In `adapter.py`, import:

```python
from cheapy.providers.traveloka.selection import (
    wait_for_outbound_selection_transition,
    wait_for_return_selection_transition,
)
from cheapy.providers.traveloka.totals import final_total_texts, wait_for_final_total
```

Replace:

```text
_wait_for_outbound_selection_transition -> wait_for_outbound_selection_transition
_wait_for_return_selection_transition -> wait_for_return_selection_transition
_final_total_texts -> final_total_texts
_wait_for_final_total -> wait_for_final_total
```

Delete moved selection and totals definitions from `adapter.py`.

- [ ] **Step 6: Run selection and totals tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_traveloka_totals_module_reads_final_total tests/test_traveloka_adapter.py::test_traveloka_selection_module_detects_return_transition tests/test_traveloka_adapter.py::test_read_final_total_prefers_checkout_total_over_summary_and_label tests/test_traveloka_adapter.py::test_read_final_total_ignores_conflicting_global_label_totals tests/test_traveloka_adapter.py::test_wait_for_return_selection_transition_recognizes_selected_summary tests/test_traveloka_adapter.py::test_round_trip_rejects_stale_summary_total_after_return_transition -v
```

Expected: PASS.

- [ ] **Step 7: Commit selection and totals extraction**

Run:

```bash
git add cheapy/providers/traveloka/selection.py cheapy/providers/traveloka/totals.py cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "refactor: extract Traveloka selection and totals" -m "Model: GPT-5 Codex"
```

## Task 7: Adapter Facade Cleanup

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Inspect remaining adapter definitions**

Run:

```bash
rg -n "^(class|def|    def) " cheapy/providers/traveloka/adapter.py
wc -l cheapy/providers/traveloka/adapter.py
```

Expected: `adapter.py` primarily contains `TravelokaAdapter`, `_default_launch_browser`, and thin workflow methods. Line count should be much lower than the pre-refactor 1,783 lines.

- [ ] **Step 2: Remove stale imports and helper remnants**

In `adapter.py`, keep imports limited to:

```python
from __future__ import annotations

from time import monotonic
from typing import Callable

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka.activation import click_visible_option
from cheapy.providers.traveloka.browser_helpers import (
    close_quietly,
    read_body_text,
    remaining_timeout_ms,
)
from cheapy.providers.traveloka.capture import CaptureState, wait_for_capture
from cheapy.providers.traveloka.errors import (
    TravelokaProviderError,
    browser_unavailable_error,
    is_timeout_exception,
    navigation_failed_error,
    raise_blocked_if_terminal_page,
    timeout_error,
)
from cheapy.providers.traveloka.inventory import (
    bind_visible_option_to_payload,
    cheapest_visible_option,
    visible_options_from_page,
)
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
    partial_round_trip_result,
)
from cheapy.providers.traveloka.selection import (
    wait_for_outbound_selection_transition,
    wait_for_return_selection_transition,
)
from cheapy.providers.traveloka.timing import (
    TravelokaPhaseRecorder,
    TravelokaPhaseTiming,
)
from cheapy.providers.traveloka.totals import final_total_texts, wait_for_final_total
from cheapy.providers.traveloka.urls import DEFAULT_BASE_URL, build_full_search_url
```

Remove any unused imports reported by pytest or static inspection.

- [ ] **Step 3: Keep adapter workflow readable**

In `_search_selected_round_trip`, keep the phase block order readable and using extracted helpers:

```python
with self._phase_recorder.phase("outbound_visible_option_discovery"):
    outbound_option = cheapest_visible_option(
        visible_options_from_page(page, deadline=deadline)
    )
    if outbound_option is None:
        return partial_round_trip_result(
            outbound_capture,
            "outbound_selection_unavailable",
        )
```

Use the same pattern for outbound binding, outbound transition, return capture, return option discovery, return binding, return transition, and final total read.

- [ ] **Step 4: Run adapter workflow tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_adapter_captures_completed_initial_fare_payload tests/test_traveloka_adapter.py::test_round_trip_selects_cheapest_visible_outbound_and_return tests/test_traveloka_adapter.py::test_round_trip_returns_partial_when_final_total_is_unavailable tests/test_traveloka_adapter.py::test_adapter_maps_navigation_timeout_after_launch -v
```

Expected: PASS.

- [ ] **Step 5: Commit adapter facade cleanup**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "refactor: slim Traveloka adapter facade" -m "Model: GPT-5 Codex"
```

## Task 8: Provider, Normalizer, Docs, And Package Verification

**Files:**
- Modify: `cheapy/providers/traveloka/provider.py`
- Modify: `cheapy/providers/traveloka/normalizer.py`
- Modify: `README.md`
- Modify: `README.vi.md`
- Modify: `tests/test_traveloka_provider.py`
- Modify: `tests/test_traveloka_normalizer.py`

- [ ] **Step 1: Update provider and normalizer imports**

In `provider.py`, use:

```python
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.errors import TravelokaProviderError
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
```

In `normalizer.py`, use:

```python
from cheapy.providers.traveloka.results import TravelokaSelectedRoundTripResult
```

Update `tests/test_traveloka_provider.py` and `tests/test_traveloka_normalizer.py` to import result/error contracts from their owning modules.

- [ ] **Step 2: Update English README Traveloka paragraph**

Replace the Traveloka paragraph in `README.md` with:

```markdown
Traveloka is a fragile, default-enabled research provider in this codebase under
the project owner's stated Traveloka support approval. It may time out or be
blocked, and it is intentionally conservative: no login, no persistent browser
profile, no retries, no provider-internal fanout, and a 45 second per-call
timeout. The current implementation uses a fresh browser context to let
Traveloka's own web app produce first-party fare payloads, then reads selected
round-trip totals only after both legs are selected. Do not deploy this default
live provider set for user-facing search without Traveloka permission.
```

- [ ] **Step 3: Update Vietnamese README Traveloka paragraph**

Replace the matching Traveloka paragraph in `README.vi.md` with:

```markdown
Traveloka là research provider mong manh và được bật mặc định trong codebase
này theo xác nhận của project owner rằng Traveloka support đã đồng ý cho dùng.
Provider này có thể timeout hoặc bị block, và chạy thận trọng: không login,
không dùng browser profile lưu trạng thái, không retry, không fanout nội bộ
provider, và timeout 45 giây cho mỗi call. Implementation hiện tại dùng browser
context mới để Traveloka web app tự tạo first-party fare payload, rồi chỉ đọc
selected round-trip total sau khi đã chọn cả hai chặng. Không deploy bộ default
live provider này cho user-facing search nếu chưa có permission từ Traveloka.
```

- [ ] **Step 4: Run provider and normalizer tests**

Run:

```bash
uv run pytest tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit provider/docs cleanup**

Run:

```bash
git add cheapy/providers/traveloka/provider.py cheapy/providers/traveloka/normalizer.py README.md README.vi.md tests/test_traveloka_provider.py tests/test_traveloka_normalizer.py
git commit -m "docs: align Traveloka provider documentation" -m "Model: GPT-5 Codex"
```

## Task 9: Final Verification

**Files:**
- Modify only if verification exposes a regression in files touched by earlier tasks.

- [ ] **Step 1: Run Traveloka test suite**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 2: Run search aggregation tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS with live tests skipped unless `CHEAPY_RUN_LIVE_TESTS=1`.

- [ ] **Step 4: Prove legacy symbols are gone**

Run:

```bash
rg -n "build_search_url|LEGACY_CHOOSE_BUTTON_SELECTOR|_visible_options_from_legacy_buttons|_partial_round_trip_result|_CaptureState|_wait_for_capture" cheapy/providers/traveloka tests/test_traveloka_adapter.py
```

Expected: no matches for deleted legacy symbols. Matches for renamed public functions in new modules should use names without leading underscores.

- [ ] **Step 5: Inspect Traveloka module sizes**

Run:

```bash
wc -l cheapy/providers/traveloka/*.py
```

Expected: `adapter.py` is no longer the largest god file. `normalizer.py` may remain large in this refactor; other new modules should be focused and substantially smaller than the original adapter.

- [ ] **Step 6: Commit final verification fixes if any were needed**

If verification required fixes, run:

```bash
git add cheapy/providers/traveloka tests README.md README.vi.md
git commit -m "fix: complete Traveloka refactor verification" -m "Model: GPT-5 Codex"
```

If no fixes were needed, do not create an empty commit.

