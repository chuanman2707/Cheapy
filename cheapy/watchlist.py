"""Pure watchlist decision helpers."""

from __future__ import annotations

from typing import Any

from cheapy.models import (
    FlightOfferV1,
    ProviderStatusCode,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
)


def build_watchlist_request(watchlist: dict[str, Any]) -> SearchRequestV1:
    """Build a Contract V1 search request from a stored watchlist."""

    return SearchRequestV1.model_validate(
        {
            "schema_version": "1",
            "origin": watchlist["origin"],
            "destination": watchlist["destination"],
            "departure_date": watchlist["departure_date"],
            "return_date": watchlist["return_date"],
            "max_results": watchlist["max_results"],
        }
    )


def evaluate_watchlist(
    *,
    response: SearchResponseV1,
    watchlist: dict[str, Any],
    historical_comparison: dict[str, Any],
) -> dict[str, Any]:
    """Return the manual watchlist decision for one search response."""

    rationale: list[str] = []
    if response.status == SearchStatus.FAILED:
        rationale.append("Search failed, so no booking decision can be made.")
        return _decision_payload(
            "skip", None, watchlist, historical_comparison, response, rationale
        )

    if response.mixed_currency and watchlist.get("currency") is None:
        rationale.append(
            "Mixed currencies cannot be compared without a watchlist currency."
        )
        return _decision_payload(
            "skip", None, watchlist, historical_comparison, response, rationale
        )

    offers = _qualifying_offers(response.offers, watchlist)
    if not offers:
        rationale.append("No qualifying offer matched the watchlist constraints.")
        return _decision_payload(
            "skip", None, watchlist, historical_comparison, response, rationale
        )

    best_offer = min(
        offers,
        key=lambda offer: (not offer.comparable, offer.price_amount, offer.offer_id),
    )
    threshold = watchlist.get("max_price_amount")
    if threshold is None:
        rationale.append("No max price threshold is configured.")
        return _decision_payload(
            "watch", best_offer, watchlist, historical_comparison, response, rationale
        )

    if best_offer.price_amount <= float(threshold):
        rationale.append("Best fare is at or below the configured threshold.")
        return _decision_payload(
            "book_now",
            best_offer,
            watchlist,
            historical_comparison,
            response,
            rationale,
        )

    rationale.append("Best fare is above the configured threshold.")
    return _decision_payload(
        "watch", best_offer, watchlist, historical_comparison, response, rationale
    )


def provider_confidence(response: SearchResponseV1) -> str:
    """Summarize confidence from top-level and provider statuses."""

    if response.status == SearchStatus.FAILED or not response.offers:
        return "low"
    failed_or_retryable = [
        status
        for status in response.provider_statuses
        if status.status == ProviderStatusCode.FAILED or status.retryable
    ]
    if failed_or_retryable:
        return "medium"
    return "high"


def _qualifying_offers(
    offers: list[FlightOfferV1],
    watchlist: dict[str, Any],
) -> list[FlightOfferV1]:
    currency = watchlist.get("currency")
    max_stops = watchlist.get("max_stops")
    result: list[FlightOfferV1] = []
    for offer in offers:
        if currency is not None and offer.currency != currency:
            continue
        if max_stops is not None and offer.stops > int(max_stops):
            continue
        result.append(offer)
    return result


def _decision_payload(
    decision: str,
    best_offer: FlightOfferV1 | None,
    watchlist: dict[str, Any],
    historical_comparison: dict[str, Any],
    response: SearchResponseV1,
    rationale: list[str],
) -> dict[str, Any]:
    threshold = watchlist.get("max_price_amount")
    best_price = best_offer.price_amount if best_offer is not None else None
    threshold_met = None
    if threshold is not None and best_price is not None:
        threshold_met = best_price <= float(threshold)

    return {
        "decision": decision,
        "best_offer": _best_offer_summary(best_offer),
        "threshold_comparison": {
            "max_price_amount": threshold,
            "best_price_amount": best_price,
            "currency": (
                best_offer.currency
                if best_offer is not None
                else watchlist.get("currency")
            ),
            "threshold_met": threshold_met,
        },
        "historical_comparison": historical_comparison,
        "provider_confidence": provider_confidence(response),
        "rationale": rationale,
    }


def _best_offer_summary(offer: FlightOfferV1 | None) -> dict[str, Any] | None:
    if offer is None:
        return None
    return {
        "offer_id": offer.offer_id,
        "provider": offer.provider,
        "price_amount": offer.price_amount,
        "currency": offer.currency,
        "stops": offer.stops,
        "total_duration_minutes": offer.total_duration_minutes,
        "actual_origin": offer.actual_origin,
        "actual_destination": offer.actual_destination,
        "actual_departure_date": offer.actual_departure_date,
        "actual_return_date": offer.actual_return_date,
    }
