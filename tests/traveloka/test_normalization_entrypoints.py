from __future__ import annotations

import importlib

from cheapy.models import ErrorCode
from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload

from .normalization_fixtures import _one_way_request, _segment


def test_direct_normalizer_import_has_normalize_payload() -> None:
    normalizer = importlib.import_module("cheapy.providers.traveloka.normalizer")

    assert hasattr(normalizer, "normalize_payload")


def test_normalization_entrypoints_are_importable() -> None:
    from cheapy.providers.traveloka.normalization.entrypoints import (
        normalize_payload,
        normalize_selected_round_trip,
    )

    assert callable(normalize_payload)
    assert callable(normalize_selected_round_trip)


def test_normalize_payload_maps_one_way_offer() -> None:
    payload = {
        "data": {
            "flightSearchResult": {
                "itineraries": [
                    {
                        "id": "tv-ow-1",
                        "price": {"amount": 88.5, "currency": "USD"},
                        "durationMinutes": 95,
                        "stops": 0,
                        "segments": [_segment()],
                    }
                ]
            }
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:tv-ow-1"
    assert offer.provider == "traveloka"
    assert offer.price_amount == 88.5
    assert offer.currency == "USD"
    assert offer.requested_origin == "SGN"
    assert offer.requested_destination == "BKK"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.requested_departure_date == "2026-07-10"
    assert offer.actual_departure_date == "2026-07-10"
    assert offer.departure_offset_days == 0
    assert offer.actual_return_date is None
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert offer.flags.baggage_unknown is True
    assert [(leg.origin, leg.destination, leg.flight_number) for leg in offer.legs] == [
        ("SGN", "BKK", "VJ801")
    ]


def test_normalize_payload_empty_result_returns_no_errors() -> None:
    offers, errors = normalize_payload({"data": {"itineraries": []}}, _one_way_request())

    assert offers == []
    assert errors == []


def test_normalize_payload_reports_currency_unavailable() -> None:
    secret = "sk_live_traveloka_currency_secret"
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "missing-currency",
                    "debug": secret,
                    "price": {"amount": 88.5},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert offers == []
    assert len(errors) == 1
    assert errors[0].code == ErrorCode.PROVIDER_FAILED
    assert errors[0].details["provider"] == "traveloka"
    assert errors[0].details["failure_type"] == "currency_unavailable"
    assert "exception_type" in errors[0].details
    assert secret not in errors[0].model_dump_json()


def test_normalize_payload_preserves_valid_offers_when_one_item_fails() -> None:
    secret = "sk_live_traveloka_parse_secret"
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "valid",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                },
                {
                    "id": "invalid",
                    "debug": secret,
                    "price": {"amount": 100.0, "currency": "USD"},
                    "segments": [],
                },
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert [offer.offer_id for offer in offers] == ["traveloka:SGN-BKK:2026-07-10:valid"]
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "parse_error"
    assert errors[0].details["item_index"] == 2
    assert "exception_type" in errors[0].details
    assert secret not in errors[0].model_dump_json()
