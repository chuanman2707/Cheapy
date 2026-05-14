from __future__ import annotations

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka.normalizer import normalize_payload


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def _segment(
    *,
    origin: str = "SGN",
    destination: str = "BKK",
    departure_time: str = "2026-07-10T09:00:00",
    arrival_time: str = "2026-07-10T10:35:00",
    flight_number: str = "VJ801",
) -> dict[str, object]:
    return {
        "origin": origin,
        "destination": destination,
        "departureTime": departure_time,
        "arrivalTime": arrival_time,
        "airlineCode": "VJ",
        "flightNumber": flight_number,
        "durationMinutes": 95,
    }


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


def test_normalize_payload_ranks_mixed_currency_offers_sequentially() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "usd",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                },
                {
                    "id": "eur",
                    "price": {"amount": 90.0, "currency": "EUR"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                },
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert [offer.offer_id for offer in offers] == [
        "traveloka:SGN-BKK:2026-07-10:usd",
        "traveloka:SGN-BKK:2026-07-10:eur",
    ]
    assert [offer.rank_within_currency for offer in offers] == [1, 2]
    assert [offer.global_rank for offer in offers] == [1, 2]
    assert [offer.comparable for offer in offers] == [True, True]


def test_normalize_payload_maps_round_trip_offer() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "tv-rt-1",
                    "price": {"amount": 176.0, "currency": "USD"},
                    "durationMinutes": 190,
                    "stops": 0,
                    "segments": [
                        _segment(),
                        _segment(
                            origin="BKK",
                            destination="SGN",
                            departure_time="2026-07-17T11:00:00",
                            arrival_time="2026-07-17T12:35:00",
                            flight_number="VJ802",
                        ),
                    ],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert errors == []
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:tv-rt-1"
    assert offer.actual_return_date == "2026-07-17"
    assert offer.return_offset_days == 0
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]


def test_normalize_payload_empty_result_returns_no_errors() -> None:
    offers, errors = normalize_payload({"data": {"itineraries": []}}, _one_way_request())

    assert offers == []
    assert errors == []


def test_normalize_payload_reports_currency_unavailable() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "missing-currency",
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


def test_normalize_payload_preserves_valid_offers_when_one_item_fails() -> None:
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
