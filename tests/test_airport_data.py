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
    "DMK",
    "KUL",
    "CGK",
    "DPS",
    "MNL",
    "CEB",
    "PNH",
    "SAI",
    "RGN",
    "KNO",
    "TPE",
    "HKG",
    "ICN",
    "GMP",
    "NRT",
    "HND",
    "KIX",
    "NGO",
    "FUK",
    "PEK",
    "PKX",
    "PVG",
    "SHA",
    "CAN",
    "SZX",
    "CTU",
    "TFU",
    "CKG",
    "XIY",
    "TAO",
    "DOH",
    "DXB",
    "AUH",
    "IST",
    "SAW",
    "TLV",
    "AMM",
    "CAI",
    "DEL",
    "BOM",
    "BLR",
    "MAA",
    "HYD",
    "COK",
    "CMB",
    "KTM",
    "LAX",
    "SFO",
    "JFK",
    "EWR",
    "BOS",
    "IAD",
    "ATL",
    "ORD",
    "DFW",
    "DEN",
    "SEA",
    "LAS",
    "PHX",
    "MIA",
    "FLL",
    "MCO",
    "IAH",
    "MSP",
    "DTW",
    "CLT",
    "PHL",
    "SAN",
    "PDX",
    "YVR",
    "YYZ",
    "YUL",
    "YYC",
    "MEX",
    "CUN",
    "GDL",
    "PTY",
    "SJO",
    "GRU",
    "GIG",
    "BSB",
    "EZE",
    "AEP",
    "SCL",
    "LIM",
    "BOG",
    "UIO",
    "GYE",
    "LHR",
    "LGW",
    "STN",
    "CDG",
    "ORY",
    "AMS",
    "FRA",
    "MUC",
    "DUS",
    "BER",
    "HAM",
    "ZRH",
    "GVA",
    "VIE",
    "MAD",
    "BCN",
    "LIS",
    "OPO",
    "FCO",
    "MXP",
    "ATH",
    "CPH",
    "ARN",
    "OSL",
    "HEL",
    "WAW",
    "PRG",
    "BRU",
    "DUB",
    "MAN",
    "EDI",
    "KEF",
    "JNB",
    "CPT",
    "ADD",
    "NBO",
    "CMN",
    "RAK",
    "TUN",
    "ALG",
    "LOS",
    "ACC",
    "SYD",
    "MEL",
    "BNE",
    "PER",
    "AKL",
}

EXPECTED_HUB_TIERS = {
    "SIN": 1,
    "DOH": 1,
    "DXB": 1,
    "IST": 1,
    "AMS": 1,
    "FRA": 1,
    "LHR": 1,
    "CDG": 1,
    "ICN": 1,
    "NRT": 1,
    "HKG": 2,
    "TPE": 2,
    "PEK": 1,
    "PVG": 1,
    "CAN": 1,
    "DEL": 1,
    "ATL": 1,
    "ORD": 1,
    "DFW": 1,
    "LAX": 1,
    "JFK": 1,
    "SFO": 1,
    "YYZ": 1,
    "ADD": 2,
    "AUH": 2,
    "BKK": 2,
    "BOG": 2,
    "CGK": 2,
    "DEN": 2,
    "GRU": 2,
    "HND": 2,
    "IAH": 2,
    "JNB": 2,
    "KUL": 2,
    "MAD": 2,
    "MEX": 2,
    "MIA": 2,
    "MNL": 2,
    "MUC": 2,
    "NBO": 2,
    "SEA": 2,
    "YVR": 2,
    "ZRH": 2,
    "ARN": 3,
    "ATH": 3,
    "BCN": 3,
    "BLR": 3,
    "BOM": 3,
    "BOS": 3,
    "BRU": 3,
    "CPH": 3,
    "DUB": 3,
    "EWR": 3,
    "FCO": 3,
    "GIG": 3,
    "HEL": 3,
    "KIX": 3,
    "LIS": 3,
    "MAN": 3,
    "SYD": 3,
    "MEL": 3,
    "MSP": 3,
    "OSL": 3,
    "PTY": 3,
    "SCL": 3,
    "VIE": 3,
    "WAW": 3,
}


def _load_json(name: str) -> dict:
    path = files("cheapy").joinpath("data", name)
    return json.loads(path.read_text(encoding="utf-8"))


def test_airport_snapshot_contains_exact_curated_global_airports() -> None:
    snapshot = _load_json("airports.v1.json")

    airports = snapshot["airports"]
    codes = {airport["iata"] for airport in airports}

    assert codes == EXPECTED_AIRPORTS
    assert snapshot["schema_version"] == 1
    assert snapshot["source_name"] == "OurAirports"
    assert snapshot["source_url"] == "https://ourairports.com/data/"
    assert snapshot["source_license"] == "public domain / no guarantee of accuracy"
    assert snapshot["retrieved_date"] == "2026-05-28"
    assert (
        snapshot["generation_method"]
        == "manual curated global airport snapshot for Cheapy flight-search coverage"
    )
    assert snapshot["snapshot_version"] == 2
    assert "data dictionary" in snapshot["notes"].lower()


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


def test_hub_snapshot_contains_exact_curated_global_tiers() -> None:
    snapshot = _load_json("hubs.v1.json")

    hubs = snapshot["hubs"]
    tiers = {hub["iata"]: hub["tier"] for hub in hubs}

    assert tiers == EXPECTED_HUB_TIERS
    assert snapshot["schema_version"] == 1
    assert snapshot["source_name"] == "Wikipedia List of hub airports"
    assert snapshot["source_url"] == "https://en.wikipedia.org/wiki/List_of_hub_airports"
    assert (
        snapshot["source_revision_url"]
        == "https://en.wikipedia.org/w/index.php?oldid=1344170237&title=List_of_hub_airports"
    )
    assert snapshot["retrieved_date"] == "2026-05-28"
    assert "CC BY-SA" in snapshot["license_name"]
    assert snapshot["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert "Wikipedia contributors" in snapshot["attribution"]
    assert "oldid=1344170237" in snapshot["attribution"]
    assert "manual curated excerpt" in snapshot["modification_notice"].lower()
    assert "modified local tiering" in snapshot["modification_notice"].lower()
    assert snapshot["selection_method"] == "manual curated global hub excerpt"
    assert snapshot["snapshot_version"] == 2
    assert "tier" in snapshot["notes"].lower()

    for hub in hubs:
        assert isinstance(hub["source_note"], str)
        assert hub["source_note"]


def test_hub_snapshot_only_references_known_airports() -> None:
    airports_snapshot = _load_json("airports.v1.json")
    hubs_snapshot = _load_json("hubs.v1.json")

    airport_codes = {airport["iata"] for airport in airports_snapshot["airports"]}
    hub_codes = {hub["iata"] for hub in hubs_snapshot["hubs"]}

    assert hub_codes <= airport_codes
