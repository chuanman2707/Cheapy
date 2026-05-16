# Cheapy Traveloka Safe Browser Optimization Design

Date: 2026-05-16

## Summary

Optimize the existing Traveloka browser-only selected round-trip provider without
changing the provider contract or introducing HTTP replay, user cookies, login,
or persistent browser state.

The current provider now succeeds on the selected round-trip flow for a 10-route
live benchmark, but each route takes about 24-28 seconds. This design focuses on
making that flow more observable first, then reducing avoidable waiting while
preserving the selected-final-total correctness rule.

## Current Method

The Traveloka adapter currently uses a browser-driven selected flow with
first-party API response capture:

1. Launch a fresh browser context.
2. Register a response listener.
3. Navigate to the Traveloka round-trip URL.
4. Capture first-party Traveloka fare payloads from:
   - `/api/v2/flight/search/initial`
   - `/api/v2/flight/search/poll`
5. Read visible inventory cards from the page.
6. Bind visible card keys to captured payload item IDs.
7. Select the cheapest visible outbound card.
8. Capture the return inventory payload after outbound selection.
9. Select the cheapest visible return card.
10. Read the selected round-trip final total from the post-selection UI.

This is not pure DOM scraping. The browser creates the Traveloka app state and
the adapter combines captured API payloads with DOM selection and final-total
reading.

## Goals

1. Add phase-level timing telemetry for the Traveloka adapter.
2. Reduce unnecessary final-total DOM selector scans by using a primary fast
   path before broad fallback scans.
3. Add an experimental early-proceed mode for stable, bindable visible options
   so the adapter can click sooner when enough evidence is already present.
4. Keep conservative browser-only behavior as the default unless the fast mode
   is explicitly enabled.
5. Preserve exact selected round-trip correctness: a comparable Traveloka
   round-trip offer still requires a selected outbound key, selected return key,
   and final selected total.

## Non-Goals

This design does not include:

- direct HTTP replay
- manual cookie input
- persistent user browser profiles
- Traveloka login or account state
- captcha solving, challenge bypass, proxy rotation, or identity rotation
- Traveloka city/all-airports codes such as `BKKA`
- `/en-vn` or VND parity work
- Contract V1 schema changes
- making raw Traveloka round-trip partial offers comparable
- exhaustive search across multiple outbound-return combinations

## Proposed Changes

### Phase Timing Telemetry

Add adapter-local timing collection around the major browser phases:

- browser launch
- context/page setup
- initial navigation
- outbound capture wait
- outbound visible-option discovery
- outbound binding
- outbound click and transition
- return capture wait
- return visible-option discovery
- return binding
- return click and transition
- final total read
- cleanup

Telemetry must remain safe:

- no full URLs
- no cookies
- no headers
- no raw payloads
- no query strings
- no user account data

Telemetry remains internal in V1. It can be exposed through test hooks or
developer benchmark scripts, but it must not be added to Contract V1 responses.

### Final Total Fast Path

The final-total reader currently supports multiple selectors to survive
Traveloka DOM variations. That broad scan is useful, but it can be expensive
when repeated during polling.

V1 should add a prioritized fast path:

1. Try the most recently successful selector for the current adapter run, if
   known.
2. Try the current live-selected summary scope first, including
   `#flight-search-result`.
3. Try selected/final/checkout total selectors.
4. Fall back to the existing broad selector scan.

The stale-text guard must remain in force. A total that existed before return
selection must not be accepted as the final selected total unless a new selected
state is detected and the text changes.

### Stable Visible Bindable Options Early Proceed

The current capture wait tends to wait for `searchCompleted=True`. That is
correct but can cost time because usable priced inventory often appears before
Traveloka marks the search complete.

Add an experimental fast mode that can proceed when both conditions are true:

1. A visible cheapest option exists and can be bound to the current captured
   payload item IDs.
2. The same cheapest bound option remains stable for a small number of polling
   samples or for a minimum dwell period.

This mode must be opt-in through the environment flag
`TRAVELOKA_FAST_STABLE_OPTIONS=1`. The default is disabled.

Conservative behavior remains the default. If stability cannot be proven before
the normal search completes, the adapter follows the current wait behavior.

The early-proceed result should still be described as the selected candidate
produced by the existing heuristic:

- choose the cheapest visible outbound card available at selection time
- choose the cheapest visible return card available after outbound selection
- read the final selected total for that pair

The provider must not claim this is an exhaustive cheapest final itinerary.

## Data Flow

Default conservative flow:

1. Navigate.
2. Wait for completed outbound capture or timeout.
3. Discover and bind outbound option.
4. Select outbound.
5. Wait for completed return capture or timeout.
6. Discover and bind return option.
7. Select return.
8. Read final selected total through fast path and fallback selectors.

Experimental fast-stable-options flow:

1. Navigate.
2. While capture is in progress, sample visible options and payload item IDs.
3. If a cheapest visible option is bindable and stable, proceed before
   `searchCompleted=True`.
4. If not stable, keep waiting for the normal completion condition.
5. Apply the same logic after outbound selection while waiting for return
   inventory.

## Error Handling

Existing failure types remain valid. The optimization must preserve these
structured partial states:

- `outbound_selection_unavailable`
- `selected_outbound_binding_unavailable`
- `outbound_selection_transition_unavailable`
- `return_capture_timeout`
- `return_selection_unavailable`
- `selected_return_binding_unavailable`
- `final_round_trip_total_unavailable`

If early proceed is enabled but conditions are not met, it must not introduce a
new public failure. The adapter should fall back to conservative waiting.

If the fast final-total path fails, it must fall back to the broad existing
reader before returning `final_round_trip_total_unavailable`.

## Testing

Unit tests should cover:

- phase timing records phases without leaking URLs, cookies, headers, or
  payloads
- final-total reader uses a prioritized selector before broad fallback
- stale pre-return totals remain rejected
- stable visible option can proceed before search completion when opt-in is
  enabled
- unstable visible option does not proceed early
- unbindable visible option does not proceed early
- conservative mode keeps current wait-for-completion behavior
- all existing Traveloka provider and normalizer tests still pass

Live benchmark should compare before and after:

- same 10 airport round-trip routes used in the latest benchmark
- success count
- partial/failure count
- failure types
- average duration
- p50 duration
- p95 duration
- phase timing breakdown

## Acceptance Criteria

1. Default provider behavior remains conservative and contract-compatible.
2. With fast mode disabled, existing Traveloka tests continue to pass.
3. With fast mode enabled, the adapter never returns a comparable Traveloka
   round-trip offer without selected outbound binding, selected return binding,
   and final selected total.
4. Final-total fast path does not accept stale totals from before return
   selection.
5. Live benchmark shows no correctness regression versus the current browser
   selected flow.
6. If speed improves, the benchmark report includes phase timing evidence
   explaining where time was saved.

## Risks

Early proceed can miss a cheaper fare that would have appeared later. This is
why it must remain opt-in and must not be described as exhaustive cheapest
search.

Final-total selector prioritization can accidentally overfit to one Traveloka
DOM state. This is mitigated by preserving broad fallback scanning and stale
text filtering.

Telemetry can accidentally leak sensitive request details. This is mitigated by
recording phase names, durations, counts, and safe failure types only.

## Implementation Notes

The implementation should prefer small helper units:

- a timing recorder
- a final-total selector strategy
- a stable visible-option sampler

These helpers should be tested independently where possible so
`adapter.py` does not accumulate more unbounded control-flow complexity.
