from __future__ import annotations

from cheapy.models import ErrorCode, ErrorV1, FlightOfferV1
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


def _traveloka_search_result(
    *,
    item_id: str = "tv-search-1",
    amount: str = "29890",
    decimal_points: str = "2",
    flight_number: str = "VJ-801",
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
            origin="SGN",
            destination="BKK",
            departure_day="10",
            flight_number=flight_number,
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


def test_normalize_payload_maps_traveloka_round_trip_search_result() -> None:
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

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:tv-rt-1"
    assert offer.price_amount == 176.0
    assert offer.actual_return_date == "2026-07-17"
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]


def test_normalize_payload_reports_traveloka_search_result_parse_error() -> None:
    secret = "sk_live_traveloka_search_result_secret"
    item = _traveloka_search_result()
    item["debug"] = secret
    item.pop("connectingFlightRoutes")
    payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": [item]}}

    offers, errors = normalize_payload(payload, _one_way_request())

    _assert_parse_error(offers, errors, secret=secret)


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


def test_normalize_payload_discovers_recursive_offer_like_mapping() -> None:
    payload = {
        "data": {
            "unstructuredResults": {
                "groups": [
                    {
                        "id": "nested",
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
    assert [offer.offer_id for offer in offers] == [
        "traveloka:SGN-BKK:2026-07-10:nested"
    ]


def test_normalize_payload_prefers_direct_itineraries_over_recursive_fallback() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "direct",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                }
            ],
            "unstructuredResults": {
                "groups": [
                    {
                        "id": "nested",
                        "price": {"amount": 90.0, "currency": "USD"},
                        "durationMinutes": 95,
                        "stops": 0,
                        "segments": [_segment()],
                    }
                ]
            },
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert [offer.offer_id for offer in offers] == [
        "traveloka:SGN-BKK:2026-07-10:direct"
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
    assert offer.requested_return_date == "2026-07-17"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.actual_return_date == "2026-07-17"
    assert offer.return_offset_days == 0
    assert offer.flags.uses_flexible_departure_date is False
    assert offer.flags.uses_flexible_return_date is False
    assert offer.fare_details_status == "not_collected"
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]


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


def test_normalize_payload_rejects_round_trip_wrong_return_date() -> None:
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

    _assert_parse_error(offers, errors, capability="exact_round_trip")


def test_normalize_payload_rejects_round_trip_without_valid_return_chain() -> None:
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

    _assert_parse_error(
        offers,
        errors,
        capability="exact_round_trip",
        secret=secret,
    )


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
