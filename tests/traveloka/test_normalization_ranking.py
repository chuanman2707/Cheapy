from __future__ import annotations

from cheapy.models import FlightOfferV1, OfferFlagsV1
from cheapy.providers.traveloka.normalization.ranking import rank_offers


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
