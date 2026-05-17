from __future__ import annotations

import importlib


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
