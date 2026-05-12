# Cheapy Gate 7 Live Provider Design

Date: 2026-05-12

## Tóm tắt

Gate 7 biến Cheapy từ MCP prototype dùng fixture sang MCP search có live provider mặc định.

Provider live đầu tiên là `google_fli`, dùng package upstream `flights` với import namespace `fli`. Đây là integration unofficial/reverse-engineered, không phải Google Flights public API chính thức. Google Travel Analytics Live API hiện là cơ chế Google gửi query tới partner endpoint, không phải API để Cheapy query Google Flights. Google QPX Express API cũ đã bị shutdown từ ngày 2018-04-10.

MCP tool không đổi tên và không đổi contract:

```text
search_cheapest_flights
```

Agent vẫn gọi một tool duy nhất. Cheapy gọi tất cả user-facing live providers đang enabled và hỗ trợ exact one-way. Kết quả trả về vẫn là `SearchResponseV1`; mỗi offer ghi rõ `provider`, và `provider_statuses` ghi tình trạng từng provider. Agent đọc `offers[]` để nói với user provider nào rẻ nhất.

## Quyết định đã duyệt

Gate 7 dùng hướng:

1. Thêm `flights` là runtime dependency chính, không phải optional extra.
2. Thêm provider `cheapy.providers.google_fli`.
3. `google_fli` có mặt mặc định khi package tồn tại.
4. Normal MCP search gọi live provider mặc định.
5. `manual_fixture` không xuất hiện trong user-facing `search_cheapest_flights` khi có live provider.
6. `max_results` là global cap trên danh sách offer sau khi gộp mọi provider.
7. Không expose provider selection trong request. Agent và user không chọn provider.

## Goals

- Add live exact one-way provider `google_fli`.
- Keep `search_cheapest_flights` as the only user-facing MCP search tool.
- Preserve Contract V1 request and response shapes.
- Normalize upstream `fli` results into `FlightOfferV1`.
- Return provider attribution on every offer through existing `offer.provider`.
- Return one `ProviderStatusV1` per provider call.
- Exclude fixture providers from normal user-facing search.
- Keep default tests local and deterministic, with no live network calls.
- Add opt-in live smoke coverage for the live provider lane.
- Update MCP tool annotations so clients know the tool interacts with external systems.

## Non-Goals

- No round-trip search.
- No expanded search.
- No flexible-date search.
- No nearby-airport expansion.
- No split-ticket search.
- No `cheapy search` CLI.
- No storage, price history, watchlists, scheduler, or alerts.
- No provider selection field in `SearchRequestV1`.
- No raw upstream payloads in MCP responses.
- No fixture fallback during user-facing live search.
- No official Google Flights API claim for `google_fli`.

## Product Behavior

The user-facing behavior remains agent-first:

1. User asks an agent for a flight search.
2. Agent normalizes origin, destination, date, and passengers.
3. Agent calls `search_cheapest_flights`.
4. Cheapy calls enabled live providers.
5. Cheapy returns structured offers with provider attribution.
6. Agent explains the cheapest relevant results to the user.

When `google_fli` is installed with Cheapy, a normal MCP call uses `google_fli` by default. The response includes:

```json
{
  "provider": "google_fli"
}
```

on each returned `FlightOfferV1`.

If future live providers are added, Cheapy calls all enabled providers that advertise the requested capability. Cheapy returns a single globally sorted `offers[]` list plus `provider_statuses[]`. It does not add a separate winner field. The agent decides how to explain the cheapest provider to the user.

`manual_fixture` remains in the package for deterministic tests, fixture checks, and development. It is not included in normal user-facing MCP search.

## Architecture

Gate 7 adds:

```text
cheapy/providers/google_fli/
  __init__.py
  manifest.toml
  provider.py
  adapter.py
  normalizer.py
```

`provider.py` implements the existing `FlightProvider` protocol. It accepts `ProviderExactOneWayRequest`, delegates upstream request construction to `adapter.py`, delegates response conversion to `normalizer.py`, and returns `ProviderResult`.

`adapter.py` isolates the upstream `fli` API. It owns imports and construction for objects such as:

- `SearchFlights`
- `FlightSearchFilters`
- `FlightSegment`
- `Airport`
- `PassengerInfo`
- `SeatType`
- `TripType`
- `SortBy`

`normalizer.py` converts upstream flight results into Contract V1 `FlightOfferV1` objects. It owns:

- defensive field extraction
- price parsing
- currency handling
- offer ID construction
- leg conversion
- total duration and stop count mapping
- flags
- fare-detail status
- parse failure reporting

Core Cheapy must not depend directly on upstream `fli` result internals.

## Provider Manifest Policy

Provider manifests gain a required provider kind:

```toml
provider_kind = "live" | "fixture"
```

`google_fli/manifest.toml`:

```toml
manifest_schema_version = "1"
name = "google_fli"
display_name = "Google Fli live provider"
default_enabled = true
provider_kind = "live"
module = "cheapy.providers.google_fli.provider"
capabilities = ["exact_one_way"]
```

`manual_fixture/manifest.toml` is updated with:

```toml
provider_kind = "fixture"
```

Registry behavior:

- `discover_provider_manifests()` validates `provider_kind`.
- normal search uses `load_search_providers()`, which loads `default_enabled=true` and `provider_kind!="fixture"`.
- fixture tests and local fixture checks can use an explicit fixture-capable loader.
- no user-controlled provider paths are introduced.

## Data Flow

`search_cheapest_flights` runs:

1. MCP adapter validates top-level tool arguments with `SearchRequestV1`.
2. `search_exact(request)` resolves origin and destination IATA.
3. `search_exact` rejects non-Gate-7 scope exactly as Gate 4 does:
   - `search_mode="expanded"` is unsupported.
   - `return_date` is unsupported.
4. `search_exact` loads user-facing enabled providers.
5. Providers without `exact_one_way` are skipped.
6. Each exact provider receives `ProviderExactOneWayRequest`.
7. `google_fli` maps the request to upstream `fli` filters:
   - one-way trip
   - economy cabin
   - exact origin IATA
   - exact destination IATA
   - exact departure date
   - Contract V1 passenger counts
   - cheapest sort when upstream supports it
8. `google_fli` calls `SearchFlights().search(filters)` through a bounded timeout.
9. `google_fli` normalizes upstream results to `FlightOfferV1`.
10. Core collects offers, warnings, errors, and provider statuses.
11. Core sorts offers globally.
12. Core truncates the final combined list to `request.max_results`.
13. Core returns `SearchResponseV1`.

## Offer Ordering And Ranks

`max_results` remains a global cap.

When all returned offers have the same currency, Cheapy sorts by:

1. `price_amount`
2. `offer_id`

When multiple currencies appear, Cheapy keeps deterministic grouping without claiming cross-currency comparability:

1. `currency`
2. `price_amount`
3. `offer_id`

Gate 7 must make final ranks reflect the returned, sorted, truncated offer list. Provider-specific ranks from upstream are not trusted as final global ranks.

For single-currency responses:

- `comparable=true`
- `rank_within_currency` is assigned within that currency
- `global_rank` is assigned across the returned list

For mixed-currency responses:

- `mixed_currency=true`
- `comparable=false`
- `rank_within_currency` is assigned per currency
- `global_rank=null`
- `currency_notes` explains that Cheapy did not convert currencies

## Currency Handling

Contract V1 requires every offer to include `currency`.

The normalizer uses upstream currency when it is available on the result object. If the exact `SearchFlights` result shape does not expose a currency field, `google_fli` uses `USD` as the provider currency because upstream examples document dollar prices and date-price examples document `USD`. This behavior must be documented in provider code comments or provider docs, and tests must assert it explicitly.

Cheapy must not convert currencies in Gate 7.

## Error Handling

`google_fli` must fail with structured provider results.

Expected mappings:

- upstream dependency unavailable: `PROVIDER_FAILED`, `retryable=false`, `details.failure_type="dependency_unavailable"`
- unsupported airport by upstream enum/model: `PROVIDER_FAILED`, `retryable=false`, `details.failure_type="unsupported_airport_by_upstream"`
- network or transport error: `PROVIDER_FAILED`, `retryable=true`, `details.failure_type="transport_error"`
- timeout: `PROVIDER_TIMEOUT`, `retryable=true`, `details.failure_type="timeout"`
- detected rate limit: `PROVIDER_RATE_LIMITED`, `retryable=true`
- detected block: `PROVIDER_BLOCKED`, `retryable=false`
- upstream shape or parser failure for the whole response: `PROVIDER_FAILED`, `retryable=false`, `details.failure_type="parse_error"`
- successful upstream call with no flights: `ProviderStatusCode.SUCCESS` with `offers=[]`
- partial parse failure: `ProviderStatusCode.PARTIAL`, parsed offers preserved, structured error details include skipped item count

Provider errors must not include:

- raw upstream payloads
- raw HTML
- full tracebacks
- full exception messages when they might include URLs or provider internals
- environment variables
- secrets

Core response status rules stay consistent with Gate 4:

- offers and no errors: `success`
- offers and errors: `partial`
- no offers: `failed`

## Timeout And Concurrency

Gate 7 adds a provider-local timeout for `google_fli`. The target timeout is 30 seconds per provider call, matching the master spec default.

The provider interface remains async. If upstream `fli` search is synchronous, `google_fli` must run it in a worker thread from the provider async method and wrap it with an async timeout.

Gate 7 does not add cross-provider concurrency tuning beyond the existing sequential provider calling. The provider-local timeout prevents a single live call from hanging forever. Broader provider concurrency remains a later gate.

## MCP Behavior

The MCP tool remains:

```text
search_cheapest_flights
```

The input schema remains based on `SearchRequestV1`.

The output remains structured `SearchResponseV1`.

Gate 7 changes MCP annotations:

```python
openWorldHint = True
```

because user-facing search now calls an external live provider by default.

Stdout remains MCP protocol-clean. Logs, diagnostics, provider warnings, and unexpected errors go to stderr or structured MCP results, never non-protocol stdout.

## CLI Behavior

`cheapy providers list` shows provider kind:

```json
{
  "name": "google_fli",
  "provider_kind": "live",
  "default_enabled": true,
  "enabled": true
}
```

and:

```json
{
  "name": "manual_fixture",
  "provider_kind": "fixture"
}
```

`cheapy providers test` remains safe by default and must not make live network calls. It must:

- run deterministic fixture checks for fixture providers
- validate live provider manifests/importability without calling live network
- mark live provider smoke checks as skipped unless live mode is explicitly requested

Gate 7 adds an explicit live smoke option:

```bash
cheapy providers test --live
```

Live smoke execution must also require an environment gate:

```text
CHEAPY_RUN_LIVE_TESTS=1
```

## Testing

Default tests must not call live network.

### Unit Tests

Normalizer tests use fake upstream flight objects and cover:

- valid one-way flight normalization
- provider name set to `google_fli`
- price and currency mapping
- leg mapping
- stop count mapping
- duration mapping
- offer ID stability
- fare details status is `not_collected`
- malformed item handling
- no raw payload leakage

### Provider Tests

Provider tests mock upstream `SearchFlights().search(...)` and cover:

- exact one-way request construction
- passenger mapping
- successful provider result
- empty upstream result as success with no offers
- dependency error mapping
- transport error mapping
- timeout mapping
- parser failure mapping
- partial parse behavior

### Search And MCP Tests

Search/MCP tests mock provider loading and cover:

- normal search excludes `manual_fixture`
- normal search includes live provider output
- `max_results` is a global cap
- `offers[].provider` is preserved
- `provider_statuses[]` includes the provider name and status
- final ranking is assigned after global sorting
- MCP tool still returns structured `SearchResponseV1`
- MCP annotation `openWorldHint=True`

### Live Tests

Live tests are opt-in only:

```python
@pytest.mark.live
```

They run only when:

```text
CHEAPY_RUN_LIVE_TESTS=1
```

The smoke route follows the master spec:

```text
origin: SGN
destination: BKK
departure_date: current date + 30 days
trip type: one-way
```

Live tests assert structure, not exact prices:

- provider call returns structured status
- parser does not crash
- normalized offers validate if results exist
- provider failure is structured if live search fails

## Acceptance Criteria

Gate 7 is complete when:

- `flights` is a runtime dependency of `cheapy-flights`.
- `google_fli` appears in `cheapy providers list` as `provider_kind="live"` and `default_enabled=true`.
- `manual_fixture` appears as `provider_kind="fixture"`.
- `search_cheapest_flights` normal user-facing path does not return `manual_fixture` offers.
- A mocked `google_fli` search returns valid `SearchResponseV1`.
- Every live offer includes `provider="google_fli"`.
- Provider failures are structured and do not leak raw upstream data.
- `max_results` is applied globally after all provider results are combined.
- MCP annotation `openWorldHint=True`.
- `uv run pytest -v` passes without live network calls.
- Opt-in live smoke tests run only with the live marker and environment gate.

## References

- Google Travel Analytics Live API help: https://support.google.com/travelanalytics/answer/15669585
- Upstream `fli` repository and docs: https://github.com/punitarani/fli
