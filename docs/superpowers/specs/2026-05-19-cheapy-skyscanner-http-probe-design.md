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
get_entity_id(
    iata_code: str,
    *,
    config: ProbeConfig,
    client: httpx.Client,
    is_destination: bool = False,
) -> EntityResult
fetch_flights(
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None = None,
    *,
    config: ProbeConfig,
    client: httpx.Client,
) -> list[FlightProbeResult]
```

The CLI may wrap these with defaults from arguments and environment variables,
but the testable core takes explicit config and HTTP client arguments. It must
not hide network dependencies in module-level globals.

Core data shapes:

```python
@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    market: str
    locale: str
    currency: str
    cookie: str
    timeout_seconds: float


@dataclass(frozen=True)
class EntityResult:
    iata: str
    entity_id: str
    name: str
    place_type: str | None = None
    parent_entity_id: str | None = None
    place_of_stay_entity_id: str | None = None


@dataclass(frozen=True)
class FlightProbeResult:
    airline: str
    price_amount: float
    currency: str
    deeplink_url: str
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
4. Resolve origin IATA through Autosuggest with `is_destination=False`.
5. Resolve destination IATA through Autosuggest with `is_destination=True`.
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
searches include only the departure leg.

`placeOfStay` handling is explicit:

- `get_entity_id(..., is_destination=True)` fills
  `EntityResult.place_of_stay_entity_id` from a destination's parent or city
  entity when the Autosuggest response exposes one.
- `fetch_flights()` includes `placeOfStay` on the outbound leg only when
  `destination.place_of_stay_entity_id` is present.
- `fetch_flights()` omits `placeOfStay` when the destination resolver cannot
  prove a city or parent entity. It must not invent one for unknown routes.

## Autosuggest Resolution

`get_entity_id(iata_code, ..., is_destination)` calls Skyscanner's web
Autosuggest endpoint candidate:

```text
GET {base_url}/g/autosuggest-search/api/v1/search-flight/{market}/{locale}/{search_term}
```

Query parameters:

- `isDestination=true|false`
- `enable_general_search_v2=false`

Request headers:

- `accept: application/json`
- `cookie: <CHEAPY_SKYSCANNER_COOKIE>`
- `x-skyscanner-channelid: website`
- `x-skyscanner-currency: <currency>`
- `x-skyscanner-locale: <locale>`
- `x-skyscanner-market: <market>`

The implementation should keep the endpoint path in one constant so it can be
updated if Skyscanner moves the web Autosuggest surface.

The parser accepts the observed web-style field names and the documented
partner Autosuggest-style field names:

| Meaning | Accepted field names |
| --- | --- |
| result collection | `places`, `Places` |
| IATA code | `iataCode`, `IataCode`, `iata`, `IATA` |
| entity ID | `entityId`, `EntityId`, `PlaceId` |
| display name | `name`, `Name`, `PlaceName` |
| place type | `type`, `Type`, `placeType`, `PlaceType` |
| parent or city entity | `parentId`, `ParentId`, `CityId`, `cityId`, nested `parent.entityId` |

Resolution rules:

1. Match candidates by exact IATA code after uppercasing input.
2. Prefer airport candidates over city or country candidates when candidate
   type metadata is present.
3. Return the candidate's Skyscanner entity ID as `EntityResult.entity_id`.
4. For destination airport candidates, set `place_of_stay_entity_id` from a
   clear parent or city entity field when present.
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
- inspect only `.items[]` under the chosen pricing option
- choose the first item whose `.url` is a non-empty string; ties preserve
  response order
- turn relative URLs into absolute URLs under `https://www.skyscanner.com.sg`
- skip itineraries that do not provide a usable deep link URL

The script must not trust response order. It always sorts by canonical price.
Full deep link URLs may contain opaque Skyscanner tracking query parameters.
The probe may print them because deep links are one of the requested outputs,
but it must not persist them to committed fixtures or logs.

## Error Handling

The script fails closed with concise terminal errors.

Expected failures:

| Condition | Error type |
| --- | --- |
| missing `CHEAPY_SKYSCANNER_COOKIE` | `missing_cookie` |
| invalid IATA or date argument | `invalid_argument` |
| Autosuggest HTTP non-2xx | `autosuggest_http_error` |
| Autosuggest timeout or transport failure | `autosuggest_transport_error` |
| Autosuggest response is not valid JSON or has an unexpected shape | `autosuggest_parse_error` |
| no exact IATA candidate | `entity_not_found` |
| multiple plausible candidates | `entity_ambiguous` |
| search HTTP non-2xx | `search_http_error` |
| search timeout or transport failure | `search_transport_error` |
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
- fake Autosuggest HTTP non-2xx
- fake Autosuggest invalid JSON or missing result collection
- minimal search request body for one-way
- minimal search request body for round-trip
- outbound leg includes `placeOfStay` when destination entity has
  `place_of_stay_entity_id`
- outbound leg omits `placeOfStay` when destination entity has no reliable
  parent or city entity
- generated `x-skyscanner-viewid` is UUID-shaped
- fake search HTTP non-2xx
- fake search invalid JSON
- fake search response with missing or non-list `.itineraries.results`
- fake `web-unified-search` response extracts fares sorted by `.price.raw`
- pricing options with `amount == 0` are ignored
- missing deep link skips the itinerary
- relative deep link becomes an absolute Skyscanner URL
- `context.status != "complete"` maps to `search_incomplete`
- no positive-price itinerary with deep link maps to `no_usable_results`
- cookie and token-like strings do not appear in error output

Tests may use a fake HTTP client object injected into the two core functions.
The script should avoid making live network calls unless it is run manually by a
developer with `CHEAPY_SKYSCANNER_COOKIE` set.

## References

- Official Skyscanner Autosuggest documentation describes the partner
  `autosuggest/flights` response fields, including `places`, `entityId`,
  `iataCode`, `parentId`, `name`, and `type`.
- The web Autosuggest endpoint path is a researched same-origin browser surface,
  not the authenticated partner API. If live verification shows the path has
  moved, update the endpoint constant before implementing the resolver.

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
