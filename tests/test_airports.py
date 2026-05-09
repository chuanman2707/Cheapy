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
