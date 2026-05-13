from __future__ import annotations

from datetime import datetime
from enum import Enum
from types import SimpleNamespace

from cheapy.models import ErrorCode
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.google_fli.normalizer import normalize_flights


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-11",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-12",
        return_date="2026-06-19",
        requested_origin="SGN",
        requested_destination="BKK",
        requested_departure_date="2026-06-11",
        requested_return_date="2026-06-18",
    )


def _leg(
    *,
    airline: str = "VJ",
    flight_number: str = "VJ801",
    origin: str = "SGN",
    destination: str = "BKK",
    departure_datetime: datetime = datetime(2026, 6, 11, 9, 15),
    arrival_datetime: datetime = datetime(2026, 6, 11, 10, 45),
) -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(value=airline),
        flight_number=flight_number,
        departure_airport=SimpleNamespace(value=origin),
        arrival_airport=SimpleNamespace(value=destination),
        departure_datetime=departure_datetime,
        arrival_datetime=arrival_datetime,
        duration=90,
    )


def _return_leg() -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(value="VJ"),
        flight_number="VJ802",
        departure_airport=SimpleNamespace(value="BKK"),
        arrival_airport=SimpleNamespace(value="SGN"),
        departure_datetime=datetime(2026, 6, 19, 11, 15),
        arrival_datetime=datetime(2026, 6, 19, 12, 45),
        duration=90,
    )


def _flight(
    *,
    price: float = 88.5,
    currency: str | None = "USD",
    legs: list[SimpleNamespace] | None = None,
    duration: int = 90,
    stops: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        price=price,
        currency=currency,
        duration=duration,
        stops=stops,
        legs=legs if legs is not None else [_leg()],
    )


def test_normalize_flights_maps_google_fli_result_to_contract_offer() -> None:
    offers, errors = normalize_flights([_flight()], _request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "google_fli:SGN-BKK:2026-06-11:1"
    assert offer.price_amount == 88.5
    assert offer.currency == "USD"
    assert offer.provider == "google_fli"
    assert offer.requested_origin == "SGN"
    assert offer.requested_destination == "BKK"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.requested_departure_date == "2026-06-11"
    assert offer.actual_departure_date == "2026-06-11"
    assert offer.total_duration_minutes == 90
    assert offer.stops == 0
    assert offer.fare_details_status == "not_collected"
    assert offer.flags.baggage_unknown is True
    assert [(leg.airline_code, leg.flight_number) for leg in offer.legs] == [
        ("VJ", "VJ801")
    ]


def test_normalize_flights_maps_round_trip_dates_and_flags() -> None:
    offers, errors = normalize_flights(
        [
            _flight(
                legs=[
                    _leg(
                        departure_datetime=datetime(2026, 6, 12, 9, 15),
                        arrival_datetime=datetime(2026, 6, 12, 10, 45),
                    ),
                    _return_leg(),
                ],
                duration=180,
            )
        ],
        _round_trip_request(),
    )

    assert errors == []
    offer = offers[0]
    assert offer.provider == "google_fli"
    assert offer.requested_departure_date == "2026-06-11"
    assert offer.actual_departure_date == "2026-06-12"
    assert offer.departure_offset_days == 1
    assert offer.requested_return_date == "2026-06-18"
    assert offer.actual_return_date == "2026-06-19"
    assert offer.return_offset_days == 1
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.flags.uses_flexible_departure_date is True
    assert offer.flags.uses_flexible_return_date is True
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]


def test_normalize_flights_maps_round_trip_tuple_result() -> None:
    outbound = _flight(legs=[_leg()], duration=90)
    inbound = _flight(legs=[_return_leg()], duration=90, currency=None)

    offers, errors = normalize_flights([(outbound, inbound)], _round_trip_request())

    assert errors == []
    assert len(offers) == 1
    assert [(leg.origin, leg.destination) for leg in offers[0].legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]
    assert offers[0].currency == "USD"
    assert offers[0].actual_return_date == "2026-06-19"


def test_normalize_flights_uses_upstream_enum_names_for_codes() -> None:
    class FakeAirport(Enum):
        SGN = "Tan Son Nhat International Airport"
        BKK = "Suvarnabhumi Airport"

    class FakeAirline(Enum):
        VJ = "VietJet Air"

    leg = SimpleNamespace(
        airline=FakeAirline.VJ,
        flight_number="807",
        departure_airport=FakeAirport.SGN,
        arrival_airport=FakeAirport.BKK,
        departure_datetime=datetime(2026, 6, 11, 13, 15),
        arrival_datetime=datetime(2026, 6, 11, 14, 45),
        duration=90,
    )

    offers, errors = normalize_flights([_flight(legs=[leg])], _request())

    assert errors == []
    assert len(offers) == 1
    assert offers[0].actual_origin == "SGN"
    assert offers[0].actual_destination == "BKK"
    assert [(leg.airline_code, leg.flight_number) for leg in offers[0].legs] == [
        ("VJ", "807")
    ]


def test_normalize_flights_uses_configured_currency_when_result_currency_is_missing() -> None:
    offers, errors = normalize_flights(
        [_flight(currency=None)],
        _request(),
        configured_currency="VND",
    )

    assert errors == []
    assert offers[0].currency == "VND"


def test_normalize_flights_uses_configured_currency_when_result_currency_is_malformed() -> None:
    offers, errors = normalize_flights(
        [_flight(currency="US$")],
        _request(),
        configured_currency="VND",
    )

    assert errors == []
    assert offers[0].currency == "VND"


def test_normalize_flights_fails_item_when_currency_is_unavailable() -> None:
    offers, errors = normalize_flights([_flight(currency=None)], _request())

    assert offers == []
    assert len(errors) == 1
    error = errors[0]
    assert error.code == ErrorCode.PROVIDER_FAILED
    assert error.details == {
        "provider": "google_fli",
        "capability": "exact_one_way",
        "failure_type": "currency_unavailable",
        "item_index": 1,
    }
    assert error.retryable is False


def test_normalize_flights_fails_item_when_currency_is_malformed_and_unconfigured() -> None:
    offers, errors = normalize_flights([_flight(currency="US$")], _request())

    assert offers == []
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "currency_unavailable"


def test_normalize_flights_skips_malformed_item_without_leaking_payload() -> None:
    bad_flight = SimpleNamespace(
        price="secret-price",
        currency="USD",
        duration=90,
        stops=0,
        legs=[],
        raw_payload="secret raw payload",
    )

    offers, errors = normalize_flights([bad_flight], _request())

    assert offers == []
    assert len(errors) == 1
    payload = errors[0].model_dump_json()
    assert errors[0].details["failure_type"] == "parse_error"
    assert "secret raw payload" not in payload
    assert "secret-price" not in payload


def test_normalize_flights_converts_contract_validation_errors_to_sanitized_parse_errors() -> None:
    offers, errors = normalize_flights([_flight(price=-1)], _request())

    assert offers == []
    assert len(errors) == 1
    payload = errors[0].model_dump_json()
    assert errors[0].details["failure_type"] == "parse_error"
    assert "-1" not in payload


def test_normalize_flights_ranks_successful_offers_contiguously_after_skipped_item() -> None:
    bad_flight = _flight(legs=[])

    offers, errors = normalize_flights([bad_flight, _flight()], _request())

    assert len(errors) == 1
    assert errors[0].details["item_index"] == 1
    assert [offer.offer_id for offer in offers] == ["google_fli:SGN-BKK:2026-06-11:2"]
    assert [offer.rank_within_currency for offer in offers] == [1]
    assert [offer.global_rank for offer in offers] == [1]


def test_normalize_flights_marks_mixed_currency_offers_not_globally_comparable() -> None:
    offers, errors = normalize_flights(
        [
            _flight(currency="USD"),
            _flight(currency="VND"),
        ],
        _request(),
    )

    assert errors == []
    assert [offer.currency for offer in offers] == ["USD", "VND"]
    assert [offer.comparable for offer in offers] == [False, False]
    assert [offer.rank_within_currency for offer in offers] == [1, 1]
    assert [offer.global_rank for offer in offers] == [None, None]
