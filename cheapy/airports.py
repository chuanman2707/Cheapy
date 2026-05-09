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
