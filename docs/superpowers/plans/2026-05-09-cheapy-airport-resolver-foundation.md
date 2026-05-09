# Cheapy Airport Resolver Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Gate 2: a small packaged airport resolver and MVP hub selector foundation, plus the Cheapy agent skill that teaches Codex/Claude to convert Vietnamese airport aliases into IATA before calling Cheapy tools.

**Architecture:** Cheapy remains provider-free in this gate. `cheapy.models.contracts` stays the contract source of truth. New `cheapy.airports` loads packaged JSON snapshots from `cheapy/data/`, resolves strict IATA values, calculates distance, and selects hub candidates. The agent skill handles human-language aliases; runtime only accepts IATA.

**Tech Stack:** Python 3.14, Pydantic v2, `importlib.resources`, Hatchling, uv, pytest.

---

## Current Baseline

Before starting, confirm the repo is at the expected state:

```bash
uv run pytest -q
git status --short
```

Expected test baseline: `19 passed`.

Do not add storage, provider calls, live network calls, or real MCP flight search behavior in this gate.

---

## Task 1: Add Packaged Airport And Hub Data

**Files:**

- Create `tests/test_airport_data.py`
- Create `cheapy/data/README.md`
- Create `cheapy/data/airports.v1.json`
- Create `cheapy/data/hubs.v1.json`

### Step 1.1: Write Data Contract Tests First

Create `tests/test_airport_data.py`:

```python
from __future__ import annotations

import json
from importlib.resources import files


EXPECTED_AIRPORTS = {
    "CXR",
    "SGN",
    "HAN",
    "DAD",
    "PQC",
    "SIN",
    "BKK",
    "KUL",
    "TPE",
    "HKG",
    "ICN",
    "NRT",
    "DOH",
    "DXB",
    "LAX",
    "SFO",
    "JFK",
    "LHR",
    "CDG",
    "FRA",
    "SYD",
    "MEL",
}

EXPECTED_HUB_TIERS = {
    "SIN": 1,
    "DOH": 1,
    "DXB": 1,
    "ICN": 1,
    "NRT": 1,
    "LHR": 1,
    "FRA": 1,
    "BKK": 2,
    "KUL": 2,
    "TPE": 2,
    "HKG": 2,
    "CDG": 2,
    "LAX": 2,
    "SFO": 2,
    "JFK": 3,
    "SYD": 3,
    "MEL": 3,
}


def _load_json(name: str) -> dict:
    path = files("cheapy").joinpath("data", name)
    return json.loads(path.read_text(encoding="utf-8"))


def test_airport_snapshot_contains_exact_mvp_airports() -> None:
    snapshot = _load_json("airports.v1.json")

    airports = snapshot["airports"]
    codes = {airport["iata"] for airport in airports}

    assert codes == EXPECTED_AIRPORTS
    assert snapshot["version"] == 1
    assert snapshot["source"]["name"] == "OurAirports"
    assert snapshot["source"]["license"] == "public domain"


def test_airports_have_required_coordinates() -> None:
    snapshot = _load_json("airports.v1.json")

    for airport in snapshot["airports"]:
        assert airport["iata"].isupper()
        assert len(airport["iata"]) == 3
        assert isinstance(airport["name"], str)
        assert isinstance(airport["city"], str)
        assert isinstance(airport["country"], str)
        assert isinstance(airport["latitude"], float)
        assert isinstance(airport["longitude"], float)


def test_hub_snapshot_contains_exact_mvp_tiers() -> None:
    snapshot = _load_json("hubs.v1.json")

    hubs = snapshot["hubs"]
    tiers = {hub["iata"]: hub["tier"] for hub in hubs}

    assert tiers == EXPECTED_HUB_TIERS
    assert snapshot["version"] == 1
    assert snapshot["source"]["name"] == "Wikipedia List of hub airports"
    assert "CC BY-SA" in snapshot["source"]["license"]
    assert "accessed" in snapshot["source"]["attribution"].lower()
```

Run:

```bash
uv run pytest tests/test_airport_data.py -v
```

Expected: FAIL because `cheapy/data/*.json` does not exist yet.

### Step 1.2: Add Data Resource README

Create `cheapy/data/README.md`:

```markdown
# Cheapy Packaged Data

Gate 2 intentionally ships a tiny hand-curated data snapshot. It is not a full airport database.

## airports.v1.json

Airport records are a manual mini snapshot derived from OurAirports public-domain airport data.

Source: https://ourairports.com/data/
License: public domain

The MVP snapshot includes only airports needed for the first Cheapy workflows and tests.

## hubs.v1.json

Hub candidates are manually curated from the Wikipedia "List of hub airports" page.

Source: https://en.wikipedia.org/wiki/List_of_hub_airports
License: CC BY-SA

Cheapy stores only airport codes and manually assigned MVP tiers. The full Wikipedia table is not copied into this repository.
```

### Step 1.3: Add Exact Airport Snapshot

Create `cheapy/data/airports.v1.json`:

```json
{
  "version": 1,
  "generated_at": "2026-05-09",
  "source": {
    "name": "OurAirports",
    "url": "https://ourairports.com/data/",
    "license": "public domain",
    "notes": "Manual mini snapshot derived from OurAirports airport data for Gate 2 MVP coverage."
  },
  "airports": [
    {"iata": "CXR", "name": "Cam Ranh International Airport", "city": "Nha Trang", "country": "Vietnam", "latitude": 11.9982, "longitude": 109.2194},
    {"iata": "SGN", "name": "Tan Son Nhat International Airport", "city": "Ho Chi Minh City", "country": "Vietnam", "latitude": 10.8188, "longitude": 106.6520},
    {"iata": "HAN", "name": "Noi Bai International Airport", "city": "Hanoi", "country": "Vietnam", "latitude": 21.2212, "longitude": 105.8072},
    {"iata": "DAD", "name": "Da Nang International Airport", "city": "Da Nang", "country": "Vietnam", "latitude": 16.0439, "longitude": 108.1994},
    {"iata": "PQC", "name": "Phu Quoc International Airport", "city": "Phu Quoc", "country": "Vietnam", "latitude": 10.1698, "longitude": 103.9931},
    {"iata": "SIN", "name": "Singapore Changi Airport", "city": "Singapore", "country": "Singapore", "latitude": 1.3644, "longitude": 103.9915},
    {"iata": "BKK", "name": "Suvarnabhumi Airport", "city": "Bangkok", "country": "Thailand", "latitude": 13.6900, "longitude": 100.7501},
    {"iata": "KUL", "name": "Kuala Lumpur International Airport", "city": "Kuala Lumpur", "country": "Malaysia", "latitude": 2.7456, "longitude": 101.7072},
    {"iata": "TPE", "name": "Taiwan Taoyuan International Airport", "city": "Taipei", "country": "Taiwan", "latitude": 25.0777, "longitude": 121.2328},
    {"iata": "HKG", "name": "Hong Kong International Airport", "city": "Hong Kong", "country": "Hong Kong", "latitude": 22.3080, "longitude": 113.9185},
    {"iata": "ICN", "name": "Incheon International Airport", "city": "Seoul", "country": "South Korea", "latitude": 37.4602, "longitude": 126.4407},
    {"iata": "NRT", "name": "Narita International Airport", "city": "Tokyo", "country": "Japan", "latitude": 35.7720, "longitude": 140.3929},
    {"iata": "DOH", "name": "Hamad International Airport", "city": "Doha", "country": "Qatar", "latitude": 25.2731, "longitude": 51.6081},
    {"iata": "DXB", "name": "Dubai International Airport", "city": "Dubai", "country": "United Arab Emirates", "latitude": 25.2532, "longitude": 55.3657},
    {"iata": "LAX", "name": "Los Angeles International Airport", "city": "Los Angeles", "country": "United States", "latitude": 33.9425, "longitude": -118.4081},
    {"iata": "SFO", "name": "San Francisco International Airport", "city": "San Francisco", "country": "United States", "latitude": 37.6213, "longitude": -122.3790},
    {"iata": "JFK", "name": "John F. Kennedy International Airport", "city": "New York", "country": "United States", "latitude": 40.6413, "longitude": -73.7781},
    {"iata": "LHR", "name": "Heathrow Airport", "city": "London", "country": "United Kingdom", "latitude": 51.4700, "longitude": -0.4543},
    {"iata": "CDG", "name": "Charles de Gaulle Airport", "city": "Paris", "country": "France", "latitude": 49.0097, "longitude": 2.5479},
    {"iata": "FRA", "name": "Frankfurt Airport", "city": "Frankfurt", "country": "Germany", "latitude": 50.0379, "longitude": 8.5622},
    {"iata": "SYD", "name": "Sydney Kingsford Smith Airport", "city": "Sydney", "country": "Australia", "latitude": -33.9399, "longitude": 151.1753},
    {"iata": "MEL", "name": "Melbourne Airport", "city": "Melbourne", "country": "Australia", "latitude": -37.6733, "longitude": 144.8433}
  ]
}
```

### Step 1.4: Add Exact Hub Snapshot

Create `cheapy/data/hubs.v1.json`:

```json
{
  "version": 1,
  "generated_at": "2026-05-09",
  "source": {
    "name": "Wikipedia List of hub airports",
    "url": "https://en.wikipedia.org/wiki/List_of_hub_airports",
    "license": "CC BY-SA",
    "attribution": "Manual curated excerpt from Wikipedia List of hub airports, accessed 2026-05-09. Cheapy stores only selected IATA codes and local MVP tiers.",
    "notes": "Tier values are Cheapy MVP routing priorities, not Wikipedia data."
  },
  "hubs": [
    {"iata": "SIN", "tier": 1},
    {"iata": "DOH", "tier": 1},
    {"iata": "DXB", "tier": 1},
    {"iata": "ICN", "tier": 1},
    {"iata": "NRT", "tier": 1},
    {"iata": "LHR", "tier": 1},
    {"iata": "FRA", "tier": 1},
    {"iata": "BKK", "tier": 2},
    {"iata": "KUL", "tier": 2},
    {"iata": "TPE", "tier": 2},
    {"iata": "HKG", "tier": 2},
    {"iata": "CDG", "tier": 2},
    {"iata": "LAX", "tier": 2},
    {"iata": "SFO", "tier": 2},
    {"iata": "JFK", "tier": 3},
    {"iata": "SYD", "tier": 3},
    {"iata": "MEL", "tier": 3}
  ]
}
```

Run:

```bash
uv run pytest tests/test_airport_data.py -v
```

Expected: PASS.

Commit:

```bash
git add tests/test_airport_data.py cheapy/data/README.md cheapy/data/airports.v1.json cheapy/data/hubs.v1.json
git commit -m "feat: add airport and hub data snapshots"
```

---

## Task 2: Implement Airport Resolver And Distance Utility

**Files:**

- Create `tests/test_airports.py`
- Create `cheapy/airports.py`

### Step 2.1: Write Resolver Tests First

Create `tests/test_airports.py`:

```python
from __future__ import annotations

import pytest

from cheapy.airports import AirportNotFound, haversine_km, load_airport_catalog, resolve_airport


def test_load_airport_catalog_indexes_by_iata() -> None:
    catalog = load_airport_catalog()

    assert catalog.resolve("SGN").city == "Ho Chi Minh City"
    assert catalog.resolve("CXR").name == "Cam Ranh International Airport"


def test_resolve_airport_normalizes_case_and_whitespace() -> None:
    airport = resolve_airport("  sgn  ")

    assert airport.iata == "SGN"


@pytest.mark.parametrize("value", ["Nha Trang", "Sai Gon", "SG", "", "   ", "XXXX"])
def test_resolve_airport_rejects_non_iata_and_unknown_values(value: str) -> None:
    with pytest.raises(AirportNotFound) as exc_info:
        resolve_airport(value)

    assert exc_info.value.code == "AIRPORT_NOT_FOUND"
    assert exc_info.value.value == value


def test_haversine_km_returns_reasonable_distance() -> None:
    cxr = resolve_airport("CXR")
    sgn = resolve_airport("SGN")

    distance = haversine_km(cxr, sgn)

    assert 300 <= distance <= 400
```

Run:

```bash
uv run pytest tests/test_airports.py -v
```

Expected: FAIL because `cheapy.airports` does not exist.

### Step 2.2: Implement `cheapy.airports`

Create `cheapy/airports.py`:

```python
from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AirportNotFound(ValueError):
    """Raised when a value cannot be resolved to a packaged IATA airport."""

    code = "AIRPORT_NOT_FOUND"

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(f"Airport not found for IATA value: {value!r}")


class AirportV1(_StrictDataModel):
    iata: str = Field(min_length=3, max_length=3)
    name: str
    city: str
    country: str
    latitude: float | None = None
    longitude: float | None = None


class AirportSourceV1(_StrictDataModel):
    name: str
    url: str
    license: str
    notes: str


class AirportSnapshotV1(_StrictDataModel):
    version: Literal[1]
    generated_at: str
    source: AirportSourceV1
    airports: list[AirportV1]


class AirportCatalog:
    def __init__(self, snapshot: AirportSnapshotV1) -> None:
        self.snapshot = snapshot
        self.airports_by_iata = {airport.iata: airport for airport in snapshot.airports}

    def resolve(self, value: str) -> AirportV1:
        normalized = value.strip().upper()
        airport = self.airports_by_iata.get(normalized)
        if airport is None:
            raise AirportNotFound(value)
        return airport


def _load_json_resource(name: str) -> dict:
    path = files("cheapy").joinpath("data", name)
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_airport_snapshot() -> AirportSnapshotV1:
    return AirportSnapshotV1.model_validate(_load_json_resource("airports.v1.json"))


@lru_cache(maxsize=1)
def load_airport_catalog() -> AirportCatalog:
    return AirportCatalog(load_airport_snapshot())


def resolve_airport(value: str, catalog: AirportCatalog | None = None) -> AirportV1:
    active_catalog = catalog or load_airport_catalog()
    return active_catalog.resolve(value)


def haversine_km(origin: AirportV1, destination: AirportV1) -> float:
    if origin.latitude is None or origin.longitude is None:
        raise ValueError(f"Airport {origin.iata} is missing coordinates")
    if destination.latitude is None or destination.longitude is None:
        raise ValueError(f"Airport {destination.iata} is missing coordinates")

    radius_km = 6371.0
    origin_lat = math.radians(origin.latitude)
    destination_lat = math.radians(destination.latitude)
    delta_lat = math.radians(destination.latitude - origin.latitude)
    delta_lon = math.radians(destination.longitude - origin.longitude)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(origin_lat) * math.cos(destination_lat) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c
```

Run:

```bash
uv run pytest tests/test_airports.py -v
uv run pytest tests/test_airport_data.py tests/test_airports.py -v
```

Expected: PASS.

Commit:

```bash
git add tests/test_airports.py cheapy/airports.py
git commit -m "feat: add airport resolver"
```

---

## Task 3: Add MVP Hub Selector

**Files:**

- Edit `tests/test_airports.py`
- Edit `cheapy/airports.py`

### Step 3.1: Add Hub Selector Tests First

Append to `tests/test_airports.py`:

```python
from cheapy.airports import (
    AirportCatalog,
    AirportSnapshotV1,
    AirportSourceV1,
    AirportV1,
    HubCatalog,
    HubSnapshotV1,
    HubSourceV1,
    HubV1,
    select_hub_candidates,
)


def test_select_hub_candidates_returns_sorted_candidates() -> None:
    result = select_hub_candidates("SGN", "LHR")

    assert result.reason is None
    assert 1 <= len(result.candidates) <= 3
    assert [candidate.tier for candidate in result.candidates] == sorted(
        candidate.tier for candidate in result.candidates
    )
    assert all(candidate.iata not in {"SGN", "LHR"} for candidate in result.candidates)
    assert all(candidate.detour_ratio <= 1.8 for candidate in result.candidates)


def test_select_hub_candidates_returns_route_too_short_for_short_routes() -> None:
    result = select_hub_candidates("CXR", "SGN")

    assert result.candidates == []
    assert result.reason == "route_too_short"


def test_select_hub_candidates_returns_no_hub_when_detour_filter_rejects_all() -> None:
    result = select_hub_candidates("SGN", "LHR", max_detour_ratio=0.5)

    assert result.candidates == []
    assert result.reason == "no_hub_passed_detour_filter"


def test_select_hub_candidates_returns_missing_coordinates_before_short_route_check() -> None:
    airport_source = AirportSourceV1(
        name="test",
        url="https://example.test",
        license="test",
        notes="test",
    )
    airport_catalog = AirportCatalog(
        AirportSnapshotV1(
            version=1,
            generated_at="2026-05-09",
            source=airport_source,
            airports=[
                AirportV1(
                    iata="AAA",
                    name="A",
                    city="A",
                    country="A",
                    latitude=None,
                    longitude=None,
                ),
                AirportV1(
                    iata="BBB",
                    name="B",
                    city="B",
                    country="B",
                    latitude=1.0,
                    longitude=1.0,
                ),
            ],
        )
    )
    hub_catalog = HubCatalog(
        HubSnapshotV1(
            version=1,
            generated_at="2026-05-09",
            source=HubSourceV1(
                name="test",
                url="https://example.test",
                license="test",
                attribution="test",
                notes="test",
            ),
            hubs=[HubV1(iata="BBB", tier=1)],
        )
    )

    result = select_hub_candidates("AAA", "BBB", airport_catalog=airport_catalog, hub_catalog=hub_catalog)

    assert result.candidates == []
    assert result.reason == "missing_airport_coordinates"


def test_select_hub_candidates_returns_missing_coordinates_when_no_hub_can_be_evaluated() -> None:
    airport_source = AirportSourceV1(
        name="test",
        url="https://example.test",
        license="test",
        notes="test",
    )
    airport_catalog = AirportCatalog(
        AirportSnapshotV1(
            version=1,
            generated_at="2026-05-09",
            source=airport_source,
            airports=[
                AirportV1(
                    iata="AAA",
                    name="A",
                    city="A",
                    country="A",
                    latitude=0.0,
                    longitude=0.0,
                ),
                AirportV1(
                    iata="BBB",
                    name="B",
                    city="B",
                    country="B",
                    latitude=50.0,
                    longitude=50.0,
                ),
                AirportV1(
                    iata="CCC",
                    name="C",
                    city="C",
                    country="C",
                    latitude=None,
                    longitude=None,
                ),
            ],
        )
    )
    hub_catalog = HubCatalog(
        HubSnapshotV1(
            version=1,
            generated_at="2026-05-09",
            source=HubSourceV1(
                name="test",
                url="https://example.test",
                license="test",
                attribution="test",
                notes="test",
            ),
            hubs=[HubV1(iata="CCC", tier=1)],
        )
    )

    result = select_hub_candidates(
        "AAA",
        "BBB",
        airport_catalog=airport_catalog,
        hub_catalog=hub_catalog,
        short_route_threshold_km=1,
    )

    assert result.candidates == []
    assert result.reason == "missing_airport_coordinates"
```

Run:

```bash
uv run pytest tests/test_airports.py -v
```

Expected: FAIL because hub models and `select_hub_candidates` are missing.

### Step 3.2: Implement Hub Models And Selection

Extend `cheapy/airports.py` with:

```python
HubSelectionReason = Literal[
    "route_too_short",
    "no_hub_passed_detour_filter",
    "missing_airport_coordinates",
]


class HubSourceV1(_StrictDataModel):
    name: str
    url: str
    license: str
    attribution: str
    notes: str


class HubV1(_StrictDataModel):
    iata: str = Field(min_length=3, max_length=3)
    tier: int = Field(ge=1, le=3)


class HubSnapshotV1(_StrictDataModel):
    version: Literal[1]
    generated_at: str
    source: HubSourceV1
    hubs: list[HubV1]


class HubCatalog:
    def __init__(self, snapshot: HubSnapshotV1) -> None:
        self.snapshot = snapshot
        self.hubs_by_iata = {hub.iata: hub for hub in snapshot.hubs}


class HubCandidate(_StrictDataModel):
    iata: str
    tier: int
    origin_to_hub_km: float
    hub_to_destination_km: float
    detour_ratio: float


class HubSelectionResult(_StrictDataModel):
    candidates: list[HubCandidate]
    reason: HubSelectionReason | None = None


@lru_cache(maxsize=1)
def load_hub_snapshot() -> HubSnapshotV1:
    return HubSnapshotV1.model_validate(_load_json_resource("hubs.v1.json"))


@lru_cache(maxsize=1)
def load_hub_catalog() -> HubCatalog:
    return HubCatalog(load_hub_snapshot())


def _has_coordinates(airport: AirportV1) -> bool:
    return airport.latitude is not None and airport.longitude is not None


def select_hub_candidates(
    origin_iata: str,
    destination_iata: str,
    *,
    max_candidates: int = 3,
    airport_catalog: AirportCatalog | None = None,
    hub_catalog: HubCatalog | None = None,
    short_route_threshold_km: float = 1500.0,
    max_detour_ratio: float = 1.8,
) -> HubSelectionResult:
    airports = airport_catalog or load_airport_catalog()
    hubs = hub_catalog or load_hub_catalog()

    origin = airports.resolve(origin_iata)
    destination = airports.resolve(destination_iata)

    if not _has_coordinates(origin) or not _has_coordinates(destination):
        return HubSelectionResult(candidates=[], reason="missing_airport_coordinates")

    direct_distance = haversine_km(origin, destination)
    if direct_distance < short_route_threshold_km:
        return HubSelectionResult(candidates=[], reason="route_too_short")

    candidates: list[HubCandidate] = []
    evaluated_hubs = 0
    skipped_missing_coordinates = 0

    for hub in hubs.snapshot.hubs:
        if hub.iata in {origin.iata, destination.iata}:
            continue

        try:
            hub_airport = airports.resolve(hub.iata)
        except AirportNotFound:
            skipped_missing_coordinates += 1
            continue

        if not _has_coordinates(hub_airport):
            skipped_missing_coordinates += 1
            continue

        evaluated_hubs += 1
        origin_to_hub = haversine_km(origin, hub_airport)
        hub_to_destination = haversine_km(hub_airport, destination)
        detour_ratio = (origin_to_hub + hub_to_destination) / direct_distance

        if detour_ratio <= max_detour_ratio:
            candidates.append(
                HubCandidate(
                    iata=hub.iata,
                    tier=hub.tier,
                    origin_to_hub_km=round(origin_to_hub, 2),
                    hub_to_destination_km=round(hub_to_destination, 2),
                    detour_ratio=round(detour_ratio, 4),
                )
            )

    candidates.sort(key=lambda candidate: (candidate.tier, candidate.detour_ratio, candidate.iata))
    selected = candidates[:max_candidates]

    if selected:
        return HubSelectionResult(candidates=selected, reason=None)
    if evaluated_hubs == 0 and skipped_missing_coordinates:
        return HubSelectionResult(candidates=[], reason="missing_airport_coordinates")
    return HubSelectionResult(candidates=[], reason="no_hub_passed_detour_filter")
```

Run:

```bash
uv run pytest tests/test_airports.py -v
```

Expected: PASS.

Commit:

```bash
git add tests/test_airports.py cheapy/airports.py
git commit -m "feat: add hub candidate selector"
```

---

## Task 4: Tighten Search Contract Descriptions To IATA-Only

**Files:**

- Edit `tests/test_schema_export.py`
- Edit `cheapy/models/contracts.py`

### Step 4.1: Add Schema Description Tests First

Update `tests/test_schema_export.py` with assertions against exported schema descriptions:

```python
def test_search_request_schema_documents_iata_only_airports() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)

    request_properties = exported["SearchRequestV1"]["properties"]
    origin_description = request_properties["origin"]["description"]
    destination_description = request_properties["destination"]["description"]

    assert "IATA" in origin_description
    assert "IATA" in destination_description
    assert "city" not in origin_description.lower()
    assert "city" not in destination_description.lower()
```

Run:

```bash
uv run pytest tests/test_schema_export.py -v
```

Expected: FAIL because current descriptions still allow city names.

### Step 4.2: Update Contract Field Descriptions

In `cheapy/models/contracts.py`, update only the descriptions for `SearchRequestV1.origin` and `SearchRequestV1.destination`.

Use wording equivalent to:

```python
origin: str = Field(
    min_length=1,
    description="Origin airport as a 3-letter IATA code. Cheapy tools only accept IATA codes.",
)
destination: str = Field(
    min_length=1,
    description="Destination airport as a 3-letter IATA code. Cheapy tools only accept IATA codes.",
)
```

Do not add a regex or Pydantic validation for IATA in `SearchRequestV1`. Gate 2 enforces IATA in the resolver, not the contract model.

Run:

```bash
uv run pytest tests/test_schema_export.py tests/test_contracts.py -v
```

Expected: PASS.

Commit:

```bash
git add tests/test_schema_export.py cheapy/models/contracts.py
git commit -m "fix: document search airports as iata-only"
```

---

## Task 5: Add Project-Local Cheapy Agent Skill

**Files:**

- Create `tests/test_cheapy_skill.py`
- Create `.codex/skills/cheapy/SKILL.md`

### Step 5.1: Write Skill Tests First

Create `tests/test_cheapy_skill.py`:

```python
from __future__ import annotations

from pathlib import Path


SKILL_PATH = Path(".codex/skills/cheapy/SKILL.md")


REQUIRED_ALIASES = {
    "nha trang": "CXR",
    "cam ranh": "CXR",
    "sài gòn": "SGN",
    "sai gon": "SGN",
    "tp hcm": "SGN",
    "ho chi minh": "SGN",
    "hà nội": "HAN",
    "ha noi": "HAN",
    "đà nẵng": "DAD",
    "da nang": "DAD",
    "phú quốc": "PQC",
    "phu quoc": "PQC",
}


def test_cheapy_skill_exists_in_project_local_path() -> None:
    assert SKILL_PATH.exists()


def test_cheapy_skill_explicitly_says_tools_accept_iata_only() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    normalized = text.lower()

    assert "only accept iata" in normalized
    assert "3-letter iata" in normalized
    assert "do not pass city names" in normalized


def test_cheapy_skill_contains_vietnamese_aliases_for_snapshot_airports() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8").lower()

    for alias, iata in REQUIRED_ALIASES.items():
        assert alias in text
        assert iata.lower() in text


def test_cheapy_skill_does_not_claim_runtime_resolves_aliases() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8").lower()

    assert "the agent is responsible" in text
    assert "cheapy runtime resolves vietnamese aliases" not in text
```

Run:

```bash
uv run pytest tests/test_cheapy_skill.py -v
```

Expected: FAIL because the skill file does not exist.

### Step 5.2: Create Skill

Create `.codex/skills/cheapy/SKILL.md`:

```markdown
---
name: cheapy-flight-search
description: Use when a user asks an agent to search flights with Cheapy, normalize airport aliases to IATA codes, or call Cheapy MCP tools.
---

# Cheapy Flight Search

Use this skill before calling Cheapy MCP tools.

Cheapy tools only accept IATA airport codes. Always pass origin and destination as 3-letter IATA codes.

The agent is responsible for understanding the user's sentence and converting clear airport aliases to IATA before calling Cheapy. Do not pass city names, airport names, or Vietnamese aliases into Cheapy tools.

## Vietnamese Airport Aliases

Use these aliases only when the user's meaning is clear.

| User text | IATA |
| --- | --- |
| nha trang | CXR |
| cam ranh | CXR |
| sân bay cam ranh | CXR |
| sài gòn | SGN |
| sai gon | SGN |
| tp hcm | SGN |
| ho chi minh | SGN |
| hồ chí minh | SGN |
| hà nội | HAN |
| ha noi | HAN |
| nội bài | HAN |
| noi bai | HAN |
| đà nẵng | DAD |
| da nang | DAD |
| phú quốc | PQC |
| phu quoc | PQC |

## Supported MVP Airports

Vietnam: CXR, SGN, HAN, DAD, PQC.

Regional and Asia: SIN, BKK, KUL, TPE, HKG, ICN, NRT, DOH, DXB.

Long haul: LAX, SFO, JFK, LHR, CDG, FRA, SYD, MEL.

## Calling Pattern

1. Convert clear human airport names to IATA.
2. Convert dates into ISO `YYYY-MM-DD`.
3. Decide one-way or round-trip from the user's sentence.
4. Call the Cheapy MCP search tool with IATA values only.
5. If an airport is ambiguous or outside the supported list, ask the user to clarify instead of guessing.
```

Run:

```bash
uv run pytest tests/test_cheapy_skill.py -v
```

Expected: PASS.

Commit:

```bash
git add tests/test_cheapy_skill.py .codex/skills/cheapy/SKILL.md
git commit -m "feat: add cheapy agent skill"
```

---

## Task 6: Verify Packaged Data Ships In Built Wheel

**Files:**

- Create `tests/test_package_data.py`
- Edit `pyproject.toml` if needed

### Step 6.1: Write Wheel Verification Test First

Create `tests/test_package_data.py`:

```python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_built_wheel_can_load_packaged_airport_data(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    wheel = next(dist_dir.glob("*.whl"))
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True, stdout=subprocess.PIPE)

    script = """
from importlib.resources import files
import json

base = files("cheapy").joinpath("data")
airports = json.loads(base.joinpath("airports.v1.json").read_text(encoding="utf-8"))
hubs = json.loads(base.joinpath("hubs.v1.json").read_text(encoding="utf-8"))
readme = base.joinpath("README.md").read_text(encoding="utf-8")

assert airports["version"] == 1
assert hubs["version"] == 1
assert "OurAirports" in readme
"""
    subprocess.run([str(python), "-c", script], check=True)
```

Run:

```bash
uv run pytest tests/test_package_data.py -v
```

Expected: FAIL if the wheel does not include JSON/README resources.

### Step 6.2: Make Package Data Inclusion Explicit

If the wheel test fails, update `pyproject.toml` with explicit Hatchling force-includes:

```toml
[tool.hatch.build.targets.wheel.force-include]
"cheapy/data/README.md" = "cheapy/data/README.md"
"cheapy/data/airports.v1.json" = "cheapy/data/airports.v1.json"
"cheapy/data/hubs.v1.json" = "cheapy/data/hubs.v1.json"
```

Run:

```bash
uv run pytest tests/test_package_data.py -v
```

Expected: PASS.

Commit:

```bash
git add tests/test_package_data.py pyproject.toml
git commit -m "test: verify packaged data in wheel"
```

If `pyproject.toml` was not needed because the first wheel test already passed, commit only `tests/test_package_data.py`.

---

## Task 7: Final Verification

Run all targeted checks:

```bash
uv run pytest tests/test_airport_data.py -v
uv run pytest tests/test_airports.py -v
uv run pytest tests/test_schema_export.py -v
uv run pytest tests/test_cheapy_skill.py -v
uv run pytest tests/test_package_data.py -v
uv run pytest -v
uv run cheapy --version
```

Expected:

- All tests pass.
- `uv run cheapy --version` prints the current package version.
- No live network calls are made by default tests.

Then inspect the final diff:

```bash
git status --short
git log --oneline -5
```

Only files from this plan should be staged or committed for Gate 2. Leave unrelated untracked planning/source files alone.

---

## Success Criteria

- Packaged airport data contains exactly the 22 MVP airports.
- Packaged hub data contains exactly the approved tier map.
- Resolver accepts `sgn`, ` SGN `, and rejects non-IATA aliases like `Nha Trang`.
- `AirportNotFound.code == "AIRPORT_NOT_FOUND"`.
- `haversine_km(CXR, SGN)` returns a reasonable distance.
- Hub selector returns max 3 candidates, sorted by tier, detour ratio, then IATA.
- Hub selector empty reasons are exactly:
  - `route_too_short`
  - `no_hub_passed_detour_filter`
  - `missing_airport_coordinates`
- Search contract descriptions explicitly say Cheapy accepts IATA only.
- `.codex/skills/cheapy/SKILL.md` exists and teaches the agent to convert Vietnamese aliases before calling tools.
- Built wheel can load `cheapy/data/airports.v1.json`, `cheapy/data/hubs.v1.json`, and `cheapy/data/README.md`.
