# Cheapy Traveloka Client API Replayer Design

Date: 2026-05-14

## Goal

Replace the current Traveloka page-shell fetch with a real Traveloka client API
HTTP replayer. Runtime search must remain HTTP-only, but discovery may use a
browser/devtools capture to identify the API endpoint, request shape, required
headers, and response schema.

The provider must return normalized Traveloka offers when the client API is
reachable, and structured provider failures when Traveloka blocks, rate limits,
times out, returns captcha/interstitial HTML, or returns an unsupported response
shape.

## Current Evidence

The existing adapter calls:

```text
https://www.traveloka.com/en-en/flight?trip=roundtrip&origin=CXR&destination=HAN&departureDate=2026-05-20&returnDate=2026-05-25...
```

Live diagnostics for CXR to HAN, outbound 2026-05-20 and return 2026-05-25,
showed:

- `200 text/html; charset=utf-8`
- page title: `Cheap Flights, Air Ticket Prices & Airline Deals | Traveloka`
- `__NEXT_DATA__` existed, but only described the `/flight` page and echoed the
  query parameters
- no reliable result paths such as `flightSearchResult`, `itineraries`, or
  offer-like `price + segments/legs` objects were present
- the current normalizer therefore saw no itinerary items and returned
  `offers=[]`, `errors=[]`
- the current provider then reported `status="success"` because an empty
  unsupported HTML shell was indistinguishable from a real empty-result payload

Browser capture also showed Traveloka UI links use a different full-search URL
shape, for example:

```text
/en-en/flight/fullsearch?ap=CGK.DPS&dt=9-06-2026&ps=1.0.0&sc=ECONOMY...
```

A direct CXR.HAN fullsearch navigation returned `403` and a captcha interstitial.
This means simply changing the adapter to fetch `/flight/fullsearch` is not a
safe real fix.

## Decisions

1. Runtime Cheapy must remain HTTP-only.
2. Browser automation is allowed only during endpoint discovery, not in runtime,
   package dependencies, or default tests.
3. A single Traveloka provider call may perform at most two HTTP requests:
   - optional bootstrap request to obtain non-secret session metadata required by
     the client API
   - one client API search request
4. No retry loop is allowed.
5. No provider-internal fanout is allowed.
6. No login, captcha solving, proxy rotation, or WAF bypass is allowed.
7. Runtime must not call analytics, logging, tracking, or unrelated endpoints.
8. Raw Traveloka responses, cookies, tokens, and full headers must not be logged
   or committed. Fixtures must be minimized and redacted.
9. Implementation is two-phase. Phase 1 discovers and documents the client API
   contract. Phase 2 implements the HTTP replayer against that discovered
   contract. Runtime adapter work must not begin until Phase 1 has either
   produced a replayable contract or documented a hard blocker.

## Architecture

### Discovery Artifacts

Endpoint discovery is a research step, not runtime code. It may use browser
network capture to identify:

- API URL and HTTP method
- required query parameters or JSON body fields
- required non-secret headers
- whether a bootstrap page request is required
- response content type and top-level JSON keys
- path to itinerary, price, segment, and airline fields
- explicit no-results representation
- block/rate-limit/captcha response patterns

The implementation plan must commit only small redacted fixtures or schema
snapshots needed for offline tests.

Discovery must produce a committed artifact before runtime adapter changes. That
artifact may be a new section in this spec or a separate focused document under
`docs/superpowers/specs/`. It must include:

- canonical API URL and method
- whether bootstrap is required
- exact request body or query schema for one-way and round-trip
- required first-party headers and which can be static
- response envelope keys and the path to offer/result collections
- explicit no-results shape
- block, captcha, rate-limit, and unsupported-response indicators
- a redacted sample payload or reduced fixture schema sufficient for offline
  tests

If discovery cannot find a replayable endpoint without captcha solving, login,
proxying, or more than two HTTP requests, Phase 2 must not implement a fake
successful provider. The implementation must instead stop after adding fail-fast
structured failure handling for unsupported Traveloka page/fullsearch responses.

### Bootstrap Artifacts

Runtime may use only first-party, ephemeral bootstrap artifacts returned by
Traveloka during the same provider call. Allowed in memory:

- cookies set by `www.traveloka.com` response headers
- CSRF, device, viewer, or session headers extracted from first-party Traveloka
  HTML, JSON, or response headers
- static non-secret headers discovered from Traveloka's web client

Disallowed:

- user credentials or account-backed tokens
- manually supplied cookies or tokens
- third-party captcha/WAF tokens, including captcha-delivery artifacts
- persisted cookies, token caches, or state files
- human captcha interaction

If the client API requires any disallowed artifact, the provider must fail
closed with `failure_type="bootstrap_unavailable"` or `failure_type="blocked"`.

### Runtime Adapter

`TravelokaAdapter` must become an HTTP replayer around the discovered client
API. It remains synchronous and injectable like the current adapter, so provider
tests can use fake HTTP calls.

The adapter must expose the same provider-facing methods:

- `search_exact_one_way(request)`
- `search_exact_round_trip(request)`

Internally it must:

1. build the Traveloka API request from the provider-local request
2. optionally call a bootstrap URL when discovery proves it is required
3. call the client API search endpoint
4. classify transport, HTTP, captcha, and unsupported response failures
5. return parsed JSON payloads only when they are suitable for normalization

The adapter must count every outbound HTTP request and fail closed if a code
path attempts a third request.

Redirects count against the two-request budget. Runtime HTTP must either disable
automatic redirects or use a redirect handler that increments the same request
counter before following. Redirects are allowed only to HTTPS URLs on
`www.traveloka.com`; redirects to captcha, tracking, non-Traveloka hosts, or a
third request must fail closed as `failure_type="blocked"` or
`failure_type="request_budget_exceeded"`.

### Normalizer

`TravelokaNormalizer` must normalize the discovered API response shape rather
than generic HTML app shells.

It must keep existing Contract V1 responsibilities:

- stable `offer_id` beginning with `traveloka:`
- provider field set to `traveloka`
- exact route validation
- exact departure and return date validation
- price, currency, segment, airline, duration, and stop mapping
- item-level parse errors without leaking raw payload values
- partial results when some items normalize and others fail

It must distinguish explicit no-results API payloads from result payloads that
contain offers. It must not receive HTML app shells or unsupported provider-wide
API envelopes; the adapter owns those failures before normalization.

### Provider Wrapper

`TravelokaProvider` must keep the existing async wrapper and timeout boundary.
It must map adapter and normalization outcomes into `ProviderResult`:

- `SUCCESS` with offers when API results normalize
- `SUCCESS` with no offers only for explicit no-results responses
- `PARTIAL` when at least one offer succeeds and at least one item-level error
  occurs
- `FAILED` when bootstrap, API, block, timeout, unsupported response, or
  provider-wide parse failures occur

## Data Flow

1. Cheapy search planner selects Traveloka for exact one-way or exact round-trip.
2. Provider request is converted into Traveloka API parameters:
   - airport pair, for example `CXR.HAN`
   - outbound date and optional return date in the discovered API format
   - passengers, cabin, currency, and locale
3. Optional bootstrap request runs if required by the discovered API.
4. Client API search request runs.
5. Adapter parses JSON and validates that the response is a supported API
   envelope.
6. Normalizer produces Contract V1 offers and item-level errors from supported
   API payloads only.
7. Provider returns a structured result to core search.

Adapter owns provider-wide unsupported response detection. It must reject HTML
page shells, captcha/interstitial HTML, invalid JSON, and JSON without the
discovered supported API envelope before calling the normalizer. The normalizer
owns item-level parse errors after the adapter has accepted the envelope.

## Error Handling

Adapter-level failures must map to existing Contract V1 error codes and exact
safe `details.failure_type` values:

| Condition | Error code | failure_type | Retryable |
| --- | --- | --- | --- |
| socket/read/provider timeout | `PROVIDER_TIMEOUT` | `timeout` | true |
| HTTP 401 or 403 | `PROVIDER_BLOCKED` | `blocked` | false |
| captcha, WAF, access challenge, interstitial HTML | `PROVIDER_BLOCKED` | `blocked` | false |
| HTTP 429 | `PROVIDER_RATE_LIMITED` | `rate_limited` | true |
| HTTP 408 | `PROVIDER_TIMEOUT` | `timeout` | true |
| HTTP 400, 404, 409, 422 | `PROVIDER_FAILED` | `bad_request` | false |
| HTTP 5xx | `PROVIDER_FAILED` | `transport_error` | true |
| response exceeds size limit | `PROVIDER_FAILED` | `response_too_large` | false |
| bootstrap transport failure | `PROVIDER_FAILED` | `bootstrap_unavailable` | true |
| bootstrap required artifact missing | `PROVIDER_FAILED` | `bootstrap_unavailable` | false |
| third outbound request would be required | `PROVIDER_FAILED` | `request_budget_exceeded` | false |
| invalid JSON from API endpoint | `PROVIDER_FAILED` | `invalid_json` | false |
| valid JSON without supported API envelope | `PROVIDER_FAILED` | `unsupported_response` | false |
| provider-wide normalization failure | `PROVIDER_FAILED` | `parse_error` | false |

Errors must include safe details such as provider, capability, failure type,
HTTP status code, and exception type. They must not include raw response bodies,
cookies, tokens, or request headers.

## Testing

Default tests must remain offline and must not call Traveloka.

Required focused coverage:

- API request builder for one-way and round-trip
- bootstrap-required and bootstrap-not-required paths
- maximum two outbound HTTP requests per provider call
- redirects count against the request budget and cannot leave the
  `www.traveloka.com` HTTPS allowlist
- no retry behavior
- HTTP 403, 429, 5xx, timeout, invalid JSON, captcha HTML, and unsupported JSON
  response classification
- HTML app shell without result data becomes structured `unsupported_response`
  failure
- redacted API fixture normalizes into `FlightOfferV1`
- explicit no-results fixture returns success with empty offers
- provider maps success, partial, and failed outcomes correctly
- search-orchestration test coverage that other provider offers are preserved
  when Traveloka fails
- `uv run pytest -v` passes without live network access

Opt-in live smoke must exist through `cheapy providers test --live` behind
`CHEAPY_RUN_LIVE_TESTS=1`. A dedicated Traveloka pytest live test may also be
added, but is not required. Live smoke is allowed to return blocked, timed out,
or unsupported response as a structured provider result; it must not crash the
CLI.

## Success Criteria

- Runtime Traveloka search uses the discovered client API endpoint, not the
  `/flight` landing page shell.
- Runtime uses HTTP-only stdlib code and adds no browser dependency.
- A provider call performs no more than two HTTP requests.
- Unsupported HTML/page-shell responses no longer report `SUCCESS` empty.
- Redacted offline fixtures prove the normalizer can parse the discovered API
  payload shape.
- Full offline test suite passes.
- `CHEAPY_RUN_LIVE_TESTS=1 uv run cheapy providers test --live` includes
  Traveloka and produces a structured result without unhandled exceptions, even
  if Traveloka blocks or rate limits the request.

## Non-Goals

- No login or account-backed Traveloka search.
- No captcha solving.
- No proxy rotation or anti-WAF bypass.
- No scraping rendered DOM as the runtime data source.
- No storage or caching layer.
- No changes to Contract V1 response shape.
- No user-facing provider selection UI or provider selection prompts.

## Implementation Boundaries

The implementation must stay scoped to the Traveloka provider package, its
tests, and documentation or live-smoke guidance if behavior changes. Search
orchestration tests may be updated to prove Traveloka failure does not suppress
other provider offers. Core search implementation and Contract V1 models may
change only if a bug is found that is independent of Traveloka.

The ignored user-owned README and `.gitignore` history must not be reverted.
