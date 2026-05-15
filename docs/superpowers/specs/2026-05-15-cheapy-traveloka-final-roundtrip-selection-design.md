# Cheapy Traveloka Final Round-Trip Selection Design

Date: 2026-05-15

## Summary

Extend the Traveloka browser provider so an exact round-trip search can return a
complete, comparable round-trip offer instead of stopping at the first outbound
fare list.

The current Traveloka browser flow can capture priced outbound cards. Some
routes label those cards as a round-trip combo, but the Paris to Nha Trang test
showed that the first-step displayed price is not always a reliable final
round-trip total. Therefore Cheapy must not mark a Traveloka round-trip offer as
comparable until the browser has selected both legs and read the final total
after selection.

## Approved V1 Scope

For exact round-trip Traveloka searches:

1. Load the Traveloka round-trip results page.
2. Capture the outbound result list.
3. Select exactly one outbound option: the cheapest visible outbound option.
4. Capture the return result list shown after that outbound selection.
5. Select exactly one return option: the cheapest visible return option.
6. Read the final round-trip total after both legs are selected.
7. Return a complete comparable offer only when outbound details, return
   details, and final total are all available.

This v1 intentionally does not explore the top two or top three outbound
options. The result is the cheapest final itinerary within the single branch
created by choosing the cheapest outbound option first. Cheapy must not claim it
has exhaustively optimized every possible outbound and return pairing.

## Corrected Price Rule

The first Traveloka outbound result page is no longer trusted as the source of
the final round-trip total.

The final comparable Traveloka price must come from the selected-itinerary state
after both the outbound and return flights have been chosen. If Traveloka
reprices after return selection, the final selected-itinerary total wins.

If the final selected-itinerary total cannot be read or parsed, the provider
falls back to the existing partial behavior:

- return the best priced outbound information captured so far
- set `actual_return_date=null`
- set `comparable=false`
- set rank fields to `null`
- attach a clear provider error

## Runtime Flow

One-way searches stay unchanged.

Round-trip searches use a two-selection browser flow:

1. Build the Traveloka round-trip URL from the exact Cheapy request.
2. Launch a fresh CloakBrowser context.
3. Register response listeners before navigation.
4. Navigate to the Traveloka result page.
5. Wait for outbound results to render and for a supported outbound fare payload
   to be captured.
6. Identify the cheapest visible outbound option.
7. Bind that visible card to a captured outbound item when possible, using
   stable visible data such as airline name, departure time, arrival time,
   displayed route, and displayed price.
8. Click the outbound card's selection action. The locator should support the
   Traveloka labels observed in practice, including `Choose` and `Chọn`.
9. Wait for the return-selection state.
10. Capture return flight data emitted by Traveloka's own web app.
11. Identify the cheapest visible return option.
12. Bind that visible return card to a captured return item when possible.
13. Click the return card's selection action, with the same label handling.
14. Read the final selected-itinerary total from the post-selection UI or a
   supported first-party payload produced by that UI state.
15. Close browser resources in `finally`.

The adapter should continue listening only to first-party Traveloka browser
traffic produced by the loaded page. It must not introduce direct HTTP replay,
persisted cookies, login, captcha solving, proxy rotation, or provider-internal
retry loops.

## Adapter Contract

`cheapy/providers/traveloka/adapter.py` should keep the existing
`TravelokaCaptureResult` contract for one-way and partial round-trip captures,
then add a round-trip-specific result shape for completed selections:

```python
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
```

Provider code can then distinguish:

- existing `TravelokaCaptureResult`: partial or one-way capture
- `TravelokaSelectedRoundTripResult`: full selected round-trip capture

`source_paths` must contain only safe API paths, never full URLs, cookies,
headers, tokens, or query strings.

For a result to be treated as a complete comparable round trip, both selected
keys must be bound to reliable captured or rendered flight details, and the
final amount and currency must come from the selected-itinerary state after the
return leg has been selected. A configured provider default currency is not
enough for this final selected total.

## Normalization

`normalizer.py` should add a selected-round-trip path that builds one
`FlightOfferV1` from:

- the selected outbound item
- the selected return item
- the final selected-itinerary total

For a successful selected round trip:

- `requested_departure_date` and `requested_return_date` reflect the user's
  exact request
- `actual_departure_date` comes from the selected outbound leg
- `actual_return_date` comes from the selected return leg
- `return_offset_days` is computed normally
- `comparable=true`
- rank fields can be assigned normally
- price comes from the final selected-itinerary total, not the initial outbound
  card

If either selected leg cannot be mapped to reliable flight details, the
normalizer must not synthesize a complete itinerary. It should return the
existing partial offer shape and attach a specific provider error.

## Error Handling

The provider should preserve useful partial output whenever Traveloka has
already returned usable priced data.

Provider-wide failure remains appropriate when no usable outbound data exists.
After outbound data exists, failures become partial errors:

- `outbound_selection_unavailable`: cheapest outbound could not be clicked
- `selected_outbound_binding_unavailable`: clicked outbound could not be mapped
  to captured flight details
- `return_capture_timeout`: return list did not arrive before timeout
- `return_selection_unavailable`: cheapest return could not be clicked
- `selected_return_binding_unavailable`: clicked return could not be mapped to
  captured flight details
- `final_round_trip_total_unavailable`: both selections happened, but the final
  total could not be read or parsed

Timeout behavior should remain deadline-based. The adapter owns browser cleanup
and should not be wrapped by an equal-duration outer timeout that can preempt
partial result return.

## Ranking Semantics

Only full selected Traveloka round-trip offers are comparable with other
providers.

Partial Traveloka offers remain non-comparable and must keep:

- `comparable=false`
- `rank_within_currency=null`
- `global_rank=null`

This prevents Cheapy from ranking a Traveloka outbound-only or not-yet-final
price against providers that returned complete round-trip itineraries.

## Tests

Default tests remain offline and must not call Traveloka or launch a real
browser.

Add focused tests for:

- cheapest visible outbound selection
- cheapest visible return selection
- final total parsing after both legs are selected
- successful selected round-trip normalization with `comparable=true`
- fallback to partial when outbound click fails
- fallback to partial when return capture times out
- fallback to partial when final total is unavailable
- search aggregation ranking full selected Traveloka offers normally while
  keeping partial Traveloka offers non-comparable

The existing opt-in live smoke test can be extended to allow either:

- full selected round-trip success with `comparable=true`
- partial fallback with a specific error when Traveloka changes UI/API behavior
  or times out

Live smoke remains gated by `CHEAPY_RUN_LIVE_TESTS=1`.

## Non-Goals

V1 will not:

- test multiple outbound branches
- guarantee the mathematically cheapest possible Traveloka round trip across all
  outbound and return combinations
- add login, app-only APIs, captcha solving, proxy rotation, or persisted
  sessions
- add direct HTTP replay for Traveloka
- store Traveloka responses or user searches
- expose provider cookies, headers, tokens, or full request URLs in Contract V1
