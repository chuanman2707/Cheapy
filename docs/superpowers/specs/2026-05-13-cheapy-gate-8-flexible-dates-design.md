# Cheapy Gate 8 Flexible Dates Design

Date: 2026-05-13

## Summary

Gate 8 expands Cheapy search beyond exact one-way dates. It turns
`search_mode="expanded"` into flexible-date search with a fixed plus/minus
3-day window and a Gate 8 execution budget of 10 provider calls per request.
This intentionally overrides the master-spec default of 20 calls for the first
live flexible-date release; the master-spec value remains the longer-term
target after Gate 8 stability is proven.

Gate 8 also starts true round-trip search when `return_date` is provided. A
round-trip result must come from a provider round-trip query, not from pairing
two one-way searches. This keeps user-facing results closer to bookable
itineraries and avoids synthetic fares that may not exist as a single fare
product.

The public MCP tool remains:

```text
search_cheapest_flights
```

The Contract V1 request and response shape remains unchanged. Existing fields
for `return_date`, `SearchMode.EXPANDED`, `CandidateFamily.FLEXIBLE_DATES`,
date offsets, and flexible-date flags are used for the new behavior.

## Approved Decisions

1. Flexible date window is fixed at plus/minus 3 days.
2. Gate 8 does not add new public request fields.
3. Gate 8 supports one-way flexible departure dates.
4. Gate 8 supports true round-trip search when `return_date` is present.
5. Round-trip flexible dates vary departure and return dates independently.
6. Gate 8 executes at most 10 provider calls per request.
7. Exact candidates run before flexible candidates.
8. Nearby-airport and split-ticket expansion remain deferred.
9. Provider statuses are recorded per provider call for transparent accounting.

## Goals

- Preserve existing exact one-way behavior for `search_mode="exact"` without
  regressions.
- Support exact round-trip search when `return_date` is provided.
- Support expanded one-way flexible-date search.
- Support expanded round-trip flexible-date search.
- Keep `max_results` as a global cap applied after all executed provider
  results are merged.
- Return accurate `SearchPlanV1` accounting for planned, executed, and
  truncated candidates.
- Mark flexible-date offers with date offsets and flags.
- Keep default tests deterministic and offline.
- Keep live provider smoke tests opt-in.
- Update managed agent instructions so agents can use expanded and round-trip
  search instead of treating them as deferred.

## Non-Goals

- No nearby-airport expansion.
- No split-ticket search.
- No configurable flexible-date window.
- No provider selection field.
- No storage, price history, watchlists, scheduler, or alerts.
- No currency conversion.
- No synthetic round-trip fares built from paired one-way searches.
- No new MCP tool.
- No raw upstream payloads in MCP responses.

## Product Behavior

`search_mode="exact"` remains the narrow mode:

- If `return_date` is omitted, Cheapy searches the exact one-way request.
- If `return_date` is present, Cheapy searches the exact round-trip request
  through providers that support the internal `exact_round_trip` capability.

`search_mode="expanded"` performs flexible-date expansion:

- One-way expanded search runs the exact departure date first, then departure
  dates in the plus/minus 3-day window.
- Round-trip expanded search runs the exact departure and return pair first,
  then departure and return date pairs in the plus/minus 3-day windows.
- Round-trip expansion varies departure and return dates independently.
- Invalid round-trip candidates where return date is before departure date are
  not created.
- Candidate order favors dates closest to the user's requested dates.
- Gate 8 executes at most 10 provider calls per request.
- If planned candidates exceed the execution budget, Cheapy returns the
  executed results and marks the search plan as truncated.

Offer fields describe the difference between requested and actual dates:

- `requested_departure_date` is the user's requested departure date.
- `actual_departure_date` is the date used for the returned provider offer.
- `departure_offset_days` is the actual departure offset from the requested
  date.
- `requested_return_date`, `actual_return_date`, and `return_offset_days` are
  set for round-trip offers.
- `flags.uses_flexible_departure_date` is true when the offer departure date
  differs from the requested departure date.
- `flags.uses_flexible_return_date` is true when the offer return date differs
  from the requested return date.

Cheapy adds `FLEXIBLE_DATE_USED` warnings only when returned offers include
non-exact dates. If the exact candidate is the only returned winner, Cheapy
does not warn that flexible dates were used.

## Architecture

Gate 8 should keep planning separate from provider execution.

`cheapy/search.py` remains the public orchestration module. It should validate
and resolve airports, load providers, call the planner, execute selected
candidates, normalize provider results, merge offers, rank offers, and assemble
`SearchResponseV1`.

A focused planner module should be added, for example:

```text
cheapy/search_planner.py
```

The planner owns:

- candidate generation
- candidate ordering
- budget selection
- `SearchPlanV1` accounting
- truncation flags

Provider-local models in `cheapy/providers/base.py` should add a true
round-trip request:

```text
ProviderExactRoundTripRequest
```

The provider protocol should continue to support:

```text
search_exact_one_way(request)
```

and add:

```text
search_exact_round_trip(request)
```

Providers advertise round-trip support through an internal capability string:

```text
exact_round_trip
```

This is provider-internal capability metadata, not a new public Contract V1
enum.

Gate 8 flexible-date expansion does not require providers to advertise a
separate `flexible_dates` capability. The planner implements flexible dates by
issuing multiple exact-date provider calls. The master-spec idea of a native
provider flexible-date capability remains deferred for a future provider that
can search a date calendar in a single call.

## Planner Rules

The flexible window is fixed:

```text
-3, -2, -1, 0, +1, +2, +3
```

The exact candidate is always represented by offset `0`.

One-way expanded candidate order:

```text
0, -1, +1, -2, +2, -3, +3
```

Round-trip expanded candidate order:

1. Exact pair `(0, 0)`.
2. Remaining valid pairs sorted by closeness to the requested trip.

The recommended sort key for round-trip flexible pairs is:

```text
abs(departure_offset) + abs(return_offset)
abs(departure_offset)
abs(return_offset)
departure_offset
return_offset
```

This keeps the nearest alternatives first, while making order deterministic.

Gate 8 budget:

```text
maximum provider calls per request = 10
```

Budget accounting is based on provider calls. If two enabled providers support
the candidate capability, one candidate contributes two planned provider calls.

The planner should compute the full planned candidate set first, then expand it
into an ordered provider-call list:

```text
(candidate order, provider manifest/load order)
```

Cheapy executes the first 10 calls from that ordered list. Candidate execution
is therefore call-budgeted, not all-or-nothing:

- `planned_provider_call_count` is the full count before the budget is applied.
- `executed_provider_call_count` is the number of provider calls actually run.
- `planned_candidate_count` is the full candidate count before budget.
- `executed_candidate_count` is the count of candidates with at least one
  provider call executed.
- `candidate_count_by_family` is planned candidate count by family.
- `provider_call_count_by_family` is executed provider-call count by family.

If the budget ends in the middle of a candidate's provider list, Cheapy executes
the calls that fit and skips the remaining provider calls. If the first exact
candidate alone has more than 10 provider calls, Cheapy executes the first 10
exact calls in stable provider order and marks the exact family as truncated.

If any candidate family is partially executed or skipped because of budget,
`SearchPlanV1.truncated` is true and `truncated_families` includes every
affected family.

Contract V1 has candidate families for search breadth, not trip shape. Exact
one-way and exact round-trip candidates both count under `CandidateFamily.EXACT`.
One-way and round-trip flexible-date candidates both count under
`CandidateFamily.FLEXIBLE_DATES`.

## Provider Data Flow

For each selected candidate, core search builds a provider-local request:

- one-way candidate -> `ProviderExactOneWayRequest`
- round-trip candidate -> `ProviderExactRoundTripRequest`

`ProviderExactOneWayRequest` should use these field names:

```text
origin: actual query origin IATA
destination: actual query destination IATA
departure_date: actual query departure date
requested_origin: requested origin IATA, defaulting to origin
requested_destination: requested destination IATA, defaulting to destination
requested_departure_date: requested departure date, defaulting to departure_date
passengers: Contract V1 passenger counts
```

`ProviderExactRoundTripRequest` should use these field names:

```text
origin: actual outbound origin IATA
destination: actual outbound destination IATA
departure_date: actual outbound departure date
return_date: actual inbound departure date
requested_origin: requested origin IATA, defaulting to origin
requested_destination: requested destination IATA, defaulting to destination
requested_departure_date: requested departure date, defaulting to departure_date
requested_return_date: requested return date, defaulting to return_date
passengers: Contract V1 passenger counts
```

The existing one-way field names remain the actual provider query fields. The
new requested fields let normalizers fill Contract V1 requested/actual date
fields without guessing and without breaking exact one-way call sites that only
set the existing fields.

For Gate 8, actual origin and destination are the requested airports because
nearby-airport expansion remains deferred.

`google_fli` should add round-trip support:

- Build `TripType.ROUND_TRIP`.
- Use two flight segments.
- Segment 1: origin to destination on the actual departure date.
- Segment 2: destination to origin on the actual return date.
- Preserve economy cabin, passenger counts, cheapest sort, and current timeout
  behavior.

The normalizer should continue to isolate upstream `fli` result details from
core Cheapy. For round-trip results, it should populate outbound and inbound
legs in a single `FlightOfferV1` when upstream returns them as one itinerary.

## Response Assembly

Gate 8 should update request IDs so one-way and round-trip requests cannot
collide. The request ID must include trip shape and return date:

```text
search:{trip_shape}:{origin}:{destination}:{departure_date}:{return_date_or_none}:{search_mode}:{adults}:{children}:{infants_on_lap}:{infants_in_seat}:{max_results}
```

`trip_shape` is `one_way` when `return_date` is omitted and `round_trip` when
`return_date` is present.

Core search merges all offers from executed provider calls, then applies the
same Gate 7 ordering and ranking rules after deduplication:

- Deduplicate before applying `request.max_results`.
- Within the same provider, deduplicate by exact itinerary signature and keep
  the cheapest duplicate.
- Across providers, do not deduplicate offers when either offer has
  `fare_details_status="not_collected"`.
- Cross-provider deduplication remains deferred until fare details are known
  well enough to prove two offers are equivalent.

- If all returned offers use one currency, sort by `price_amount` then
  `offer_id`.
- If currencies are mixed, group deterministically by currency and do not claim
  cross-currency comparability.
- Apply `request.max_results` after merging all executed provider results.
- Reassign ranks after sorting and truncation.

`ProviderStatusV1` should be emitted per provider call. This makes
`executed_provider_call_count` easy to reconcile with response details, and it
keeps failures on individual flexible candidates visible.

Budget-skipped provider calls are not emitted as
`ProviderStatusV1(status="skipped")` in Gate 8. They are represented through
`SearchPlanV1.truncated`, `truncated_families`, and
`CANDIDATE_FAMILY_TRUNCATED` warnings. Provider statuses describe calls Cheapy
actually attempted.

Provider status `capability` should be either:

```text
exact_one_way
exact_round_trip
```

Warnings and errors attached to provider status entries may include safe
candidate metadata such as family and date offsets if useful for debugging, but
must not include raw upstream payloads.

## Error Handling

Airport resolution failures happen before planning and before provider loading.

Unsupported scope handling:

- `search_mode="expanded"` supports flexible dates only.
- Nearby-airport and split-ticket expansion remain deferred.
- One-way searches require at least one provider with `exact_one_way`.
- Round-trip searches require at least one provider with `exact_round_trip`.

Invalid date ordering:

- `SearchRequestV1` should reject `return_date` values earlier than
  `departure_date` with a Pydantic validation error.
- Exact round-trip and expanded round-trip searches therefore never reach
  provider loading with an impossible requested date order.
- Flexible round-trip planning still omits generated candidate pairs where the
  actual return date is before the actual departure date.

If no provider supports the needed capability, Cheapy returns
`NO_PROVIDER_AVAILABLE` with a clear reason.

Provider failures keep the Gate 7 behavior:

- Offer plus error returns top-level `partial`.
- Offers without errors return `success`.
- No offers with errors returns `failed`.
- Provider exceptions are sanitized.
- Raw payloads, raw HTML, full tracebacks, URLs, and environment values are not
  returned.

Truncation behavior:

- If candidate execution is budget-truncated, response search plan marks
  `truncated=true`.
- `truncated_families` includes every family that had planned provider calls
  skipped because of budget.
- Cheapy adds `CANDIDATE_FAMILY_TRUNCATED` for every family in
  `truncated_families`.
- `SEARCH_TRUNCATED` is reserved for a future whole-request truncation case
  that is not specific to one candidate family.
- The response can still be `success` if executed candidates returned offers
  without provider errors.

## Testing

Default tests must be deterministic and offline.

Planner tests should cover:

- one-way expanded candidate order
- round-trip expanded candidate order
- fixed plus/minus 3-day window
- budget 10 truncation
- `SearchPlanV1` planned and executed accounting
- invalid round-trip date pairs are omitted

Search orchestration tests should cover:

- exact one-way regression behavior
- exact round-trip provider call routing
- `return_date < departure_date` rejected at request validation
- expanded one-way provider call routing
- expanded round-trip true round-trip provider call routing
- no synthetic two-one-way round-trip pairing
- request IDs include trip shape and return date to avoid one-way/round-trip
  collisions
- deduplication happens before sorting, `max_results`, and ranking
- flexible offer flags and offsets
- truncation warnings and search plan flags
- mixed provider success and failure producing `partial`
- no-provider errors for missing one-way or round-trip capabilities

Google Fli tests should cover:

- round-trip filter construction with `TripType.ROUND_TRIP`
- two segment construction with correct airport/date direction
- preserved passenger counts and sort behavior
- round-trip normalizer return-date fields
- date offset flags for flexible offers
- sanitized provider errors

MCP and installer tests should cover:

- MCP schema remains Contract V1 compatible.
- `return_date` and `search_mode="expanded"` are accepted by the existing tool.
- managed Codex and Claude instructions no longer say expanded, flexible, and
  round-trip search are deferred.
- instructions still say nearby-airport and split-ticket search are deferred.

Recommended focused verification:

```text
uv run pytest tests/test_search.py -v
uv run pytest tests/test_google_fli_provider.py tests/test_google_fli_normalizer.py -v
uv run pytest tests/test_mcp.py tests/test_agent_hooks.py tests/test_schema_export.py -v
uv run pytest -v
```

Live smoke tests remain opt-in. Gate 8 should keep the existing exact one-way
smoke and may add separate exact round-trip and expanded smoke coverage behind
the existing live-test gate.

## Completion Criteria

Gate 8 is complete when:

- `search_mode="exact"` still supports exact one-way.
- `search_mode="exact"` supports exact round-trip with `return_date`.
- `search_mode="expanded"` supports one-way flexible dates.
- `search_mode="expanded"` supports true round-trip flexible dates.
- Round-trip results are not synthetic one-way pairings.
- Search planning and truncation are reflected in `SearchPlanV1`.
- Flexible-date offers carry correct requested and actual dates, offsets, and
  flags.
- Provider statuses reconcile with executed provider-call counts.
- Default tests make no live network calls.
- The full test suite passes locally.
