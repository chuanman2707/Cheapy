# Cheapy Skyscanner HTTP Probe Design

Date: 2026-05-19

## Summary

Add a standalone Skyscanner HTTP research probe script.

The script proves that Cheapy can resolve Skyscanner entity IDs through
Autosuggest, replay the minimal `web-unified-search` POST without a browser,
and extract the cheapest fares plus deep links from the response.

This is not a Cheapy provider. It does not add a provider manifest, does not
touch the provider registry, does not affect MCP behavior, and does not change
normal user-facing search.

## Approved Decisions

1. Scope is a standalone research script, not an experimental provider.
2. Script path is `scripts/skyscanner_http_probe.py`.
3. The script reads Skyscanner session cookies from
   `CHEAPY_SKYSCANNER_COOKIE`.
4. The script does not bootstrap or refresh Skyscanner sessions.
5. The script does not hardcode cookies, JWTs, or session IDs.
6. The script uses browserless HTTP only.
7. Default tests stay offline and use fake HTTP responses.
8. The probe prints the top fares to the terminal for human inspection.

## Goals

- Provide a small executable proof that the researched Skyscanner HTTP flow is
  replayable without browser automation when a valid cookie is supplied.
- Resolve IATA airport codes to Skyscanner entity IDs through Autosuggest.
- Search one-way and round-trip exact-date fares through
  `web-unified-search`.
- Print the top results with airline, cheapest price, and deep link URL.
- Keep request headers minimal and explicit.
- Keep secrets out of code, committed fixtures, logs, test output, and errors.
- Keep the script isolated from the packaged provider registry.

## Non-Goals

- No Cheapy provider implementation.
- No `manifest.toml` for Skyscanner.
- No provider registry integration.
- No MCP or CLI search integration.
- No session bootstrap, cookie refresh, login, captcha solving, proxy rotation,
  or anti-bot workaround.
- No storage, caching, alerts, price history, or scheduler.
- No currency conversion.
- No booking-flow automation beyond printing Skyscanner deep links.
- No large raw Skyscanner response fixture committed to the repository.

## Architecture

Add:

```text
scripts/
  skyscanner_http_probe.py
```

The script contains two main functions:

```python
get_entity_id(iata_code: str) -> EntityResult
fetch_flights(
    origin_entity: str,
    dest_entity: str,
    departure_date: str,
    return_date: str | None = None,
) -> list[FlightProbeResult]
```

The script uses `httpx` for browserless HTTP. If `httpx` is not already
available through the current environment, the implementation plan should add
it as a development dependency or document it as a probe-only requirement. It
must not become an imported dependency of Cheapy runtime modules in this
milestone.

The script owns:

- argument parsing
- cookie loading from the environment
- Skyscanner Autosuggest calls
- minimal `web-unified-search` request construction
- response validation
- fare extraction and sorting
- terminal output

It does not export package APIs and is not imported by Cheapy runtime code.

## CLI Shape

Example:

```sh
CHEAPY_SKYSCANNER_COOKIE='...' uv run python scripts/skyscanner_http_probe.py \
  --origin HAN \
  --destination SGN \
  --departure-date 2026-06-11 \
  --return-date 2026-06-16
```

Arguments:

- `--origin`: required IATA code.
- `--destination`: required IATA code.
- `--departure-date`: required `YYYY-MM-DD`.
- `--return-date`: optional `YYYY-MM-DD`.
- `--market`: default `SG`.
- `--locale`: default `en-GB`.
- `--currency`: default `SGD`.
- `--limit`: default `3`.

The script prints one compact table or line-oriented report containing at least:

- airline or airline codes
- cheapest price with currency
- absolute Skyscanner deep link URL

## Data Flow

1. Parse CLI arguments.
2. Read `CHEAPY_SKYSCANNER_COOKIE`.
3. Validate IATA and date shapes.
4. Resolve origin IATA through Autosuggest.
5. Resolve destination IATA through Autosuggest.
6. Generate a `viewId` with `uuid.uuid4()`.
7. Build the minimal `web-unified-search` JSON body.
8. Send the POST with minimal headers:
   - `content-type`
   - `cookie`
   - `x-skyscanner-channelid`
   - `x-skyscanner-currency`
   - `x-skyscanner-locale`
   - `x-skyscanner-market`
   - `x-skyscanner-viewid`
9. Parse the JSON response.
10. Require `context.status == "complete"`.
11. Extract itineraries from `.itineraries.results`.
12. Sort itineraries by `.price.raw`.
13. Extract positive-price pricing options and deep link items.
14. Join relative deep link URLs against `https://www.skyscanner.com.sg`.
15. Print the top `--limit` usable results.

The route body uses Skyscanner entity IDs:

```json
{
  "legOrigin": {"@type": "entity", "entityId": "95673375"},
  "legDestination": {"@type": "entity", "entityId": "95673379"},
  "dates": {"@type": "date", "year": "2026", "month": "06", "day": "11"}
}
```

Round trips include a second leg with origin and destination reversed. One-way
searches include only the departure leg. The script should include
`placeOfStay` only when Autosuggest or known response data provides a reliable
destination city entity. It must not invent a `placeOfStay` value for unknown
routes.

## Autosuggest Resolution

`get_entity_id(iata_code)` calls a Skyscanner Autosuggest endpoint that returns
candidate places for the supplied IATA code.

Resolution rules:

1. Match candidates by exact IATA code after uppercasing input.
2. Prefer airport candidates over city or country candidates when candidate
   type metadata is present.
3. Return the candidate's Skyscanner entity ID.
4. Return a destination city or parent entity only when the Autosuggest payload
   exposes it clearly enough to support `placeOfStay`.
5. If no exact IATA candidate exists, raise `entity_not_found`.
6. If multiple exact plausible candidates remain after airport preference,
   raise `entity_ambiguous` and show safe candidate metadata.

Safe candidate metadata means IATA code, display name, entity ID, and candidate
type only. It must not include request cookies or raw response bodies.

## Fare Extraction

For each itinerary:

- canonical price comes from `.price.raw`
- formatted price may be used only for display
- itinerary is skipped if canonical price is missing or not positive
- airline is derived from unique marketing carrier display codes or names under
  `.legs[].segments[]`
- stop count is the sum of `.legs[].stopCount`
- departure and return times may be displayed when present

Pricing option handling:

- inspect `.pricingOptions[]`
- ignore options where `.price.amount <= 0`
- choose the lowest positive option
- find an item URL from the chosen pricing option
- turn relative URLs into absolute URLs under `https://www.skyscanner.com.sg`
- skip itineraries that do not provide a usable deep link URL

The script must not trust response order. It always sorts by canonical price.

## Error Handling

The script fails closed with concise terminal errors.

Expected failures:

| Condition | Error type |
| --- | --- |
| missing `CHEAPY_SKYSCANNER_COOKIE` | `missing_cookie` |
| invalid IATA or date argument | `invalid_argument` |
| Autosuggest HTTP non-2xx | `autosuggest_http_error` |
| no exact IATA candidate | `entity_not_found` |
| multiple plausible candidates | `entity_ambiguous` |
| search HTTP non-2xx | `search_http_error` |
| response is not valid JSON | `search_parse_error` |
| `context.status` is not `complete` | `search_incomplete` |
| results path is missing or not a list | `search_parse_error` |
| no positive-price itinerary with deep link | `no_usable_results` |

Errors must not print:

- cookie values
- JWT values
- anon tokens
- CSRF tokens
- session IDs
- full request headers
- full raw response bodies

HTTP timeout and transport failures should include only the endpoint family
(`autosuggest` or `search`) and the exception type.

## Testing

Default tests are offline.

Required coverage:

- missing cookie path
- IATA argument normalization
- date argument validation
- fake Autosuggest response resolving `HAN` to an entity ID
- fake Autosuggest response resolving `SGN` to an entity ID
- fake no-match Autosuggest response
- fake ambiguous Autosuggest response
- minimal search request body for one-way
- minimal search request body for round-trip
- generated `x-skyscanner-viewid` is UUID-shaped
- fake `web-unified-search` response extracts fares sorted by `.price.raw`
- pricing options with `amount == 0` are ignored
- missing deep link skips the itinerary
- relative deep link becomes an absolute Skyscanner URL
- `context.status != "complete"` maps to `search_incomplete`
- cookie and token-like strings do not appear in error output

Tests may use a fake HTTP client object injected into the two core functions.
The script should avoid making live network calls unless it is run manually by a
developer with `CHEAPY_SKYSCANNER_COOKIE` set.

## Future Provider Path

If the probe proves stable, a later spec can promote the logic into:

```text
cheapy/providers/skyscanner/
  manifest.toml
  adapter.py
  normalizer.py
  provider.py
```

That later provider should start as `default_enabled = false`, use the proven
Autosuggest resolver and result normalizer, and keep cookie injection explicit.
This probe intentionally stops before that step.
