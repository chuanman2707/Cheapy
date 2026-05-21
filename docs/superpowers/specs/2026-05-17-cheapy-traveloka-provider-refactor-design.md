# Cheapy Traveloka Provider Refactor Design

Date: 2026-05-17

## Summary

Refactor the Traveloka provider internals to reduce tech debt, remove legacy
fallbacks, and make the selected round-trip browser flow maintainable.

This is an aggressive boundary refactor, not a public contract change. Cheapy
must keep the current Traveloka selected round-trip capability: select the
cheapest visible outbound, select the cheapest visible return, read the final
selected round-trip total, and return a comparable selected offer only when all
selected evidence is available.

## Current Problem

Traveloka provider behavior is currently concentrated in a few large files:

- `cheapy/providers/traveloka/adapter.py` is about 1,800 lines and mixes browser
  lifecycle, URL building, response capture, DOM inventory parsing, click
  activation, transition detection, final total parsing, timeout handling, and
  provider-local error factories.
- `tests/test_traveloka_adapter.py` is about 3,400 lines and protects both the
  selected round-trip behavior and older helper/fallback behavior.
- README text still describes Traveloka as "no browser" with a 20 second
  timeout, while the current implementation uses browser automation with a 45
  second provider timeout.

The refactor should make each unit understandable without requiring a maintainer
to read the whole adapter file.

## Approved Approach

Use a boundary refactor with a readable adapter state machine.

Rejected alternatives:

- Split-only cleanup: safer, but leaves the core spaghetti orchestration intact.
- Full rewrite: could be cleaner, but is too risky because Traveloka selected
  round-trip behavior depends on fragile live-learned edge cases.

The approved approach keeps public behavior stable where it matters, while
removing legacy fallback code and tests that no longer serve the selected
round-trip runtime path.

## Goals

- Keep exact one-way and selected exact round-trip Traveloka capabilities.
- Preserve Contract V1 request/response shapes.
- Make `adapter.py` a thin workflow facade rather than a god file.
- Move cohesive responsibilities into focused modules with explicit internal
  contracts.
- Delete legacy wrappers, unused helpers, and obsolete fallback DOM paths.
- Preserve safe provider error handling and partial fallback semantics.
- Keep default tests offline and deterministic.
- Update docs that currently describe Traveloka incorrectly.

## Non-Goals

- No Contract V1 schema changes.
- No provider selection field or new MCP tool.
- No direct HTTP replay.
- No login, captcha solving, proxy rotation, retries, or persistent browser
  state.
- No exhaustive optimization across multiple outbound and return combinations.
- No live Traveloka calls in default tests.
- No broad search/ranking refactor outside the Traveloka provider boundary.

## Architecture

Refactor `cheapy/providers/traveloka/` into focused modules:

```text
cheapy/providers/traveloka/
  provider.py        # async provider boundary and ProviderResult/error mapping
  adapter.py         # thin browser workflow facade
  results.py         # result dataclasses
  errors.py          # provider-local error factories and safe failure metadata
  browser_helpers.py # shared deadline, cleanup, and bounded DOM-read helpers
  urls.py            # Traveloka URL/date/passenger query building
  capture.py         # first-party response filtering and capture waits
  inventory.py       # visible card discovery, cheapest option, key binding
  activation.py      # Traveloka-specific DOM activation workaround
  selection.py       # outbound/return transition waits
  totals.py          # final selected round-trip total parsing
  normalizer.py      # Traveloka payload/result to Contract V1 offers
  timing.py          # safe phase timing
```

`adapter.py` should read as the high-level workflow:

1. launch browser resources
2. navigate
3. capture outbound payload
4. choose and bind outbound
5. activate outbound and wait for transition
6. capture return payload
7. choose and bind return
8. activate return and wait for transition
9. read final selected total
10. build a Traveloka result dataclass
11. clean up browser resources

## Component Contracts

`results.py` owns provider-local data contracts:

- `TravelokaCaptureResult`
- `TravelokaSelectedRoundTripResult`
- `partial_round_trip_result(capture, failure_type)`

`errors.py` owns provider-local error contracts and factories:

- `TravelokaProviderError`
- timeout, browser-unavailable, navigation-failed, blocked, rate-limited,
  transport, invalid JSON, and unsupported response error factories
- terminal-page block detection
- safe enum-like failure metadata only; no raw URLs, headers, payloads, or page
  content in public error details

Adapter, capture, and any other Traveloka module that needs to raise a
provider-local error imports from `errors.py`. Traveloka modules must not import
each other just to reuse an error factory.

`browser_helpers.py` owns shared low-level browser helpers:

- `remaining_timeout_ms(deadline, raise_on_expired=True)`
- `dom_operation_timeout_ms(timeout_ms, deadline)`
- `locator_texts(page, selector, timeout_ms, deadline)`
- `read_body_text(page, timeout_ms, deadline)`
- `close_quietly(target)`

These helpers are intentionally low-level and must not import provider,
normalizer, inventory, selection, or totals modules.

`capture.py` owns response capture:

- `CaptureState.handle_response(response)`
- `wait_for_capture(state, page, deadline, poll_interval_seconds)`
- first-party Traveloka host checks
- supported fare path checks
- fare payload validation
- calls into `errors.py` for timeout, blocked, rate-limited, transport, invalid
  JSON, and unsupported response errors

`inventory.py` owns rendered fare inventory:

- `TravelokaVisibleOption`
- `visible_options_from_page(page, deadline)`
- `cheapest_visible_option(options)`
- `bind_visible_option_to_payload(option, payload)`
- price parsing and stable key extraction for the current inventory-card surface

Remove the legacy button fallback path outside the current inventory-card
surface, including `_visible_options_from_legacy_buttons` and tests that only
protect that fallback.

Before removing that fallback, selected round-trip tests that currently reach
options through fake `button:has-text(...)` locators must be migrated to the
current inventory-card fixture shape. Required selected round-trip behavior
tests should keep their assertions and use card/container fixtures equivalent to
`LiveTravelokaCardLocator`, so deleting the legacy button fallback does not
accidentally delete coverage for the required flow.

`activation.py` owns Traveloka card activation:

- scroll selected card into view when possible
- dispatch the Traveloka-specific pointer/mouse sequence
- keep bounded timeout behavior
- fall back only to the current supported locator activation path

`selection.py` owns UI state transitions:

- `wait_for_outbound_selection_transition(...)`
- `wait_for_return_selection_transition(...)`
- body/URL/marker checks needed to prove state transition
- no final total parsing
- no offer normalization

`totals.py` owns final selected total detection:

- `final_total_texts(page, deadline)`
- `wait_for_final_total(page, deadline, poll_interval_seconds, before_texts)`
- `read_final_total(...)`
- selector tier precedence:
  1. selected/final/checkout total selectors
  2. selected summary selectors
  3. unambiguous global label total
- stale text rejection
- ambiguous global-label rejection

`urls.py` owns Traveloka URL helpers:

- `build_full_search_url(request, base_url=DEFAULT_BASE_URL)`
- Traveloka date formatting
- passenger spec formatting

The legacy `build_search_url` compatibility wrapper should be removed.

## Behavior Rules

Selected round-trip behavior is required:

- Round-trip flow chooses the cheapest visible outbound option.
- It waits for return inventory after outbound selection.
- It chooses the cheapest visible return option.
- It reads the final selected round-trip total after return selection.
- A comparable round-trip offer is produced only from
  `TravelokaSelectedRoundTripResult` with selected outbound key, selected return
  key, and final selected total.
- Raw round-trip captures remain partial and non-comparable.

Partial fallback remains required:

- If outbound payload exists but a later selected round-trip stage fails,
  preserve normalizable outbound partial offers.
- Preserve existing safe failure types:
  - `outbound_selection_unavailable`
  - `selected_outbound_binding_unavailable`
  - `outbound_selection_transition_unavailable`
  - `return_capture_timeout`
  - `return_selection_unavailable`
  - `selected_return_binding_unavailable`
  - `final_round_trip_total_unavailable`
  - `timeout`
  - `blocked`
  - `rate_limited`

Provider error safety remains required:

- Do not leak full URLs, query strings, cookies, headers, payloads, tokens, or
  raw page content.
- `source_path` values stay as safe API paths, such as
  `/api/v2/flight/search/poll`.

Legacy cleanup is explicitly in scope:

- Remove compatibility wrappers with no runtime caller.
- Remove parser helpers with no caller after extraction.
- Remove legacy DOM fallback paths that are not part of the current selected
  round-trip surface.
- Delete or rewrite tests that only protect removed legacy behavior.

## Data Flow

Exact one-way flow:

```text
TravelokaProvider
  -> TravelokaAdapter.search_exact_one_way
  -> urls.build_full_search_url
  -> browser/context/page setup
  -> capture.CaptureState handles first-party fare responses
  -> capture.wait_for_capture
  -> TravelokaCaptureResult
  -> normalizer.normalize_payload
  -> ProviderResult
```

Selected exact round-trip flow:

```text
TravelokaProvider
  -> TravelokaAdapter.search_exact_round_trip
  -> browser/context/page setup
  -> outbound CaptureState + wait_for_capture
  -> inventory.cheapest_visible_option
  -> inventory.bind_visible_option_to_payload
  -> activation.click_visible_option
  -> selection.wait_for_outbound_selection_transition
  -> return CaptureState + wait_for_capture
  -> inventory.cheapest_visible_option
  -> inventory.bind_visible_option_to_payload
  -> totals.final_total_texts before return activation
  -> activation.click_visible_option
  -> selection.wait_for_return_selection_transition
  -> totals.wait_for_final_total
  -> TravelokaSelectedRoundTripResult
  -> normalizer.normalize_selected_round_trip
  -> ProviderResult
```

Ownership rules:

- Only `provider.py` creates `ProviderResult`.
- Only `normalizer.py` creates `FlightOfferV1`.
- Only `adapter.py` owns browser lifecycle and workflow ordering.
- Only `errors.py` creates `TravelokaProviderError` through named factories.
- Capture, inventory, activation, selection, and totals modules do not import
  Contract V1 models.

## Error Handling

Provider-level errors remain structured and provider-local.

Expected whole-call failures include:

- `timeout`
- `browser_unavailable`
- `navigation_failed`
- `blocked`
- `rate_limited`
- `transport_error`
- `invalid_json`
- `unsupported_response`
- `unexpected_error`
- `no_usable_outbound_data`

Expected partial selected round-trip failures include:

- `outbound_selection_unavailable`
- `selected_outbound_binding_unavailable`
- `outbound_selection_transition_unavailable`
- `return_capture_timeout`
- `return_selection_unavailable`
- `selected_return_binding_unavailable`
- `final_round_trip_total_unavailable`

Unknown partial failure metadata still maps to `partial_failure`.

The refactor must preserve retryability and public `ErrorCode` mappings for all
failure types that remain reachable after the approved legacy cleanup.

## Testing

Use existing Traveloka tests as a safety net, but do not preserve tests that
only protect deleted legacy behavior.

Baseline command before implementation:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Targeted test ownership after extraction:

- `urls.py`: URL/date/passenger tests.
- `capture.py`: first-party host filtering, supported path filtering, HTTP
  status mapping, invalid JSON, unsupported payload, timeout, partial capture.
- `inventory.py`: current inventory-card discovery, price parsing, cheapest
  tie-break, payload ID binding.
- `activation.py`: synthetic pointer/mouse activation sequence and timeout cap.
- `selection.py`: outbound and return transition marker behavior.
- `totals.py`: final-total precedence, stale text guard, ambiguous global label
  rejection.
- `provider.py` and `normalizer.py`: selected result dispatch, partial fallback,
  safe failure mapping, comparable/non-comparable round-trip semantics.

Tests to delete or rewrite:

- `build_search_url` compatibility tests.
- legacy button fallback tests outside current inventory-card flow.
- selected round-trip tests that currently rely on fake legacy button locators
  should be rewritten to use current inventory-card fixtures before the fallback
  is deleted.
- direct tests for parser helpers removed during extraction.

Final verification:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
uv run pytest tests/test_search.py -v
uv run pytest -v
```

Live Traveloka tests remain opt-in behind `CHEAPY_RUN_LIVE_TESTS=1`.

## Migration Plan

1. Extract result contracts first.
   - Create `results.py`.
   - Move Traveloka result dataclasses.
   - Update adapter, provider, normalizer, and tests.

2. Extract provider-local errors and shared browser helpers.
   - Create `errors.py`.
   - Move `TravelokaProviderError` and safe error factory functions.
   - Create `browser_helpers.py`.
   - Move deadline, bounded DOM text, body text, and quiet cleanup helpers.
   - Update adapter tests to import helper functions from their owning modules
     when they remain directly tested.

3. Extract URL and capture code.
   - Create `urls.py` and `capture.py`.
   - Remove `build_search_url`.
   - Keep adapter calling only `build_full_search_url`, `CaptureState`, and
     `wait_for_capture`.

4. Migrate selected round-trip tests off legacy button fixtures.
   - Rewrite selected round-trip tests that currently depend on
     `button:has-text(...)` fallback discovery to use current inventory-card
     fixtures.
   - Preserve assertions for required selected round-trip behavior.
   - Delete tests that only protect legacy fallback discovery.

5. Extract inventory and activation code.
   - Create `inventory.py` and `activation.py`.
   - Keep current inventory-card path.
   - Remove obsolete legacy button fallback behavior and tests.

6. Extract selection and totals code.
   - Create `selection.py` and `totals.py`.
   - Keep selector precedence and stale-text behavior.
   - Make round-trip adapter flow read as orchestration.

7. Tighten provider and normalizer imports.
   - Normalizer imports selected result contracts from `results.py`.
   - Provider imports adapter facade and result/error contracts from their owning
     modules.

8. Update docs and verify.
   - Fix `README.md` and `README.vi.md` language that says Traveloka is
     no-browser or 20 seconds.
   - `README.zh-CN.md` has no matching Traveloka paragraph today, so no Chinese
     translation update is required for this refactor.
   - Run targeted and full test commands.

## Acceptance Criteria

- `cheapy/providers/traveloka/adapter.py` is no longer a god file and reads as a
  workflow facade.
- New Traveloka modules have one clear responsibility each.
- Selected exact round-trip behavior still works through the current offline
  tests.
- Raw round-trip captures remain non-comparable partial offers.
- Safe partial failure types and provider error safety are preserved.
- Legacy wrappers, unused helpers, and obsolete fallback DOM paths identified
  during implementation are deleted rather than moved.
- `README.md` and `README.vi.md` Traveloka documentation match the
  browser-based implementation.
- Default tests do not make live Traveloka calls.
