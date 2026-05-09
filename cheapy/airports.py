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


class AirportSnapshotV1(_StrictDataModel):
    schema_version: Literal[1]
    source_name: str
    source_url: str
    source_license: str
    retrieved_date: str
    generation_method: str
    snapshot_version: int
    notes: str
    airports: list[AirportV1]
    version: Literal[1] | None = None


HubSelectionReason = Literal[
    "route_too_short",
    "no_hub_passed_detour_filter",
    "missing_airport_coordinates",
]


class HubV1(_StrictDataModel):
    iata: str = Field(min_length=3, max_length=3)
    tier: int = Field(ge=1, le=3)
    source_note: str


class HubSnapshotV1(_StrictDataModel):
    schema_version: Literal[1]
    source_name: str
    source_url: str
    source_revision_url: str
    retrieved_date: str
    license_name: str
    license_url: str
    attribution: str
    modification_notice: str
    selection_method: str
    snapshot_version: int
    notes: str
    hubs: list[HubV1]
    version: Literal[1] | None = None


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


def _load_json_resource(name: str) -> dict:
    path = files("cheapy").joinpath("data", name)
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_airport_snapshot() -> AirportSnapshotV1:
    return AirportSnapshotV1.model_validate(_load_json_resource("airports.v1.json"))


@lru_cache(maxsize=1)
def load_airport_catalog() -> AirportCatalog:
    return AirportCatalog(load_airport_snapshot())


@lru_cache(maxsize=1)
def load_hub_snapshot() -> HubSnapshotV1:
    return HubSnapshotV1.model_validate(_load_json_resource("hubs.v1.json"))


@lru_cache(maxsize=1)
def load_hub_catalog() -> HubCatalog:
    return HubCatalog(load_hub_snapshot())


def resolve_airport(value: str, catalog: AirportCatalog | None = None) -> AirportV1:
    active_catalog = catalog or load_airport_catalog()
    return active_catalog.resolve(value)


def _has_coordinates(airport: AirportV1) -> bool:
    return airport.latitude is not None and airport.longitude is not None


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
    if max_candidates < 1:
        raise ValueError("max_candidates must be greater than or equal to 1")

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
