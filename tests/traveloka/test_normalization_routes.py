from __future__ import annotations

from cheapy.models import FlightLegV1
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload
from cheapy.providers.traveloka.normalization.routes import validate_route

from .normalization_fixtures import (
    _assert_parse_error,
    _one_way_request,
    _round_trip_request,
    _segment,
)


def _leg(origin: str, destination: str) -> FlightLegV1:
    return FlightLegV1(
        origin=origin,
        destination=destination,
        departure_time="2026-07-10T09:00:00",
        arrival_time="2026-07-10T10:35:00",
        airline_code="VJ",
        flight_number="VJ801",
        duration_minutes=95,
    )


def test_validate_route_accepts_one_way_chain() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )

    route = validate_route(request, [_leg("SGN", "BKK")])

    assert route.outbound_end_index == 0
    assert route.return_start_index is None


def test_normalize_payload_rejects_one_way_wrong_outbound_date() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "wrong-date",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [
                        _segment(
                            departure_time="2026-07-11T09:00:00",
                            arrival_time="2026-07-11T10:35:00",
                        )
                    ],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    _assert_parse_error(offers, errors)


def test_normalize_payload_rejects_wrong_one_way_route() -> None:
    secret = "sk_live_traveloka_wrong_route_secret"
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "wrong-route",
                    "debug": secret,
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment(origin="HAN", destination="BKK")],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    _assert_parse_error(offers, errors, secret=secret)


def test_normalize_payload_rejects_malformed_datetime_without_leaking_value() -> None:
    invalid_datetime = "not-a-date"
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "bad-time",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [
                        _segment(
                            departure_time=invalid_datetime,
                            arrival_time="2026-07-10T10:35:00",
                        )
                    ],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    _assert_parse_error(offers, errors, secret=invalid_datetime)


def test_normalize_payload_rejects_round_trip_wrong_outbound_date() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "wrong-outbound-date",
                    "price": {"amount": 176.0, "currency": "USD"},
                    "durationMinutes": 190,
                    "stops": 0,
                    "segments": [
                        _segment(
                            departure_time="2026-07-11T09:00:00",
                            arrival_time="2026-07-11T10:35:00",
                        ),
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

    _assert_parse_error(offers, errors, capability="exact_round_trip")
