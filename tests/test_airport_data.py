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
