"""Traveloka offer ranking helpers."""

from __future__ import annotations

from cheapy.models import FlightOfferV1


def rank_offers(
    offers: list[FlightOfferV1],
    *,
    sort_non_comparable: bool = False,
) -> list[FlightOfferV1]:
    if sort_non_comparable and all(not offer.comparable for offer in offers):
        offers = sorted(
            offers,
            key=lambda offer: (offer.currency, offer.price_amount, offer.offer_id),
        )

    ranked: list[FlightOfferV1] = []
    comparable_rank = 0
    for offer in offers:
        if not offer.comparable:
            ranked.append(
                offer.model_copy(
                    update={
                        "comparable": False,
                        "rank_within_currency": None,
                        "global_rank": None,
                    }
                )
            )
            continue

        comparable_rank += 1
        ranked.append(
            offer.model_copy(
                update={
                    "comparable": True,
                    "rank_within_currency": comparable_rank,
                    "global_rank": comparable_rank,
                }
            )
        )
    return ranked
