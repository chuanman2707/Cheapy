from __future__ import annotations

from cheapy.models import FlightLegV1
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka.normalization.routes import validate_route


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


# Legacy normalization test helpers moved from tests/test_traveloka_normalizer.py.
from decimal import Decimal

from cheapy.models import ErrorCode, ErrorV1, FlightOfferV1
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload
from cheapy.providers.traveloka.normalization.selected import (
    normalize_selected_round_trip,
)
from cheapy.providers.traveloka.results import TravelokaSelectedRoundTripResult


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


def _traveloka_search_result(
    *,
    item_id: str = "tv-search-1",
    amount: str = "29890",
    decimal_points: str = "2",
    flight_number: str = "VJ-801",
    origin: str = "SGN",
    destination: str = "BKK",
    departure_day: str = "10",
    departure_hour: str = "9",
    arrival_hour: str = "10",
    arrival_minute: str = "35",
    return_flight_number: str | None = None,
) -> dict[str, object]:
    def route(
        *,
        origin: str,
        destination: str,
        departure_day: str,
        flight_number: str,
        departure_hour: str = "9",
        arrival_hour: str = "10",
        arrival_minute: str = "35",
    ) -> dict[str, object]:
        return {
            "departureAirport": origin,
            "arrivalAirport": destination,
            "totalNumStop": "0",
            "durationInMinutes": "95",
            "segments": [
                {
                    "departureAirport": origin,
                    "arrivalAirport": destination,
                    "flightNumber": flight_number,
                    "airlineCode": "VJ",
                    "durationMinutes": "95",
                    "departureDate": {
                        "year": "2026",
                        "month": "7",
                        "day": departure_day,
                    },
                    "departureTime": {"hour": departure_hour, "minute": "0"},
                    "arrivalDate": {
                        "year": "2026",
                        "month": "7",
                        "day": departure_day,
                    },
                    "arrivalTime": {
                        "hour": arrival_hour,
                        "minute": arrival_minute,
                    },
                }
            ],
        }

    routes = [
        route(
            origin=origin,
            destination=destination,
            departure_day=departure_day,
            flight_number=flight_number,
            departure_hour=departure_hour,
            arrival_hour=arrival_hour,
            arrival_minute=arrival_minute,
        )
    ]
    if return_flight_number is not None:
        routes.append(
            route(
                origin="BKK",
                destination="SGN",
                departure_day="17",
                flight_number=return_flight_number,
                departure_hour="11",
                arrival_hour="12",
            )
        )

    price = {
        "currencyValue": {"currency": "USD", "amount": amount},
        "numOfDecimalPoint": decimal_points,
    }
    return {
        "id": item_id,
        "flightMetadata": {
            "totalNumStop": "0",
            "tripDuration": "95",
            "airlineIds": ["VJ"],
            "totalCombinedPrice": price,
        },
        "fare": {"display": price},
        "connectingFlightRoutes": routes,
    }


def _selected_result(
    *,
    selected_outbound_key: str | None = "out-1",
    selected_return_key: str | None = "ret-1",
    final_total_amount: Decimal = Decimal("321.09"),
    final_total_currency: str = "USD",
    return_departure_day: str = "17",
) -> TravelokaSelectedRoundTripResult:
    return TravelokaSelectedRoundTripResult(
        outbound_payload={
            "data": {
                "meta": {"searchCompleted": True},
                "searchResults": [
                    _traveloka_search_result(
                        item_id="out-1",
                        amount="11100",
                        flight_number="VJ-801",
                    )
                ],
            }
        },
        return_payload={
            "data": {
                "meta": {"searchCompleted": True},
                "searchResults": [
                    _traveloka_search_result(
                        item_id="ret-1",
                        amount="22200",
                        flight_number="VJ-802",
                        origin="BKK",
                        destination="SGN",
                        departure_day=return_departure_day,
                        departure_hour="11",
                        arrival_hour="12",
                    )
                ],
            }
        },
        selected_outbound_key=selected_outbound_key,
        selected_return_key=selected_return_key,
        final_total_amount=final_total_amount,
        final_total_currency=final_total_currency,
        source_paths=(
            "/api/v2/flight/search/initial",
            "/api/v2/flight/search/poll",
        ),
    )


def _assert_parse_error(
    offers: list[FlightOfferV1],
    errors: list[ErrorV1],
    *,
    capability: str = "exact_one_way",
    item_index: int = 1,
    secret: str | None = None,
) -> None:
    assert offers == []
    assert len(errors) == 1
    error = errors[0]
    assert error.code == ErrorCode.PROVIDER_FAILED
    assert error.details["provider"] == "traveloka"
    assert error.details["capability"] == capability
    assert error.details["failure_type"] == "parse_error"
    assert error.details["item_index"] == item_index
    assert "exception_type" in error.details
    if secret is not None:
        assert secret not in error.model_dump_json()


def _assert_selected_total_unavailable_fallback(amount: Decimal) -> None:
    offers, errors = normalize_selected_round_trip(
        _selected_result(final_total_amount=amount),
        _round_trip_request(),
    )

    assert len(offers) == 1
    assert offers[0].comparable is False
    assert offers[0].actual_return_date is None
    assert [error.details["failure_type"] for error in errors] == [
        "return_details_unavailable",
        "final_round_trip_total_unavailable",
    ]


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
