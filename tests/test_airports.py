from __future__ import annotations

import pytest

from cheapy.airports import (
    AirportCatalog,
    AirportNotFound,
    AirportSnapshotV1,
    AirportSourceV1,
    AirportV1,
    HubCatalog,
    HubSnapshotV1,
    HubSourceV1,
    HubV1,
    haversine_km,
    load_airport_catalog,
    resolve_airport,
    select_hub_candidates,
)


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


def test_select_hub_candidates_limits_candidates_to_one() -> None:
    result = select_hub_candidates("SGN", "LHR", max_candidates=1)

    assert result.reason is None
    assert len(result.candidates) == 1


@pytest.mark.parametrize("max_candidates", [0, -1])
def test_select_hub_candidates_rejects_non_positive_max_candidates(max_candidates: int) -> None:
    with pytest.raises(ValueError, match="max_candidates"):
        select_hub_candidates("SGN", "LHR", max_candidates=max_candidates)


def test_select_hub_candidates_sorts_by_tier_detour_ratio_and_iata() -> None:
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
                AirportV1(iata="AAA", name="A", city="A", country="A", latitude=0.0, longitude=0.0),
                AirportV1(iata="BBB", name="B", city="B", country="B", latitude=0.0, longitude=100.0),
                AirportV1(iata="CCC", name="C", city="C", country="C", latitude=30.0, longitude=50.0),
                AirportV1(iata="DDD", name="D", city="D", country="D", latitude=0.0, longitude=50.0),
                AirportV1(iata="EEE", name="E", city="E", country="E", latitude=0.0, longitude=50.0),
                AirportV1(iata="FFF", name="F", city="F", country="F", latitude=0.0, longitude=50.0),
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
            hubs=[
                HubV1(iata="CCC", tier=1),
                HubV1(iata="FFF", tier=1),
                HubV1(iata="DDD", tier=2),
                HubV1(iata="EEE", tier=1),
            ],
        )
    )

    result = select_hub_candidates(
        "AAA",
        "BBB",
        airport_catalog=airport_catalog,
        hub_catalog=hub_catalog,
        short_route_threshold_km=1,
    )

    assert [candidate.iata for candidate in result.candidates] == ["EEE", "FFF", "CCC"]


def test_select_hub_candidates_skips_origin_and_destination_when_they_are_hubs() -> None:
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
                AirportV1(iata="AAA", name="A", city="A", country="A", latitude=0.0, longitude=0.0),
                AirportV1(iata="BBB", name="B", city="B", country="B", latitude=0.0, longitude=100.0),
                AirportV1(iata="CCC", name="C", city="C", country="C", latitude=0.0, longitude=50.0),
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
            hubs=[
                HubV1(iata="AAA", tier=1),
                HubV1(iata="CCC", tier=1),
                HubV1(iata="BBB", tier=1),
            ],
        )
    )

    result = select_hub_candidates(
        "AAA",
        "BBB",
        airport_catalog=airport_catalog,
        hub_catalog=hub_catalog,
        short_route_threshold_km=1,
    )

    assert [candidate.iata for candidate in result.candidates] == ["CCC"]
