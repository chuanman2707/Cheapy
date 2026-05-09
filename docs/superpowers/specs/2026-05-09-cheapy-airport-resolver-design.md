# Cheapy Airport Resolver Design

Date: 2026-05-09

## Purpose

Gate 2 builds Cheapy's airport data foundation before any provider or real MCP search work.

The goal is to make route handling deterministic enough for later orchestrator, planner, and provider work:

- bundled airport data
- strict IATA validation
- distance calculation
- bounded hub candidate selection
- Cheapy-specific agent skill guidance for Vietnamese aliases

Gate 2 does not search flights, call providers, create split-ticket searches, or add end-user CLI airport commands.

## Scope

Gate 2 includes:

- packaged airport data in `cheapy/data/airports.v1.json`
- packaged hub data in `cheapy/data/hubs.v1.json`
- data reproduction/provenance notes in `cheapy/data/README.md`
- one public-ish Python module: `cheapy.airports`
- Pydantic models for airport and hub data
- strict IATA resolver
- `haversine_km` distance utility
- MVP hub candidate selector
- package-data tests
- resolver tests
- distance tests
- hub selector tests
- Cheapy-specific project-local agent skill at `.codex/skills/cheapy/SKILL.md`
- Contract schema description updates for `origin` and `destination`

Gate 2 excludes:

- real MCP server implementation
- provider registry or provider calls
- Google Flights / `google_fli`
- Traveloka research
- split-ticket search candidate creation
- nearby-airport search expansion
- runtime airport data fetching
- generator script for full airport snapshot
- CLI airport debug commands
- storage

## Current Contract Adjustment

Gate 1 created `SearchRequestV1.origin` and `SearchRequestV1.destination` as plain strings with broad descriptions.

Gate 2 keeps the fields as strings, but changes their schema descriptions to IATA-only:

- `origin`: three-letter IATA airport code; agents must resolve city names before calling Cheapy
- `destination`: three-letter IATA airport code; agents must resolve city names before calling Cheapy

Pydantic request validation does not enforce the IATA regex in Gate 2. The airport resolver enforces runtime support.

This is intentional:

- the user decided runtime Cheapy should be strict IATA-only
- alias handling belongs in the Cheapy agent skill
- resolver remains responsible for checking that an IATA code exists in Cheapy's bundled snapshot

If the agent sends `"Nha Trang"` instead of `"CXR"`, resolver rejects it. Later orchestrator work will convert `AirportNotFound` into `SearchResponseV1(status="failed")` with `AIRPORT_NOT_FOUND`.

## Data Sources

### Airports

The airport snapshot is a manual mini snapshot sourced from OurAirports.

OurAirports is suitable for Gate 2 because it provides airport name, municipality/city, country, IATA code, latitude, and longitude, and its public pages state the data is released to the public domain with no guarantee of accuracy.

Gate 2 does not write a generator script. Instead, `cheapy/data/README.md` must include reproduction notes:

- source name: OurAirports
- source URL: `https://ourairports.com/data/`
- data dictionary URL: `https://ourairports.com/help/data-dictionary.html`
- license note: public domain / no guarantee of accuracy
- retrieved date
- selection method: manual mini snapshot for Gate 2 MVP
- exact IATA list included

### Hubs

The hub source is a manual curated excerpt from Wikipedia's List of hub airports.

This is a pragmatic MVP reference, not a perfect global hub authority. Gate 2 does not redistribute article prose or table text. `hubs.v1.json` stores Cheapy-curated factual IATA/tier decisions after consulting the Wikipedia page. Because Wikipedia article text is reusable only under its open-content license terms, the hub data file and README must still keep concrete provenance and attribution metadata:

- source name: Wikipedia List of hub airports
- source URL: `https://en.wikipedia.org/wiki/List_of_hub_airports`
- permanent revision URL or oldid used for curation
- retrieved date
- license name and license URL
- attribution text naming Wikipedia contributors
- modified/excerpted notice
- selection method: manual curated excerpt for MVP routing experiments
- tier policy

Runtime does not fetch Wikipedia. Gate 2 does not scrape or parse Wikipedia HTML.

## Airport Snapshot

`cheapy/data/airports.v1.json` stores a mini airport snapshot with metadata and airport entries.

Required metadata:

- `schema_version`
- `source_name`
- `source_url`
- `source_license`
- `retrieved_date`
- `generation_method`
- `snapshot_version`
- `notes`

Required airport fields:

- `iata`
- `name`
- `city`
- `country`
- `latitude`
- `longitude`

The JSON keys must exist for every airport. Gate 2 packaged data must use non-null numeric latitude and longitude values. The runtime models may still tolerate missing coordinates for injected test catalogs or future imperfect data, so hub selection can return `missing_airport_coordinates` instead of crashing.

Gate 2 airport list:

Vietnam:

- `CXR`
- `SGN`
- `HAN`
- `DAD`
- `PQC`

Regional / Asia / Middle East:

- `SIN`
- `BKK`
- `KUL`
- `TPE`
- `HKG`
- `ICN`
- `NRT`
- `DOH`
- `DXB`

Long-haul test airports / hubs:

- `LAX`
- `SFO`
- `JFK`
- `LHR`
- `CDG`
- `FRA`
- `SYD`
- `MEL`

All airport IATA codes must be unique.

## Hub Data

`cheapy/data/hubs.v1.json` stores bounded hub candidates with provenance.

Required metadata:

- `schema_version`
- `source_name`
- `source_url`
- `source_revision_url`
- `retrieved_date`
- `license_name`
- `license_url`
- `attribution`
- `modification_notice`
- `selection_method`
- `snapshot_version`
- `notes`

Required hub fields:

- `iata`
- `tier`
- `source_note`

Tier policy:

- Tier 1: `SIN`, `DOH`, `DXB`, `ICN`, `NRT`, `LHR`, `FRA`
- Tier 2: `BKK`, `KUL`, `TPE`, `HKG`, `CDG`, `LAX`, `SFO`
- Tier 3: `JFK`, `SYD`, `MEL`

Every hub IATA code must exist in `airports.v1.json`.

Tests must enforce the exact hub tier mapping, not only that the listed hubs are present.

## Python Module

Gate 2 adds one module:

```text
cheapy/airports.py
```

It is public-ish for tests and future orchestrator work, but it is not exported from `cheapy.__init__`.

Expected imports:

```python
from cheapy.airports import (
    AirportCatalog,
    AirportNotFound,
    HubSelectionResult,
    haversine_km,
    load_airport_catalog,
    load_hub_catalog,
    resolve_airport,
    select_hub_candidates,
)
```

The module should use `importlib.resources` to load packaged JSON resources.

## Models

Gate 2 uses Pydantic models for data validation.

Expected model concepts:

- `AirportV1`
- `AirportSnapshotV1`
- `HubV1`
- `HubSnapshotV1`
- `HubCandidate`
- `HubSelectionResult`

Model names can vary slightly in implementation if the plan chooses clearer names, but tests must cover the behavior.

`HubCandidate` must include at least:

- `iata`
- `tier`
- `origin_to_hub_km`
- `hub_to_destination_km`
- `detour_ratio`

`HubSelectionResult` must include:

- `candidates`
- `reason`

When candidates exist, `reason` is `None`. When candidates are empty, `reason` is one of the Gate 2 reason codes.

## Resolver Behavior

`resolve_airport(value: str)`:

- strips surrounding whitespace
- uppercases the value
- accepts only three-letter IATA-shaped values after normalization
- returns the airport when it exists in the snapshot
- raises `AirportNotFound` when the value is not IATA-shaped
- raises `AirportNotFound` when the value is IATA-shaped but absent from the snapshot

Examples:

- `resolve_airport("SGN")` returns Tan Son Nhat / Ho Chi Minh City
- `resolve_airport("sgn")` returns the same airport
- `resolve_airport(" NRT ")` returns Narita
- `resolve_airport("Nha Trang")` raises `AirportNotFound`
- `resolve_airport("XXX")` raises `AirportNotFound`

Gate 2 does not return `SearchResponseV1` directly from resolver errors. Later orchestrator work will translate `AirportNotFound` into Contract V1 response errors.

## Distance Utility

`haversine_km` computes distance using airport latitude/longitude. The implementation may accept airport objects or coordinates, but public tests should exercise it through resolved airports so the behavior matches future planner usage.

Expected behavior:

- same airport distance is `0`
- distance is symmetric
- known route distances are approximately correct within test tolerance
- missing coordinates cause the caller to return `missing_airport_coordinates` where applicable

Gate 2 does not use distance for nearby-airport expansion.

## Hub Selector Behavior

`select_hub_candidates(origin_iata, destination_iata, max_candidates=3)`:

- resolves origin and destination through the strict resolver
- computes direct distance
- returns no candidates with reason `route_too_short` when direct distance is below `1500 km`
- evaluates hubs from `hubs.v1.json`
- skips hubs equal to origin or destination
- computes detour ratio:

```text
(origin_to_hub_km + hub_to_destination_km) / direct_distance_km
```

- keeps hubs with detour ratio `<= 1.8`
- sorts by tier first, detour ratio second, IATA code third
- returns at most `max_candidates`
- returns no candidates with reason `no_hub_passed_detour_filter` when no hub passes the filter
- returns no candidates with reason `missing_airport_coordinates` if required coordinates are unavailable

Failure precedence:

1. Resolve origin and destination first. Invalid or unknown origin/destination raises `AirportNotFound`.
2. If origin or destination lacks coordinates, return empty candidates with `missing_airport_coordinates`.
3. Compute direct distance and apply the short-route threshold.
4. Evaluate hubs. A hub missing coordinates is skipped.
5. If every otherwise-eligible hub is skipped because of missing coordinates and no candidate can be evaluated deterministically, return `missing_airport_coordinates`.
6. If hubs are evaluated but none pass the detour filter, return `no_hub_passed_detour_filter`.

Reason codes in Gate 2:

- `route_too_short`
- `no_hub_passed_detour_filter`
- `missing_airport_coordinates`

The selector does not create split-ticket searches and does not call providers.

## Cheapy Agent Skill

Gate 2 adds:

```text
.codex/skills/cheapy/SKILL.md
```

This is the canonical Cheapy skill path. It preserves compatibility with the master spec's future installer target. The skill name may be `cheapy-flight-search` in YAML frontmatter if that improves discovery, but the file path remains `.codex/skills/cheapy/SKILL.md`.

The skill must explicitly state:

- Cheapy tools only accept three-letter IATA airport codes for `origin` and `destination`
- agents must resolve city names and Vietnamese aliases before calling Cheapy
- if unsure, agents should ask the user instead of sending city names to Cheapy
- aliases are limited to the Gate 2 snapshot

Vietnam alias examples:

- `Nha Trang`, `Cam Ranh` -> `CXR`
- `Sài Gòn`, `Sai Gon`, `Saigon`, `TPHCM`, `Ho Chi Minh`, `Ho Chi Minh City` -> `SGN`
- `Hà Nội`, `Ha Noi`, `Hanoi` -> `HAN`
- `Đà Nẵng`, `Da Nang` -> `DAD`
- `Phú Quốc`, `Phu Quoc` -> `PQC`

The skill may include aliases for the regional and long-haul airports in the snapshot, but it must not include aliases for airports absent from `airports.v1.json`.

## Tests

Gate 2 test coverage must include:

### Packaged Data

- airport JSON loads via `importlib.resources`
- hub JSON loads via `importlib.resources`
- required provenance fields exist
- required airport fields exist
- required hub fields exist
- airport IATA codes are unique
- every hub IATA exists in the airport snapshot
- `set(airport_iata)` exactly equals the Gate 2 airport list with no extras
- hub tier mapping exactly equals the Gate 2 tier policy

### Resolver

- uppercase IATA resolves
- lowercase IATA resolves after normalization
- whitespace is stripped
- city/name aliases like `"Nha Trang"` are rejected
- unknown IATA like `"XXX"` is rejected

### Distance

- same airport is zero
- distance is symmetric
- `CXR -> SGN` is within a reasonable tolerance

### Hub Selector

- `CXR -> SGN` returns empty candidates with `route_too_short`
- long-haul route such as `SGN -> LAX` returns at most 3 candidates
- candidates are sorted by tier then detour ratio
- hub equal to origin or destination is skipped
- detour ratio filter is enforced
- no passing hubs returns `no_hub_passed_detour_filter`
- missing coordinate case returns `missing_airport_coordinates`
- resolver errors, coordinate errors, short-route filtering, and detour filtering follow the documented failure precedence

### Contract Description

- `SearchRequestV1.origin` description says IATA-only
- `SearchRequestV1.destination` description says IATA-only
- Contract V1 does not add new public fields for hubs
- `WarningCode` does not add hub-specific codes in Gate 2

### Cheapy Skill

- `.codex/skills/cheapy/SKILL.md` exists
- skill says Cheapy only accepts IATA codes
- skill contains the required Vietnam aliases
- skill does not contain aliases for airports outside the Gate 2 snapshot

### Package

- `uv build --wheel` succeeds
- the built wheel contains `cheapy/data/airports.v1.json`
- the built wheel contains `cheapy/data/hubs.v1.json`
- the built wheel contains `cheapy/data/README.md`
- an installed wheel can load the airport and hub JSON through `importlib.resources`
- resources should be loaded through `importlib.resources.files("cheapy").joinpath("data", ...)`; `cheapy/data/__init__.py` is not required

## Acceptance Criteria

Gate 2 is complete when:

- `uv run pytest -v` passes
- `uv build --wheel` passes
- package-data tests prove airport and hub JSON are present in the wheel
- `uv run cheapy schema | uv run python -m json.tool` passes
- no live network access is required
- no provider code is added
- no real MCP server behavior is added
- no CLI airport debug command is added
- airport and hub data load from packaged resources
- Cheapy skill clearly instructs agents to send IATA codes only

## Deferred Work

Deferred to later gates:

- full OurAirports snapshot generator
- automated Wikipedia scraping or hub generator
- nearby-airport expansion planner
- split-ticket planner
- provider registry
- `google_fli` provider
- MCP search tool implementation
- CLI install hooks for Codex and Claude
