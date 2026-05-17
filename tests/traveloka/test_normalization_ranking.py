from __future__ import annotations

from cheapy.models import FlightOfferV1, OfferFlagsV1
from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload
from cheapy.providers.traveloka.normalization.ranking import rank_offers

from .normalization_fixtures import (
    _one_way_request,
    _round_trip_request,
    _segment,
    _traveloka_search_result,
)


def _offer(*, comparable: bool, price: float, offer_id: str) -> FlightOfferV1:
    return FlightOfferV1(
        offer_id=offer_id,
        price_amount=price,
        currency="USD",
        comparable=comparable,
        rank_within_currency=1 if comparable else None,
        global_rank=1 if comparable else None,
        provider="traveloka",
        requested_origin="SGN",
        requested_destination="BKK",
        actual_origin="SGN",
        actual_destination="BKK",
        nearby_origin_distance_km=None,
        nearby_destination_distance_km=None,
        requested_departure_date="2026-07-10",
        actual_departure_date="2026-07-10",
        departure_offset_days=0,
        requested_return_date=None,
        actual_return_date=None,
        return_offset_days=None,
        legs=[],
        total_duration_minutes=0,
        stops=0,
        flags=OfferFlagsV1(
            uses_flexible_departure_date=False,
            uses_flexible_return_date=False,
        ),
        fare_details_status="not_collected",
    )


def test_rank_offers_clears_rank_for_non_comparable_offer() -> None:
    ranked = rank_offers([_offer(comparable=False, price=10, offer_id="b")])

    assert ranked[0].comparable is False
    assert ranked[0].rank_within_currency is None
    assert ranked[0].global_rank is None


def test_normalize_payload_sorts_raw_round_trip_partials_by_price() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [
                _traveloka_search_result(
                    item_id="eva-expensive",
                    amount="430731",
                    flight_number="BR-76",
                ),
                _traveloka_search_result(
                    item_id="qatar-cheapest",
                    amount="166360",
                    flight_number="QR-274",
                ),
                _traveloka_search_result(
                    item_id="qatar-middle",
                    amount="172840",
                    flight_number="QR-284",
                ),
            ],
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert [offer.offer_id for offer in offers] == [
        "traveloka:SGN-BKK:2026-07-10:2026-07-17:qatar-cheapest",
        "traveloka:SGN-BKK:2026-07-10:2026-07-17:qatar-middle",
        "traveloka:SGN-BKK:2026-07-10:2026-07-17:eva-expensive",
    ]
    assert [offer.price_amount for offer in offers] == [1663.6, 1728.4, 4307.31]
    assert all(offer.comparable is False for offer in offers)
    assert all(offer.rank_within_currency is None for offer in offers)
    assert all(offer.global_rank is None for offer in offers)
    assert [error.details["item_index"] for error in errors] == [1, 2, 3]


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
