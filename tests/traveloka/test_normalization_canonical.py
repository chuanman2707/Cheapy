from __future__ import annotations

from cheapy.providers.traveloka.normalization.canonical import canonical_search_result
from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload

from .normalization_fixtures import (
    _assert_parse_error,
    _one_way_request,
    _traveloka_search_result,
)


def test_canonical_search_result_maps_minor_unit_price() -> None:
    item = {
        "id": "tv-1",
        "fare": {
            "display": {
                "currencyValue": {"currency": "USD", "amount": "12345"},
                "numOfDecimalPoint": "2",
            }
        },
        "connectingFlightRoutes": [],
    }

    canonical = canonical_search_result(item)

    assert getattr(canonical, "payload")["id"] == "tv-1"
    assert getattr(canonical, "payload")["price"] == {
        "currency": "USD",
        "amount": 123.45,
    }


def test_normalize_payload_maps_traveloka_search_results_offer() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [_traveloka_search_result()],
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:tv-search-1"
    assert offer.provider == "traveloka"
    assert offer.price_amount == 298.9
    assert offer.currency == "USD"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.actual_departure_date == "2026-07-10"
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert [(leg.origin, leg.destination, leg.flight_number) for leg in offer.legs] == [
        ("SGN", "BKK", "VJ-801")
    ]


def test_normalize_payload_accepts_completed_empty_search_results() -> None:
    payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": []}}

    offers, errors = normalize_payload(payload, _one_way_request())

    assert offers == []
    assert errors == []


def test_normalize_payload_uses_traveloka_metadata_price_fallback() -> None:
    item = _traveloka_search_result(amount="17600")
    item.pop("fare")
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [item],
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert offers[0].price_amount == 176.0
    assert offers[0].currency == "USD"


def test_normalize_payload_reports_traveloka_search_result_parse_error() -> None:
    secret = "sk_live_traveloka_search_result_secret"
    item = _traveloka_search_result()
    item["debug"] = secret
    item.pop("connectingFlightRoutes")
    payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": [item]}}

    offers, errors = normalize_payload(payload, _one_way_request())

    _assert_parse_error(offers, errors, secret=secret)
