# Cheapy Skyscanner Live Provider Design

Date: 2026-05-28

## Summary

Promote the researched Skyscanner HTTP flow into a normal Cheapy live provider.

The provider will be enabled for normal exact searches, use the existing
plain HTTP Skyscanner radar flow, and return clean Contract V1 offers. It will
not expose Skyscanner transport deeplinks, cookies, request headers, raw payloads,
browser/session data, or challenge URLs. User-clickable Skyscanner links will be
generated only through the existing safe `public_search_url` presentation layer.

## Approved Decisions

1. Implement Option A: a packaged live provider under
   `cheapy.providers.skyscanner`.
2. Add a Skyscanner provider manifest with `provider_kind = "live"` and
   `default_enabled = true`.
3. Keep default tests offline with fake clients/adapters.
4. Keep `scripts/skyscanner_http_probe.py` as a research/diagnostic CLI wrapper,
   not as the runtime provider module.
5. Read `CHEAPY_SKYSCANNER_COOKIE` at runtime only.
6. If the cookie is missing, return a sanitized provider failure instead of
   raising or logging the environment state.
7. Never put Skyscanner `/transport_deeplink/` URLs into Contract V1 offers,
   MCP structured output, Markdown reports, history observations, or storage.
8. Generate user-clickable links only via safe public Skyscanner search URLs.
9. Do not reintroduce Browserless.

## Goals

- Include Skyscanner in normal provider discovery and exact search execution.
- Support exact one-way and exact round-trip searches.
- Resolve Skyscanner entity IDs through Autosuggest.
- Execute the existing `web-unified-search` flow without browser automation.
- Normalize usable itineraries into complete `FlightOfferV1` objects.
- Attach safe public Skyscanner search links through the existing
  `attach_public_search_urls()` path.
- Surface provider status and failures in the existing Contract V1 shape.
- Keep MCP stdout protocol-clean.

## Non-Goals

- No Browserless, browser automation, cookie bootstrap, login, captcha solving,
  proxy rotation, or anti-bot workaround.
- No storage of cookies, headers, request bodies, request IDs, challenge URLs,
  raw responses, raw provider payloads, or browser/session data.
- No exposure of Skyscanner transport deeplinks to users or machine consumers.
- No changes to Contract V1 fields unless tests prove they are required.
- No live provider calls in default tests.
- No Skyscanner GraphQL scanner exposure through MCP.

## Architecture

Add provider runtime modules:

```text
cheapy/providers/skyscanner/
  manifest.toml
  provider.py
  adapter.py
  normalizer.py
```

The existing `scan_graphql_bundles.py` stays experimental and unregistered. Its
MCP non-exposure tests remain valid.

`adapter.py` owns the plain HTTP work:

- environment-to-config loading
- Autosuggest entity resolution
- search body construction
- radar search POST and polling
- sanitized probe errors
- extraction of candidate itinerary payloads needed by the normalizer

`normalizer.py` owns Contract V1 mapping:

- price, currency, route, dates, legs, stops, duration
- ranking within currency
- offer IDs
- parse warnings/errors for unusable itineraries

`provider.py` follows the existing Google Fli and Traveloka provider pattern:

- async provider methods
- `asyncio.to_thread()` around sync adapter calls
- timeout handling
- sanitized `ErrorV1` mapping
- `ProviderResult` construction

The provider must not import runtime code from `scripts/`. If shared code from
the probe is reused, it moves into package modules and the script imports or
wraps those package modules.

## Data Flow

1. `load_search_providers()` discovers `skyscanner/manifest.toml`.
2. The search planner schedules Skyscanner alongside other live providers.
3. `SkyscannerProvider` receives a provider-local exact request.
4. The adapter reads `CHEAPY_SKYSCANNER_COOKIE`.
5. Missing cookie returns a sanitized failed `ProviderResult`.
6. With a cookie, the adapter resolves origin and destination IATA to
   Skyscanner entities.
7. The adapter sends the radar search request and polls incomplete sessions.
8. The adapter returns only minimal parsed itinerary candidates, not raw payload.
9. The normalizer builds `FlightOfferV1` offers with complete legs and timing.
10. The provider returns offers without `public_search_url`.
11. The orchestrator ranks/deduplicates offers.
12. `attach_public_search_urls()` builds safe Skyscanner public search URLs from
    the Contract offer route/date/passenger fields.
13. Markdown rendering turns the fare into a clickable link only when the safe
    `public_search_url` is present.

## Contract And Link Safety

The provider must return Contract V1 offers with:

- `provider = "skyscanner"`
- complete `legs`
- non-negative `total_duration_minutes`
- non-negative `stops`
- requested and actual route/date fields
- `fare_details_status = "not_collected"`
- `public_search_url = None`

The provider must ignore or discard Skyscanner transport deeplinks after they
serve only as a local signal that an itinerary has a usable pricing option.

The only clickable user link is built later by:

```python
build_public_search_url("skyscanner", request, offer)
```

That URL must pass `validate_public_search_url("skyscanner", url)`.

## Error Handling

Errors are sanitized before reaching Contract V1:

- missing cookie: `provider_failed`, `failure_type = "missing_cookie"`,
  retryable false
- HTTP 401 or 403: `provider_blocked`, retryable false
- HTTP 429: `provider_rate_limited`, retryable true
- timeout or incomplete polling: `provider_timeout`, retryable true
- parse or no usable itinerary: `provider_failed`, retryable false
- unexpected provider exception: `provider_failed`, retryable false

Error details may include:

- provider
- capability
- failure_type
- exception_type
- HTTP status code when safe

Error details must not include:

- cookies
- headers
- request bodies
- raw URLs from radar or transport deeplink endpoints
- raw payload fragments
- session IDs
- challenge URLs

## Tests

Add or update tests for:

- package data includes Skyscanner `manifest.toml`.
- provider discovery includes Skyscanner as a default live search provider.
- CLI provider listing includes Skyscanner.
- MCP does not expose Skyscanner scanner/debug tools.
- missing cookie returns a sanitized provider failure.
- adapter/probe errors map to the right `ErrorCode`.
- successful fake adapter results normalize into valid `FlightOfferV1`.
- Skyscanner offers receive safe public search URLs through
  `attach_public_search_urls()`.
- provider output never contains `/transport_deeplink/`, cookies, headers,
  raw payload fields, or session IDs.
- one-way and round-trip fake payloads produce complete legs, stops, duration,
  and date fields.
- existing Contract V1, Markdown report, CLI, MCP, storage, and public URL tests
  continue to pass.

Relevant commands:

```sh
uv run pytest tests/skyscanner/test_http_probe.py -v
uv run pytest tests/test_providers.py tests/test_package_data.py tests/test_cli.py tests/test_mcp.py -v
uv run pytest tests/test_public_links.py tests/test_markdown_report.py -v
uv run pytest -v
```

## Rollout Notes

Skyscanner will become default-enabled. In environments without
`CHEAPY_SKYSCANNER_COOKIE`, searches will still run other providers and include
a clean Skyscanner provider failure/status. That is acceptable because the
failure is machine-readable, does not break Contract V1, and makes the provider
state visible instead of silently hiding Skyscanner.

Live matrix checks remain manual and opt-in. They may use real provider calls,
but default tests must not.
