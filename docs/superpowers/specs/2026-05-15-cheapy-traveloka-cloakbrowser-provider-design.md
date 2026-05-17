# Cheapy Traveloka CloakBrowser Provider Design

Date: 2026-05-15

## Summary

Replace the current Traveloka HTTP-only adapter with a browser-only Traveloka
runtime powered by CloakBrowser.

Traveloka remains enabled by default for normal Cheapy search. When Cheapy calls
the Traveloka provider, it launches a CloakBrowser Chromium session, navigates
the normal Traveloka flight-search page, captures the first-party fare API
responses emitted by Traveloka's own web app, normalizes those responses into
Contract V1 offers, and closes the browser.

This supersedes the previous HTTP-only replayer constraint for Traveloka.

## Approved Decisions

1. Traveloka is browser-only at runtime.
2. Traveloka uses CloakBrowser, not stock Playwright, as the browser runtime.
3. Traveloka is enabled by default in normal user-facing search.
4. No HTTP-only Traveloka fallback is attempted before or after the browser flow.
5. The provider-level timeout is 45 seconds.
6. If the browser captures usable partial offers before timeout, the provider
   returns those offers.
7. The provider consumes at most two fare API responses per provider call:
   `search/initial` and, if needed, one `search/poll`.
8. No login, account-backed session, captcha solving service, proxy rotation,
   persisted cookies, or provider-internal retry loop is added.
9. Default tests remain offline and do not call Traveloka or launch a real
   browser.

The project owner's stated Traveloka support approval remains the permission
assumption for this codebase. Deployments outside that permission must disable
the provider before use.

## Why Browser-Only

Endpoint discovery proved that the real Traveloka fare endpoints exist:

```text
POST /api/v2/flight/search/initial
POST /api/v2/flight/search/poll
```

The useful fare data is in `data.searchResults[]`. However, direct HTTP replay
without the browser-created Traveloka session and challenge artifacts returned
HTML or an empty `202 text/html` response. A first-party-only HTTP bootstrap was
also not sufficient.

The successful path was a real browser session that let Traveloka's web app
create the required runtime state and then call the fare APIs. Therefore the
correct product fix for the current goal is to run Traveloka through a browser,
not to keep trying to replay the web app with bare HTTP.

## CloakBrowser Dependency

Add `cloakbrowser` as a normal runtime dependency in `pyproject.toml`.

Rationale:

- The approved user-facing behavior is simple: Traveloka always runs by browser.
- Making CloakBrowser optional would make default-enabled Traveloka fail with a
  setup error on clean installs.
- CloakBrowser's Python API returns Playwright-compatible browser/context/page
  objects, so the provider can use familiar browser automation patterns.

Packaging boundaries:

- Cheapy lists `cloakbrowser` as a dependency.
- Cheapy does not vendor, modify, commit, or redistribute the CloakBrowser
  Chromium binary.
- Runtime may trigger CloakBrowser's normal first-run binary download and local
  cache behavior.
- Docker or hosted deployments that preinstall or redistribute the binary need a
  separate license review before doing so.

Reference facts checked from CloakBrowser upstream:

- Python package name: `cloakbrowser`
- Dependencies include `playwright>=1.40` and `httpx>=0.24`
- Basic API: `from cloakbrowser import launch`
- `launch()` returns a standard Playwright `Browser` object
- First run auto-downloads a Chromium binary around 200 MB
- Wrapper source is MIT licensed; the compiled browser binary has a separate
  binary license

Sources:

- https://github.com/CloakHQ/CloakBrowser
- https://github.com/CloakHQ/CloakBrowser/blob/main/pyproject.toml
- https://github.com/CloakHQ/CloakBrowser/blob/main/BINARY-LICENSE.md

## Architecture

Keep the public provider shape unchanged:

```text
cheapy/providers/traveloka/
  __init__.py
  manifest.toml
  provider.py
  adapter.py
  normalizer.py
```

`manifest.toml` stays default-enabled:

```toml
manifest_schema_version = "1"
name = "traveloka"
display_name = "Traveloka research provider"
default_enabled = true
provider_kind = "live"
module = "cheapy.providers.traveloka.provider"
capabilities = ["exact_one_way", "exact_round_trip"]
```

`provider.py` keeps the async `FlightProvider` protocol methods and owns the
45-second configured provider timeout. It passes that timeout into the default
Traveloka adapter, calls the adapter from a worker thread or async bridge, maps
adapter capture results and errors into `ProviderResult`, and preserves the
existing provider status behavior. The provider must not wrap the browser thread
with an equal-duration `asyncio.wait_for`, because that can preempt adapter
cleanup and drop a partial-offer `TravelokaCaptureResult` at the timeout edge.

- `SUCCESS` when offers normalize with no item errors
- `PARTIAL` when at least one offer normalizes and either some items fail or
  the 45-second timeout happens before search completion
- `FAILED` when the browser flow, capture, block, timeout, or provider-wide
  response shape fails before usable offers exist

`adapter.py` becomes a browser adapter, not an HTTP adapter. It owns:

- constructing the Traveloka full-search URL
- launching CloakBrowser
- creating a fresh context/page per provider call
- installing response listeners before navigation
- navigating to Traveloka's flight results page
- capturing supported fare API JSON from `search/initial` and `search/poll`
- closing browser resources in `finally`
- raising structured `TravelokaProviderError` values for provider-wide failures

The adapter must return a concrete capture result instead of only returning a
raw payload or raising an exception. The provider needs that contract so it can
return partial offers and still attach a timeout error when search completion was
not observed.

Required provider-facing result shape:

```python
@dataclass(frozen=True)
class TravelokaCaptureResult:
    payload: dict[str, object]
    source_path: str
    search_completed: bool
    timed_out: bool = False
```

Rules:

- `payload` is the best supported fare payload captured from `search/initial` or
  `search/poll`.
- `source_path` is only the safe API path, for example
  `/api/v2/flight/search/initial`; it is not a full URL with query, headers, or
  cookies.
- `search_completed` is true only when the supported payload has
  `data.meta.searchCompleted == true`.
- `timed_out` is true when a usable payload was captured but the 45-second
  provider timeout happened before `search_completed` became true.
- If timeout happens before any usable payload exists, the adapter raises
  `TravelokaProviderError(failure_type="timeout")` instead of returning a
  capture result.
- If a final block, unsupported response, browser failure, or provider-wide
  parse failure happens before any usable payload exists, the adapter raises the
  matching `TravelokaProviderError`.

`normalizer.py` owns the `data.searchResults[]` response shape and maps it to
`FlightOfferV1`. The current generic itinerary parser can remain temporarily
only if tests still require it, but the real Traveloka path must normalize the
discovered search-result schema.

## Runtime Flow

For exact one-way:

1. Build Traveloka request data with one journey:
   `originCode`, `destinationCode`, `departureDate`.
2. Build the Traveloka result route URL for the same search.
3. Launch CloakBrowser with a fresh ephemeral context.
4. Register response listeners for:
   - `/api/v2/flight/search/initial`
   - `/api/v2/flight/search/poll`
5. Navigate to the Traveloka result route.
6. Wait until one of these occurs:
   - `initial` or `poll` yields usable `data.searchResults[]`
   - `data.meta.searchCompleted == true` is observed, with or without offers
   - a terminal block/challenge/error response is detected
   - the 45-second provider timeout expires
7. Return a `TravelokaCaptureResult` to the provider.
8. Close page, context, and browser.

For exact round-trip:

1. Build Traveloka request data with two journeys:
   - outbound: origin to destination on departure date
   - return: destination to origin on return date
2. Use the same browser capture flow.
3. Normalize combined round-trip results only. Do not synthesize a round trip by
   pairing independent one-way offers.

## URL And API Targets

Navigation target:

```text
https://www.traveloka.com/en-en/flight/fulltwosearch
```

Expected query shape:

```text
ap=CXR.HAN
dt=20-5-2026.25-5-2026
ps=1.0.0
sc=ECONOMY
funnelSource=SEO-Homepage-SearchForm
```

One-way uses one date in `dt`. Round-trip uses outbound and return dates joined
by a period.

Supported fare API paths:

```text
/api/v2/flight/search/initial
/api/v2/flight/search/poll
```

The adapter must ignore analytics, logging, tracking, coupon, profile,
autocomplete, and non-flight-result endpoints.

The adapter may capture response JSON from the browser network layer. It should
not manually replay arbitrary browser requests unless that replay happens inside
the same browser context and targets only the supported fare API paths.

## Request Budget Reframing

The old HTTP-only budget of two total outbound HTTP requests no longer applies
to Traveloka browser runtime. A browser page load necessarily fetches HTML, JS,
CSS, API bootstraps, and other first-party assets.

The new budget is provider-business-level:

- consume at most one `search/initial` fare response
- consume at most one `search/poll` fare response
- do not retry navigation
- do not run provider-internal fanout
- do not query unrelated endpoints

This keeps Traveloka request volume bounded without pretending a browser page
load can be represented as two raw HTTP requests.

## Timeout And Partial Results

The provider timeout is 45 seconds.

If at least one supported fare response has produced normalizable offers before
timeout, the adapter returns `TravelokaCaptureResult(timed_out=True,
search_completed=False, payload=...)`. The provider normalizes that payload,
returns the offers, sets provider status to `PARTIAL`, and includes a safe
timeout error.

If the search completed and some items fail to normalize, provider status is
also `PARTIAL`. If the search completed and all captured items normalize,
provider status is `SUCCESS`.

If timeout happens before any usable offer payload is captured, the provider
returns `FAILED` with:

```text
code = PROVIDER_TIMEOUT
failure_type = "timeout"
retryable = true
```

If Traveloka returns an explicit completed empty result set, the provider returns
`SUCCESS` with no offers. This requires both:

- `data.meta.searchCompleted == true`
- `data.searchResults` exists and is an empty list

Transient interstitial or challenge markers during page load must not be treated
as terminal by themselves. The adapter may passively wait for Traveloka's own web
app to continue, but it must not click or solve a captcha, call a captcha-solving
service, use proxy rotation, or inject persisted cookies. A blocked/challenge
state is terminal only when no supported fare payload is available and Traveloka
returns an explicit final blocked response/page, or the page remains blocked
until timeout.

## Error Handling

Adapter-level errors must be structured and safe:

| Condition | Error code | failure_type | Retryable |
| --- | --- | --- | --- |
| provider timeout before usable offers | `PROVIDER_TIMEOUT` | `timeout` | true |
| browser launch/import/download failure | `PROVIDER_FAILED` | `browser_unavailable` | true |
| navigation timeout/error | `PROVIDER_TIMEOUT` or `PROVIDER_FAILED` | `timeout` or `navigation_failed` | true |
| HTTP 401 or 403 on fare/search flow | `PROVIDER_BLOCKED` | `blocked` | false |
| final captcha, WAF, access challenge, interstitial | `PROVIDER_BLOCKED` | `blocked` | false |
| HTTP 429 | `PROVIDER_RATE_LIMITED` | `rate_limited` | true |
| invalid JSON from fare endpoint | `PROVIDER_FAILED` | `invalid_json` | false |
| JSON without supported fare envelope | `PROVIDER_FAILED` | `unsupported_response` | false |
| no supported fare endpoint observed | `PROVIDER_FAILED` | `unsupported_response` | false |
| provider-wide parse failure | `PROVIDER_FAILED` | `parse_error` | false |
| unexpected exception | `PROVIDER_FAILED` | `unexpected_error` | false |

Error details may include:

- provider
- capability
- failure type
- safe HTTP status code
- exception type
- high-level URL path such as `/api/v2/flight/search/initial`

Error details must not include:

- raw response bodies
- cookies
- request or response headers
- challenge IDs
- full browser storage state
- Traveloka session artifacts

## Normalization

The real Traveloka search-result shape to support is:

```text
data.searchResults[]
```

Important fields from discovery:

- result id: `id`
- price: `fare.display.currencyValue.amount`
- currency: `fare.display.currencyValue.currency`
- decimal places: `fare.display.numOfDecimalPoint`
- metadata price fallback: `flightMetadata.totalCombinedPrice`
- stop count: `flightMetadata.totalNumStop`
- total duration: `flightMetadata.tripDuration`
- route groups: `connectingFlightRoutes[]`
- segments: `connectingFlightRoutes[].segments[]`
- segment origin: `departureAirport`
- segment destination: `arrivalAirport`
- airline: `airlineCode`
- flight number: `flightNumber`
- segment duration: `durationMinutes`
- dates: `departureDate` and `arrivalDate`
- times: `departureTime` and `arrivalTime`

Price amounts are minor units. For example, amount `29890` with
`numOfDecimalPoint` `2` means `298.90`.

Every normalized offer must keep Contract V1 behavior:

- stable `offer_id` beginning with `traveloka:`
- `provider = "traveloka"`
- exact route validation
- exact departure and return date validation
- requested and actual airport/date fields
- date offsets and flexible-date flags
- segment legs with airline, flight number, airport IATA values, times, and
  durations
- total duration and stops
- `fare_details_status = "not_collected"`

## Testing

Default tests must stay offline.

Required coverage:

- URL builder for one-way and round-trip full-search routes
- CloakBrowser launch is patched or injected in tests; no real browser starts
- response listener accepts only `search/initial` and `search/poll`
- analytics/logging/profile/autocomplete endpoints are ignored
- adapter returns `TravelokaCaptureResult` with `search_completed` and
  `timed_out` state for usable fare payloads
- adapter consumes at most one `initial` and one `poll` fare response
- timeout before usable offers maps to structured timeout failure
- usable partial offers before timeout are returned as `PARTIAL` with a safe
  timeout error
- empty `data.searchResults` is success only when
  `data.meta.searchCompleted == true`
- final block/challenge/interstitial states map to `blocked`
- browser import/launch failure maps to `browser_unavailable`
- invalid JSON and unsupported JSON are classified safely
- redacted `data.searchResults[]` fixture normalizes into `FlightOfferV1`
- explicit no-results fixture returns success with empty offers
- provider maps success, partial, failed, and timeout outcomes correctly
- core search preserves other provider offers when Traveloka fails
- `uv run pytest -v` passes without live network access

Opt-in live smoke may launch a real browser only behind an explicit live-test
gate such as `CHEAPY_RUN_LIVE_TESTS=1`. The live smoke route remains:

```text
CXR -> HAN
departure: 2026-05-20
return: 2026-05-25
passengers: 1 adult
currency: USD
```

Live smoke must accept structured `blocked`, `timeout`,
`browser_unavailable`, or `unsupported_response` failures. It must not crash the
CLI or default test suite.

## Success Criteria

- Cheapy Traveloka runtime launches CloakBrowser by default.
- Traveloka no longer uses the HTTP-only landing-page adapter.
- Traveloka captures and normalizes `search/initial` or `search/poll`
  `data.searchResults[]` fare data.
- The CXR-HAN smoke route can return Traveloka offers with prices when
  Traveloka allows the browser session.
- Provider timeout is 45 seconds.
- Partial usable offers captured before timeout are returned.
- No login, captcha-solving service, proxy rotation, persisted browser profile,
  retry loop, or provider-internal fanout is introduced.
- Default test suite remains offline and passes.

## Non-Goals

- No official Traveloka partner API implementation.
- No HTTP-only Traveloka replay fallback.
- No user-facing provider selector.
- No Contract V1 schema changes.
- No persisted session/cookie cache.
- No Traveloka account login.
- No booking checkout or fare-detail purchase flow.
- No price history, storage, scheduler, or alerts.
- No currency conversion.

## Implementation Boundaries

Expected files:

- `pyproject.toml`
- `cheapy/providers/traveloka/adapter.py`
- `cheapy/providers/traveloka/provider.py`
- `cheapy/providers/traveloka/normalizer.py`
- `tests/test_traveloka_adapter.py`
- `tests/test_traveloka_provider.py`
- `tests/test_traveloka_normalizer.py`
- optional live smoke test or CLI provider-test documentation

Core search and Contract V1 should not change unless a Traveloka integration
test exposes an independent bug in orchestration or provider result handling.

User-owned unrelated doc deletions in the worktree must not be reverted or
included in commits for this work.
