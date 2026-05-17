# Cheapy Traveloka Provider Design

Date: 2026-05-14

## Summary

Add a new bundled live provider named `traveloka`.

This provider is a research scraping prototype, not an official Traveloka API
integration. It uses HTTP-only access to public Traveloka flight search
surfaces, normalizes parsed results into Contract V1 offers, and is enabled in
normal user-facing search by default.

The provider supports the same initial trip shapes as `google_fli`:

- exact one-way
- exact round-trip

Cheapy's public MCP tool and Contract V1 request/response shapes do not change.
Agents still call `search_cheapest_flights`; they do not choose providers.

## Approved Decisions

1. Provider name is `traveloka`.
2. Provider kind is `live`.
3. Provider is enabled by default in normal search.
4. Provider supports `exact_one_way` and `exact_round_trip`.
5. Provider uses HTTP-only research access, not Playwright or another browser.
6. Default market/locale/currency target is English-facing/global with USD.
7. Traveloka is allowed to fail independently without failing the whole search
   when another provider returns usable offers.
8. No login, credential use, captcha solving, proxy rotation, aggressive retry,
   storage, or cache is added in this milestone.
9. The default-enabled behavior relies on the project owner's stated Traveloka
   support approval for this use. Deployments outside that permission must
   disable the provider before use.

## Context And Risk

Traveloka exposes official B2B integration paths through Traveloka Partners
Network and related partner/API programs. The requested implementation path is
not that official path.

Traveloka terms reviewed during design restrict unapproved commercial use and
unapproved manual or automated access, monitoring, copying, scraping, and
similar activity against site material. The project owner stated on
2026-05-14 that Traveloka support approved this use, provided Cheapy does not
send excessive traffic. This design therefore keeps the provider enabled by
default for this codebase under that permission assumption, while treating the
integration as fragile and conservative:

- no authentication bypass
- no account or cookie persistence
- no captcha or anti-bot bypass
- no proxy rotation
- no high-volume retry loop
- no raw page or endpoint payloads in user-visible errors

If Traveloka blocks the request, shows a challenge, changes response shape, or
returns ambiguous currency data, the provider fails closed with a structured
provider error.

The repository must not commit private support correspondence. Public docs
should state the permission assumption and tell deployments without Traveloka
permission to disable the provider before running user-facing search.

Sources checked:

- Traveloka Terms and Conditions: https://www.traveloka.com/en-sg/termsandconditions
- Traveloka Partners Network: https://www.travelokapartnersnetwork.com/
- Traveloka Atlas API: https://atlas.traveloka.com/

## Goals

- Add a packaged `traveloka` provider discovered by the existing provider
  registry.
- Include `traveloka` in default user-facing search.
- Support exact one-way and exact round-trip search.
- Keep Traveloka-specific request building and parsing inside the provider
  package.
- Normalize Traveloka results into `FlightOfferV1` with provider attribution.
- Preserve the existing global merge, sort, rank, mixed-currency, and
  `max_results` behavior.
- Keep default tests deterministic and offline.
- Provide opt-in live smoke coverage that never crashes on block or parse
  failure.
- Keep Traveloka request volume bounded: no provider-internal fanout, no
  automatic retry, and no extra calls beyond the existing search-planner
  selected provider calls.

## Non-Goals

- No official Traveloka partner/API integration.
- No provider selection field in `SearchRequestV1`.
- No new MCP tool.
- No native flexible-date Traveloka capability.
- No nearby-airport expansion.
- No split-ticket search.
- No browser automation in normal search.
- No login or authenticated Traveloka session.
- No scraping workaround for captcha, bot challenge, or access denial.
- No storage, price history, cache, scheduler, or alerts.
- No currency conversion.
- No booking flow or fare-detail checkout collection.

## Architecture

Add:

```text
cheapy/providers/traveloka/
  __init__.py
  manifest.toml
  provider.py
  adapter.py
  normalizer.py
```

`manifest.toml`:

```toml
manifest_schema_version = "1"
name = "traveloka"
display_name = "Traveloka research provider"
default_enabled = true
provider_kind = "live"
module = "cheapy.providers.traveloka.provider"
capabilities = ["exact_one_way", "exact_round_trip"]
```

`provider.py` implements the existing `FlightProvider` protocol. It owns the
timeout boundary, maps provider-local exceptions into `ProviderResult`, and
sets provider status per capability.

`adapter.py` owns HTTP request construction and response fetching. It should be
sync, like the current `google_fli` adapter, so `provider.py` can run it through
`asyncio.to_thread()` with a bounded timeout. The adapter should avoid a new
runtime dependency unless implementation research proves one is necessary; a
stdlib HTTP client is acceptable for the first implementation if it can set
headers, timeout, method, body, and response-size limits cleanly.

`normalizer.py` owns all Traveloka response-shape knowledge. It converts a
successful response payload into `FlightOfferV1` objects and returns structured
parse/currency errors for skipped items. Core Cheapy must not import or inspect
Traveloka payload internals.

Core modules remain unchanged in shape:

- `cheapy/search.py` continues to load enabled search providers, call selected
  planned provider calls, merge offers, rank, and build `SearchResponseV1`.
- `cheapy/search_planner.py` continues to expand flexible dates by issuing
  multiple exact provider calls.
- Contract V1 models remain the source of truth.

## Data Flow

For a user-facing search:

1. MCP validates input with `SearchRequestV1`.
2. Core resolves origin and destination to IATA.
3. Planner creates exact or expanded candidates.
4. Registry loads enabled live search providers, including `google_fli` and
   `traveloka`.
5. For each selected candidate, core calls providers that advertise the
   candidate capability.
6. Traveloka provider builds a provider-local one-way or round-trip request.
7. Adapter sends a bounded HTTP request to the researched Traveloka public
   search surface.
8. Adapter classifies transport, block, rate-limit, and response-shape
   conditions.
9. Normalizer parses stable JSON or embedded data first. HTML parsing is allowed
   only when enough stable markers exist to keep parsing deterministic.
10. Provider returns `ProviderResult`.
11. Core merges offers from all providers, deduplicates, sorts, ranks, and
    truncates to `max_results`.

## Traveloka Query Defaults

The provider targets:

- English-facing/global market where the researched endpoint supports it
- `USD` currency
- economy cabin
- passenger counts from Contract V1
- exact origin and destination IATA codes
- exact departure date
- exact return date when present

The provider must not silently invent USD. It may emit USD only when the
Traveloka request or response gives reliable evidence that the returned prices
are USD. If currency cannot be verified, the provider returns
`currency_unavailable`.

## Offer Normalization

Every normalized offer must set:

- `provider = "traveloka"`
- stable `offer_id` beginning with `traveloka:`
- price amount and currency
- requested and actual origin/destination
- requested and actual departure date
- requested and actual return date for round trips
- date offsets and flexible-date flags
- legs with airline, flight number, airport IATA values, times, and duration
- total duration, stops, and `fare_details_status = "not_collected"`

For exact requests, actual date fields normally match provider query dates. In
expanded mode, the planner passes exact candidate dates to the provider while
preserving requested dates, so the normalizer can fill offset fields using the
existing provider-local request fields.

Round-trip results must come from one Traveloka round-trip query. Cheapy does
not synthesize a round-trip fare by pairing independent one-way results.

## Error Handling

Traveloka provider failures must be structured and local to the provider call.

Expected mappings:

- timeout: `PROVIDER_TIMEOUT`, `failure_type = "timeout"`, retryable
- HTTP/network transport error: `PROVIDER_FAILED`,
  `failure_type = "transport_error"`, retryable
- 429/rate limit: `PROVIDER_RATE_LIMITED`,
  `failure_type = "rate_limited"`, retryable
- 403, captcha, bot challenge, login wall, or access denied:
  `PROVIDER_BLOCKED`, `failure_type = "blocked"`, not retryable
- unsupported airport or route by Traveloka:
  `PROVIDER_FAILED`, `failure_type = "unsupported_route_by_upstream"`, not
  retryable
- missing trustworthy currency:
  `PROVIDER_FAILED`, `failure_type = "currency_unavailable"`, not retryable
- whole-response parse failure:
  `PROVIDER_FAILED`, `failure_type = "parse_error"`, not retryable
- partial item parse failure:
  `ProviderStatusCode.PARTIAL`, parsed offers preserved, skipped count recorded
  in safe error details
- successful response with no flights:
  `ProviderStatusCode.SUCCESS` with `offers = []`

Provider errors must not include:

- raw HTML
- raw JSON payloads
- cookies
- request or response headers
- full URLs with query strings when they might expose sensitive parameters
- environment variables
- full tracebacks
- long upstream exception messages

Safe details may include:

- provider name
- capability
- failure type
- HTTP status code
- parser stage
- skipped item count
- exception type

## Runtime Constraints

Default runtime behavior:

- bounded provider timeout, in the same range as `google_fli` unless tests show
  Traveloka requires a lower value
- no automatic retry in normal MCP search
- no cookie jar persistence
- no credential or login flow
- no browser execution
- no cache or storage
- response-size limit to avoid accidentally processing large pages
- diagnostics and errors stay on stderr or structured response fields; MCP
  stdout remains protocol-clean
- Traveloka provider timeout is 20 seconds per provider call. A timeout returns
  `ProviderStatusCode.FAILED` with `PROVIDER_TIMEOUT` and does not retry.
- Traveloka must not perform provider-internal fanout. One selected Cheapy
  provider call maps to at most one Traveloka HTTP request.
- Expanded search may call Traveloka multiple times through existing Cheapy
  candidate expansion, but only under the current global selected provider-call
  budget. The provider must not add another Traveloka-specific expansion layer.

## Testing

Default tests remain offline and deterministic.

Add or update tests for:

- package-data inclusion of `traveloka/manifest.toml` and any fixture payloads
- manifest discovery for `traveloka`
- registry search provider loading includes `traveloka` and excludes fixtures
- provider shape and capabilities
- adapter request construction for one-way and round-trip
- adapter classification of block, rate-limit, timeout, and transport failures
- normalizer conversion from fixture Traveloka payloads into `FlightOfferV1`
- normalizer parse failures and currency-unavailable failures
- provider result status for success, partial, failed, and empty results
- search orchestration merging Traveloka offers with another provider's offers
- default pytest run making no live Traveloka network calls

Opt-in live smoke tests may call Traveloka only when explicitly enabled, for
example with `CHEAPY_RUN_LIVE_TESTS=1`. A live smoke failure is acceptable when
it returns a structured provider failure or block result; it is not acceptable
for the provider to crash the test process or leak raw payloads.

## Documentation Updates

Update README and agent guidance enough to explain:

- `traveloka` exists as a live research provider
- it may be fragile or blocked
- this codebase assumes Traveloka support approval for default-enabled research
  access; deployments without permission must disable it
- users and agents should not choose providers manually
- each offer's `provider` field identifies the fare source
- mixed provider results and provider failures are normal
- default tests remain offline

Do not document bypass techniques, endpoint internals, or any advice for
evading Traveloka access controls.

## Acceptance Criteria

- `uv run pytest -v` passes without live network access.
- `uv run cheapy providers list` includes `traveloka`.
- Traveloka is default enabled under the documented permission assumption.
- `search_cheapest_flights` can return offers from Traveloka when parsing
  succeeds.
- If Traveloka is blocked or fails parsing, the response includes a structured
  provider status and search can still return offers from other providers.
- If a Traveloka HTTP call exceeds 20 seconds, it returns a structured timeout
  provider status and Cheapy continues assembling the response.
- Exact one-way and exact round-trip requests are supported.
- Expanded search uses existing exact-call candidate expansion and does not add
  a Traveloka-native flexible-date capability.
- No raw Traveloka payload, cookie, header, or full traceback appears in
  Contract V1 errors.
