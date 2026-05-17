# Cheapy Traveloka Full Internal Refactor Design

Date: 2026-05-17

## Summary

Refactor the Traveloka provider internals more deeply than the previous adapter
extraction. The current adapter is no longer a god file, but the selected
round-trip workflow, normalizer internals, and tests still carry enough coupling
that future Traveloka maintenance is harder than it should be.

This design keeps the public Cheapy Contract V1 boundary stable while allowing
controlled internal behavior changes. The refactor may change internal workflow
state, remove obsolete fallback paths, and reorganize tests, as long as default
offline tests pass and opt-in live smoke plus benchmark checks do not show a
clear regression.

## Current State

Traveloka has already been split into focused modules:

- `provider.py`
- `adapter.py`
- `results.py`
- `errors.py`
- `browser_helpers.py`
- `urls.py`
- `capture.py`
- `inventory.py`
- `activation.py`
- `selection.py`
- `totals.py`
- `timing.py`
- `normalizer.py`

Targeted Traveloka tests currently pass:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -q
```

The current local baseline is `178 passed`.

The remaining debt is concentrated in:

- `cheapy/providers/traveloka/normalizer.py`, about 994 lines, which mixes
  payload discovery, Traveloka search-result canonicalization, segment parsing,
  route validation, selected result normalization, ranking, and error creation.
- `tests/test_traveloka_adapter.py`, about 3,585 lines, which tests several
  module-level responsibilities through one file and keeps adapter workflow
  tests coupled to lower-level helpers.
- `adapter.py`, now about 395 lines, which is readable but still owns browser
  lifecycle setup and selected round-trip orchestration in the same class.

## Approved Approach

Use a full internal architecture refactor.

The refactor combines:

1. A workflow/session split so `adapter.py` becomes a thin facade.
2. A normalizer package split so parsing and Contract V1 mapping are easier to
   understand and test independently.
3. A test-suite split by Traveloka module ownership.
4. Controlled removal of internal fallback paths that are no longer part of the
   current selected Traveloka runtime path.
5. Offline deterministic verification plus opt-in live smoke and a small live
   benchmark/matrix before declaring implementation complete.

Rejected alternatives:

- Workflow-only refactor: lower risk, but leaves the largest file and most of
  the parser debt untouched.
- Normalizer-only refactor: cleans the largest file, but leaves adapter and
  workflow state harder to reason about.
- Minimal cleanup: safest short term, but does not materially improve
  maintainability.

## Goals

- Preserve public `TravelokaProvider.search_exact_one_way` and
  `TravelokaProvider.search_exact_round_trip` behavior at the Contract V1
  boundary.
- Keep one-way capture and selected exact round-trip Traveloka capabilities.
- Make `adapter.py` a facade over explicit workflow functions.
- Centralize browser lifecycle and cleanup in a session helper.
- Split `normalizer.py` into smaller modules with clear ownership.
- Move Traveloka tests from a large adapter-centric file toward module-owned
  test files.
- Preserve safe public failure types and sensitive-data safety.
- Allow removal of obsolete parser or DOM fallback paths when tests prove they
  are not part of the current runtime path.
- Keep default tests offline and deterministic.
- Verify deeper changes with an opt-in live smoke and a small live matrix.

## Non-Goals

- No Contract V1 schema changes.
- No new MCP tool shape.
- No provider selection field.
- No direct HTTP replay.
- No login, captcha solving, proxy rotation, identity rotation, retries, or
  persistent browser state.
- No exhaustive search across multiple outbound and return combinations.
- No live provider calls in default tests.
- No broad search planner, ranking, airport resolver, or packaging refactor
  outside the Traveloka provider boundary.

## Architecture

Traveloka should have three clear layers:

1. `provider.py`
   - async provider boundary
   - timeout/result/error mapping into `ProviderResult`
   - Contract V1 error creation for whole-call adapter failures and safe partial
     metadata

2. `adapter.py`
   - thin sync facade
   - validates adapter configuration
   - creates workflow dependencies
   - delegates exact one-way and selected round-trip work
   - exposes phase timings

3. Internal modules
   - browser session lifecycle
   - selected round-trip workflow
   - capture, inventory, activation, selection, totals, URLs, result contracts,
     provider-local errors, and normalizer package internals

Target structure:

```text
cheapy/providers/traveloka/
  provider.py
  adapter.py
  session.py
  workflow.py
  results.py
  errors.py
  browser_helpers.py
  urls.py
  capture.py
  inventory.py
  activation.py
  selection.py
  totals.py
  timing.py
  normalization/
    __init__.py
    entrypoints.py
    payloads.py
    canonical.py
    legs.py
    routes.py
    selected.py
    ranking.py
    errors.py
```

The package may keep `cheapy/providers/traveloka/normalizer.py` as a temporary
compatibility shim that re-exports `normalize_payload` and
`normalize_selected_round_trip` from `normalization.entrypoints`. Once internal
imports and tests are migrated, keeping or deleting that shim is an
implementation choice. If kept, it must be tiny and contain no parsing logic.

## Component Contracts

### Provider Boundary

`provider.py` remains the only module that creates `ProviderResult`.

It owns:

- provider name and capability strings
- dispatch to the adapter facade
- whole-call exception mapping
- safe partial failure metadata mapping
- duration measurement
- retryability aggregation

It must not own browser lifecycle, Traveloka DOM logic, payload parsing, or
selected itinerary binding.

### Adapter Facade

`adapter.py` should read as configuration plus delegation:

- constructor validation
- dependency defaults, including browser launcher and phase recorder
- `phase_timings`
- `search_exact_one_way`
- `search_exact_round_trip`

The implementation should avoid embedding the selected round-trip state machine
directly in `adapter.py`. Workflow details belong in `workflow.py`.

### Browser Session

`session.py` owns browser resource lifecycle:

- launching the browser
- creating context and page
- registering the response handler
- navigating to a Traveloka search URL
- exposing page and capture state to workflow code
- cleanup in one place
- preserving phase timing records around launch, setup, navigation, and cleanup

`session.py` should not know how to select outbound or return flights. It should
not normalize offers or create Contract V1 models.

### Workflow

`workflow.py` owns the high-level search workflow:

- exact one-way capture workflow
- selected round-trip workflow
- stage-level helper functions such as capture outbound, select outbound,
  capture return, select return, read final total, build selected result
- stage-to-partial-failure mapping
- small state dataclasses such as `RoundTripWorkflowState`

Expected selected round-trip workflow:

1. Launch/navigate through `session.py`.
2. Capture outbound payload.
3. Discover visible outbound inventory.
4. Pick the cheapest visible outbound option.
5. Bind the option to captured payload item IDs.
6. Activate outbound and wait for outbound transition.
7. Capture return payload.
8. Discover visible return inventory.
9. Pick the cheapest visible return option.
10. Bind the option to captured payload item IDs.
11. Capture pre-return final-total text markers.
12. Activate return and wait for return transition.
13. Read final selected round-trip total.
14. Return `TravelokaSelectedRoundTripResult`.

Workflow code must not create `ProviderResult` or `FlightOfferV1`.

### Normalization Package

`normalization.entrypoints` owns the public normalizer functions:

- `normalize_payload(payload, request)`
- `normalize_selected_round_trip(result, request)`

The rest of the package owns focused internals:

- `payloads.py`
  - direct Traveloka payload list discovery
  - recursive offer fallback if retained
  - list-at-path helpers
- `canonical.py`
  - Traveloka `searchResults` canonicalization
  - price extraction from Traveloka search-result metadata
  - connecting-flight route segment canonicalization
- `legs.py`
  - segment-to-`FlightLegV1` parsing
  - datetime normalization
  - duration parsing
  - safe enum/string conversion
- `routes.py`
  - one-way route validation
  - round-trip route validation
  - raw round-trip outbound-only partial handling
  - exact date validation
  - route-derived stops and duration
- `selected.py`
  - selected outbound and selected return item lookup
  - selected round-trip Contract V1 offer creation
  - fallback to outbound partial offers when selected data is incomplete
- `ranking.py`
  - comparable and non-comparable ranking behavior
- `errors.py`
  - normalizer-local `ErrorV1` factories
  - parse, currency, return-details, and selected-total errors

Only normalizer entrypoints and selected normalization should create
`FlightOfferV1`. Normalizer modules may create `ErrorV1` only through
normalizer-local error factories.

## Behavior Rules

Public behavior remains stable at the Cheapy boundary:

- `TravelokaProvider.search_exact_one_way` returns a `ProviderResult`.
- `TravelokaProvider.search_exact_round_trip` returns a `ProviderResult`.
- One-way search still captures first-party Traveloka fare payloads and
  normalizes them.
- Round-trip search still uses selected flow: cheapest visible outbound,
  return inventory after outbound selection, cheapest visible return, final
  selected round-trip total after return selection.
- A comparable Traveloka round-trip offer is produced only when selected
  outbound key, selected return key, final selected total, and selected
  route/date evidence are all available.
- Raw round-trip capture remains a partial, non-comparable fallback.
- Default tests do not call live Traveloka.

Internal behavior may change when it improves maintainability and stays within
the verified boundary:

- workflow state may be represented with new internal dataclasses
- adapter and workflow may pass stage results differently
- obsolete parser fallbacks may be deleted
- obsolete DOM fallbacks may be deleted
- tests may be reorganized and rewritten around module contracts

Fallback behavior is data-shape dependent, so static reachability is not enough
to delete it safely. Any removed parser or DOM fallback requires a removal
catalog entry in the implementation commit or plan task that includes:

- the helper, branch, selector, or parser path being removed
- the existing test or runtime behavior it previously protected
- the current one-way or selected round-trip runtime path analysis
- the replacement test, or the exact reason no replacement is needed
- confirmation that public offers, partial results, failure types, and
  sensitive-data safety are preserved

A fallback can be deleted only when that catalog proves it is outside the
current runtime surface, or when an equivalent behavior test has been rewritten
against the current runtime surface. If the catalog cannot prove that, the
fallback should be moved into the new module boundary and revisited later.

## Data Flow

Exact one-way:

```text
TravelokaProvider
  -> TravelokaAdapter.search_exact_one_way
  -> workflow.search_exact_one_way
  -> session opens browser/context/page and navigates
  -> capture.CaptureState handles first-party fare responses
  -> capture.wait_for_capture
  -> TravelokaCaptureResult
  -> normalization.normalize_payload returns offers/errors
  -> provider.py wraps offers/errors into ProviderResult
```

Selected exact round-trip:

```text
TravelokaProvider
  -> TravelokaAdapter.search_exact_round_trip
  -> workflow.search_selected_round_trip
  -> session opens browser/context/page and navigates
  -> capture outbound payload
  -> inventory finds cheapest visible outbound
  -> inventory binds outbound option to payload ID
  -> activation clicks outbound
  -> selection proves outbound transition
  -> capture return payload
  -> inventory finds cheapest visible return
  -> inventory binds return option to payload ID
  -> totals captures stale-text markers
  -> activation clicks return
  -> selection proves return transition
  -> totals reads final selected total
  -> TravelokaSelectedRoundTripResult
  -> normalization.normalize_selected_round_trip returns offers/errors
  -> provider.py wraps offers/errors into ProviderResult
```

Partial selected round-trip fallback:

```text
selected workflow fails after outbound payload exists
  -> TravelokaCaptureResult with safe partial_failure_type
  -> normalization.normalize_payload returns outbound partial offers/errors
  -> provider.py appends public partial error
  -> ProviderResult(status=PARTIAL or FAILED)
```

## Error Handling

Provider-level error boundaries stay unchanged:

- `errors.py` creates `TravelokaProviderError` for adapter, session, capture,
  and workflow failures.
- normalizer error factories create `ErrorV1` only inside the normalization
  boundary.
- `provider.py` maps adapter exceptions and capture partial metadata to public
  `ProviderResult` values.

Whole-call public failure types to preserve:

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

Partial selected round-trip failure types to preserve:

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

Unknown partial metadata still maps to `partial_failure`.

Sensitive-data safety is required:

- no full URLs
- no query strings
- no cookies
- no headers
- no tokens
- no raw payloads
- no raw page content
- no user account data

Safe `source_path` values may remain public, for example
`/api/v2/flight/search/initial` or `/api/v2/flight/search/poll`.

## Testing Strategy

### Offline Baseline

Before implementation, run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: pass before moving code.

### Test Split

Move Traveloka tests toward module-owned files:

```text
tests/traveloka/
  test_capture.py
  test_inventory.py
  test_activation.py
  test_selection.py
  test_totals.py
  test_session.py
  test_workflow.py
  test_normalization_payloads.py
  test_normalization_canonical.py
  test_normalization_legs.py
  test_normalization_routes.py
  test_normalization_selected.py
  test_normalization_entrypoints.py
  test_provider.py
```

The implementation may move tests incrementally. It does not need to complete
the entire test-file split in one commit if doing so would make review harder,
but final ownership should no longer depend on one large adapter test file.

Coverage rules:

- Move existing assertions before deleting helper tests.
- Preserve behavior assertions for required selected round-trip flow.
- Add workflow tests for each stage-level partial failure.
- Keep capture tests focused on first-party URL filtering, supported paths, HTTP
  status mapping, invalid JSON, unsupported payloads, timeout, and partial
  capture.
- Keep inventory tests focused on current inventory-card discovery, price
  parsing, cheapest tie-break, and payload ID binding.
- Keep totals tests focused on selector precedence, stale text rejection, and
  ambiguous global-label rejection.
- Keep normalizer tests focused on payload discovery, canonicalization, leg
  parsing, route/date validation, selected total mapping, fallback errors, and
  ranking.

Tests that only protect removed legacy behavior should be deleted or rewritten
against the current runtime surface.

Any deleted fallback must be recorded in a fallback removal catalog. The catalog
can live in the relevant task section of the implementation plan and must be
carried into the implementation commit message or code review notes. Each entry
must name the removed behavior, the old coverage, the runtime-path rationale,
and the replacement coverage or deletion rationale.

### Offline Final Verification

After implementation:

```bash
uv run pytest tests/test_traveloka_* tests/traveloka tests/test_search.py -v
uv run pytest -v
```

If the test suite is fully migrated into `tests/traveloka/`, the first command
may omit old `tests/test_traveloka_*` paths after confirming they no longer
exist.

### Live Smoke And Benchmark

Live tests remain opt-in:

```bash
CHEAPY_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_traveloka.py -v
```

After offline tests pass, run a small live matrix through the existing live
provider or benchmark path. The matrix should include 3-5 representative
round-trip routes, for example:

| # | Origin | Destination | Departure | Return |
|---:|---|---|---|---|
| 1 | SGN | BKK | 2026-06-12 | 2026-06-17 |
| 2 | HAN | SIN | 2026-07-03 | 2026-07-08 |
| 3 | DAD | KUL | 2026-07-05 | 2026-07-10 |
| 4 | SGN | HKG | 2026-07-09 | 2026-07-14 |
| 5 | SGN | NRT | 2026-07-11 | 2026-07-18 |

The live matrix is not part of default CI. It blocks declaring this refactor
complete if the selected flow clearly regresses, for example:

- all routes fail after the refactor
- selected round-trip comparable offers disappear for routes that passed before
- runtime becomes clearly worse without an explained live-site reason
- failure types become less specific or leak unsafe details

The implementation plan must define the exact recording format for live matrix
results. At minimum, each route record should include origin, destination,
departure date, return date, status, offer count, comparable offer count,
failure types, duration in milliseconds, and whether the result came from the
baseline or refactored implementation.

## Migration Plan

1. Establish baseline and commit nothing if baseline fails.
2. Add `session.py` with tests around browser setup, response handler
   registration, navigation, phase recording, and cleanup.
3. Add `workflow.py` with one-way and selected round-trip workflow tests using
   fake sessions/pages.
4. Slim `adapter.py` until it delegates to workflow.
5. Create `normalization/` package and move entrypoints behind a compatibility
   shim.
6. Split normalizer internals by payload discovery, canonicalization, legs,
   routes, selected normalization, ranking, and errors.
7. Move tests out of `tests/test_traveloka_adapter.py` and
   `tests/test_traveloka_normalizer.py` into module-owned Traveloka test files.
8. Delete or rewrite tests for obsolete fallback paths.
9. Remove compatibility shims only when imports and tests prove they are no
   longer needed.
10. Run offline targeted tests, full tests, live smoke, and the small live
    matrix.

## Acceptance Criteria

- `adapter.py` is a thin facade over workflow/session internals.
- Browser lifecycle and cleanup live in one session abstraction.
- Selected round-trip workflow reads as named stages with explicit state.
- `normalizer.py` no longer contains the full parser implementation, or is
  removed after callers migrate to the normalization package.
- Normalization internals can be understood and tested by responsibility.
- Tests are split by module ownership instead of concentrated in the adapter
  test file.
- Required selected round-trip behavior remains covered offline.
- Raw round-trip captures remain non-comparable partial offers.
- Safe failure types and sensitive-data safety are preserved.
- Obsolete fallback behavior is removed only with explicit replacement or
  deletion rationale.
- Default tests do not make live Traveloka calls.
- Opt-in live smoke and small live matrix do not show a clear selected-flow
  regression.
