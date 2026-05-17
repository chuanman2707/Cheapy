from __future__ import annotations

from decimal import Decimal

from cheapy.providers.base import ProviderExactRoundTripRequest
from cheapy.providers.traveloka.normalization.selected import (
    normalize_selected_round_trip,
)
from cheapy.providers.traveloka.results import TravelokaSelectedRoundTripResult


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


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
        "connectingFlightRoutes": [
            route(
                origin=origin,
                destination=destination,
                departure_day=departure_day,
                flight_number=flight_number,
                departure_hour=departure_hour,
                arrival_hour=arrival_hour,
                arrival_minute=arrival_minute,
            )
        ],
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


def test_normalize_selected_round_trip_uses_final_total_and_marks_comparable() -> None:
    offers, errors = normalize_selected_round_trip(
        _selected_result(),
        _round_trip_request(),
    )

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:selected:out-1:ret-1"
    assert offer.price_amount == 321.09
    assert offer.currency == "USD"
    assert offer.comparable is True
    assert offer.rank_within_currency == 1
    assert offer.global_rank == 1
    assert offer.actual_return_date == "2026-07-17"
    assert offer.stops == 0
    assert [leg.flight_number for leg in offer.legs] == ["VJ-801", "VJ-802"]


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


def test_normalize_selected_round_trip_preserves_full_contract_fields() -> None:
    offers, errors = normalize_selected_round_trip(
        _selected_result(),
        _round_trip_request(),
    )

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:selected:out-1:ret-1"
    assert offer.price_amount == 321.09
    assert offer.currency == "USD"
    assert offer.comparable is True
    assert offer.rank_within_currency == 1
    assert offer.global_rank == 1
    assert offer.actual_departure_date == "2026-07-10"
    assert offer.actual_return_date == "2026-07-17"
    assert offer.return_offset_days == 0
    assert [(leg.origin, leg.destination, leg.flight_number) for leg in offer.legs] == [
        ("SGN", "BKK", "VJ-801"),
        ("BKK", "SGN", "VJ-802"),
    ]


def test_normalize_selected_round_trip_falls_back_when_return_key_is_missing() -> None:
    offers, errors = normalize_selected_round_trip(
        _selected_result(selected_return_key=None),
        _round_trip_request(),
    )

    assert len(offers) == 1
    assert offers[0].comparable is False
    assert offers[0].actual_return_date is None
    assert [error.details["failure_type"] for error in errors] == [
        "return_details_unavailable",
        "selected_return_binding_unavailable",
    ]


def test_normalize_selected_round_trip_rejects_non_selected_currency() -> None:
    offers, errors = normalize_selected_round_trip(
        _selected_result(final_total_currency=""),
        _round_trip_request(),
    )

    assert len(offers) == 1
    assert offers[0].comparable is False
    assert errors[-1].details["failure_type"] == "final_round_trip_total_unavailable"


def test_normalize_selected_round_trip_rejects_negative_final_total() -> None:
    _assert_selected_total_unavailable_fallback(Decimal("-1"))


def test_normalize_selected_round_trip_rejects_nan_final_total() -> None:
    _assert_selected_total_unavailable_fallback(Decimal("NaN"))


def test_normalize_selected_round_trip_rejects_infinite_final_total() -> None:
    _assert_selected_total_unavailable_fallback(Decimal("Infinity"))


def test_normalize_selected_round_trip_falls_back_when_return_date_is_wrong() -> None:
    offers, errors = normalize_selected_round_trip(
        _selected_result(return_departure_day="18"),
        _round_trip_request(),
    )

    assert len(offers) == 1
    assert offers[0].comparable is False
    assert offers[0].actual_return_date is None
    assert [error.details["failure_type"] for error in errors] == [
        "return_details_unavailable",
        "selected_return_binding_unavailable",
    ]


def test_normalize_payload_maps_traveloka_round_trip_search_result_as_unselected_partial() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [
                _traveloka_search_result(
                    item_id="tv-rt-1",
                    amount="17600",
                    return_flight_number="VJ-802",
                )
            ],
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:tv-rt-1"
    assert offer.price_amount == 176.0
    assert offer.comparable is False
    assert offer.rank_within_currency is None
    assert offer.global_rank is None
    assert offer.actual_return_date is None
    assert offer.return_offset_days is None
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [("SGN", "BKK")]
    assert len(errors) == 1
    assert [error.details["failure_type"] for error in errors] == [
        "return_details_unavailable"
    ]


def test_normalize_payload_maps_priced_round_trip_when_return_details_are_absent() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [
                _traveloka_search_result(
                    item_id="tv-rt-priced-outbound",
                    amount="18778",
                )
            ],
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == (
        "traveloka:SGN-BKK:2026-07-10:2026-07-17:tv-rt-priced-outbound"
    )
    assert offer.price_amount == 187.78
    assert offer.comparable is False
    assert offer.rank_within_currency is None
    assert offer.global_rank is None
    assert offer.requested_return_date == "2026-07-17"
    assert offer.actual_return_date is None
    assert offer.return_offset_days is None
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [("SGN", "BKK")]
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "return_details_unavailable"
    assert errors[0].details["capability"] == "exact_round_trip"


def test_normalize_payload_reports_missing_return_details_per_offer() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [
                _traveloka_search_result(item_id="tv-rt-priced-outbound-1"),
                _traveloka_search_result(item_id="tv-rt-priced-outbound-2"),
            ],
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert len(offers) == 2
    assert all(offer.comparable is False for offer in offers)
    assert [error.details["failure_type"] for error in errors] == [
        "return_details_unavailable",
        "return_details_unavailable",
    ]
    assert [error.details["item_index"] for error in errors] == [1, 2]


def test_normalize_payload_maps_legacy_round_trip_itinerary_as_unselected_partial() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "legacy-rt",
                    "price": {"amount": 240.0, "currency": "USD"},
                    "durationMinutes": 190,
                    "stops": 1,
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

    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:legacy-rt"
    assert offer.comparable is False
    assert offer.rank_within_currency is None
    assert offer.global_rank is None
    assert offer.actual_return_date is None
    assert offer.return_offset_days is None
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [("SGN", "BKK")]
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "return_details_unavailable"


def test_normalize_payload_maps_outbound_only_legacy_itinerary_as_unselected_partial() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "_traveloka_search_result": True,
                    "id": "legacy-outbound-only",
                    "price": {"amount": 187.78, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == (
        "traveloka:SGN-BKK:2026-07-10:2026-07-17:legacy-outbound-only"
    )
    assert offer.comparable is False
    assert offer.actual_return_date is None
    assert offer.return_offset_days is None
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [("SGN", "BKK")]
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "return_details_unavailable"


def test_normalize_payload_maps_legacy_round_trip_offer_as_unselected_partial() -> None:
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

    assert len(offers) == 1
    offer = offers[0]
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "return_details_unavailable"
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:tv-rt-1"
    assert offer.requested_return_date == "2026-07-17"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.actual_return_date is None
    assert offer.return_offset_days is None
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert offer.comparable is False
    assert offer.rank_within_currency is None
    assert offer.global_rank is None
    assert offer.flags.uses_flexible_departure_date is False
    assert offer.flags.uses_flexible_return_date is False
    assert offer.fare_details_status == "not_collected"
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [("SGN", "BKK")]


def test_normalize_payload_ignores_unselected_return_date_mismatch_for_partial() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "wrong-return-date",
                    "price": {"amount": 176.0, "currency": "USD"},
                    "durationMinutes": 190,
                    "stops": 0,
                    "segments": [
                        _segment(),
                        _segment(
                            origin="BKK",
                            destination="SGN",
                            departure_time="2026-07-18T11:00:00",
                            arrival_time="2026-07-18T12:35:00",
                            flight_number="VJ802",
                        ),
                    ],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert len(offers) == 1
    assert offers[0].actual_return_date is None
    assert offers[0].return_offset_days is None
    assert offers[0].comparable is False
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "return_details_unavailable"


def test_normalize_payload_maps_missing_return_chain_as_unselected_partial() -> None:
    secret = "sk_live_traveloka_missing_return_secret"
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "missing-return",
                    "debug": secret,
                    "price": {"amount": 176.0, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert len(offers) == 1
    assert offers[0].offer_id == (
        "traveloka:SGN-BKK:2026-07-10:2026-07-17:missing-return"
    )
    assert offers[0].comparable is False
    assert len(errors) == 1
    assert secret not in errors[0].model_dump_json()
    assert errors[0].details["failure_type"] == "return_details_unavailable"
