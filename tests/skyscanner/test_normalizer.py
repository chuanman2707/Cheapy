from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner.adapter import (
    SkyscannerItineraryCandidate,
    SkyscannerLegCandidate,
)
from cheapy.providers.skyscanner.normalizer import normalize_candidates


def _leg(
    origin: str = "SIN",
    destination: str = "SGN",
    departure_time: str = "2026-06-11T09:15:00",
    arrival_time: str = "2026-06-11T10:45:00",
    airline_code: str = "VJ",
    flight_number: str = "VJ814",
    duration_minutes: int = 90,
) -> SkyscannerLegCandidate:
    return SkyscannerLegCandidate(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=airline_code,
        flight_number=flight_number,
        duration_minutes=duration_minutes,
    )


def _candidate(
    *legs: SkyscannerLegCandidate,
    item_id: str = "itinerary-1",
    price_amount: float = 220.96,
    currency: str = "SGD",
    stops: int = 0,
) -> SkyscannerItineraryCandidate:
    candidate_legs = tuple(legs) if legs else (_leg(),)
    return SkyscannerItineraryCandidate(
        item_id=item_id,
        price_amount=price_amount,
        currency=currency,
        legs=candidate_legs,
        total_duration_minutes=sum(leg.duration_minutes for leg in candidate_legs),
        stops=stops,
    )


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SIN",
        destination="SGN",
        departure_date="2026-06-11",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SIN",
        destination="SGN",
        departure_date="2026-06-11",
        return_date="2026-06-18",
    )


def test_normalize_one_way_candidate_to_contract_offer() -> None:
    offers, errors = normalize_candidates([_candidate()], _one_way_request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "skyscanner:SIN-SGN:2026-06-11:itinerary-1"
    assert offer.provider == "skyscanner"
    assert offer.price_amount == 220.96
    assert offer.currency == "SGD"
    assert offer.public_search_url is None
    assert offer.actual_origin == "SIN"
    assert offer.actual_destination == "SGN"
    assert offer.actual_departure_date == "2026-06-11"
    assert offer.actual_return_date is None
    assert offer.total_duration_minutes == 90
    assert offer.stops == 0
    assert offer.legs[0].flight_number == "VJ814"


def test_normalize_round_trip_candidate_sets_return_date_and_legs() -> None:
    outbound = _leg()
    inbound = _leg(
        origin="SGN",
        destination="SIN",
        departure_time="2026-06-18T12:00:00",
        arrival_time="2026-06-18T15:30:00",
        airline_code="VJ",
        flight_number="VJ815",
        duration_minutes=210,
    )

    offers, errors = normalize_candidates(
        [_candidate(outbound, inbound)],
        _round_trip_request(),
    )

    assert errors == []
    offer = offers[0]
    assert offer.offer_id == "skyscanner:SIN-SGN:2026-06-11:2026-06-18:itinerary-1"
    assert offer.actual_return_date == "2026-06-18"
    assert offer.return_offset_days == 0
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SIN", "SGN"),
        ("SGN", "SIN"),
    ]
    assert offer.total_duration_minutes == 300


def test_normalize_candidate_with_no_legs_returns_parse_error() -> None:
    candidate = SkyscannerItineraryCandidate(
        item_id="broken",
        price_amount=220.96,
        currency="SGD",
        legs=(),
        total_duration_minutes=0,
        stops=0,
    )

    offers, errors = normalize_candidates([candidate], _one_way_request())

    assert offers == []
    assert len(errors) == 1
    assert errors[0].details["provider"] == "skyscanner"
    assert errors[0].details["failure_type"] == "parse_error"


def test_normalize_candidate_sets_not_collected_fare_details_and_no_public_url() -> None:
    offers, errors = normalize_candidates([_candidate()], _one_way_request())

    assert errors == []
    offer = offers[0]
    assert offer.fare_details_status == "not_collected"
    assert offer.public_search_url is None


def test_normalize_candidate_sets_flexible_date_flags_from_offsets() -> None:
    request = ProviderExactRoundTripRequest(
        origin="SIN",
        destination="SGN",
        departure_date="2026-06-12",
        return_date="2026-06-19",
        requested_departure_date="2026-06-11",
        requested_return_date="2026-06-18",
    )
    outbound = _leg(departure_time="2026-06-12T09:15:00")
    inbound = _leg(
        origin="SGN",
        destination="SIN",
        departure_time="2026-06-19T12:00:00",
        arrival_time="2026-06-19T15:30:00",
        flight_number="VJ815",
        duration_minutes=210,
    )

    offers, errors = normalize_candidates([_candidate(outbound, inbound)], request)

    assert errors == []
    offer = offers[0]
    assert offer.departure_offset_days == 1
    assert offer.return_offset_days == 1
    assert offer.flags.uses_flexible_departure_date is True
    assert offer.flags.uses_flexible_return_date is True


def test_normalize_candidates_ranks_single_currency_offers_globally_comparable() -> None:
    offers, errors = normalize_candidates(
        [
            _candidate(item_id="itinerary-1", currency="SGD"),
            _candidate(item_id="itinerary-2", currency="SGD"),
        ],
        _one_way_request(),
    )

    assert errors == []
    assert [offer.comparable for offer in offers] == [True, True]
    assert [offer.rank_within_currency for offer in offers] == [1, 2]
    assert [offer.global_rank for offer in offers] == [1, 2]


def test_normalize_candidates_ranks_multi_currency_offers_per_currency_only() -> None:
    offers, errors = normalize_candidates(
        [
            _candidate(item_id="sgd-1", currency="SGD"),
            _candidate(item_id="usd-1", currency="USD"),
            _candidate(item_id="sgd-2", currency="SGD"),
        ],
        _one_way_request(),
    )

    assert errors == []
    assert [offer.comparable for offer in offers] == [False, False, False]
    assert [offer.rank_within_currency for offer in offers] == [1, 1, 2]
    assert [offer.global_rank for offer in offers] == [None, None, None]


def test_normalize_candidate_error_details_are_sanitized() -> None:
    candidate = SkyscannerItineraryCandidate(
        item_id="broken",
        price_amount=220.96,
        currency="SGD",
        legs=(),
        total_duration_minutes=0,
        stops=0,
    )

    offers, errors = normalize_candidates([candidate], _one_way_request())

    assert offers == []
    assert len(errors) == 1
    payload = errors[0].model_dump_json()
    assert errors[0].details == {
        "provider": "skyscanner",
        "failure_type": "parse_error",
        "item_id": "broken",
        "exception_type": "ValueError",
    }
    assert "/transport_deeplink/" not in payload
    assert "cookie" not in payload.lower()
    assert "header" not in payload.lower()
    assert "raw" not in payload.lower()
    assert "challenge" not in payload.lower()
