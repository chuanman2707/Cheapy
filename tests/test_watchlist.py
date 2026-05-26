from __future__ import annotations

from typing import Any

from cheapy.models import (
    CandidateFamily,
    CurrencyGroupV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchResponseV1,
    SearchStatus,
)
from cheapy.watchlist import build_watchlist_request, evaluate_watchlist


def _watchlist(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 1,
        "name": "CXR to SGN",
        "enabled": True,
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "max_price_amount": 1_300_000.0,
        "currency": "VND",
        "max_stops": 0,
        "max_results": 5,
    }
    data.update(overrides)
    return data


def _offer(**overrides: Any) -> FlightOfferV1:
    data: dict[str, Any] = {
        "offer_id": "fixture:1",
        "price_amount": 1_280_000.0,
        "currency": "VND",
        "comparable": True,
        "rank_within_currency": 1,
        "global_rank": 1,
        "provider": "manual_fixture",
        "requested_origin": "CXR",
        "requested_destination": "SGN",
        "actual_origin": "CXR",
        "actual_destination": "SGN",
        "nearby_origin_distance_km": None,
        "nearby_destination_distance_km": None,
        "requested_departure_date": "2026-07-10",
        "actual_departure_date": "2026-07-10",
        "departure_offset_days": 0,
        "requested_return_date": None,
        "actual_return_date": None,
        "return_offset_days": None,
        "legs": [
            FlightLegV1(
                origin="CXR",
                destination="SGN",
                departure_time="2026-07-10T08:15:00",
                arrival_time="2026-07-10T09:25:00",
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=70,
            )
        ],
        "total_duration_minutes": 70,
        "stops": 0,
        "flags": OfferFlagsV1(),
        "fare_details_status": "not_collected",
    }
    data.update(overrides)
    return FlightOfferV1.model_validate(data)


def _provider_status(**overrides: Any) -> ProviderStatusV1:
    data: dict[str, Any] = {
        "provider_name": "manual_fixture",
        "capability": "exact_one_way",
        "status": ProviderStatusCode.SUCCESS,
        "planned_call_count": 1,
        "executed_call_count": 1,
        "succeeded_call_count": 1,
        "failed_call_count": 0,
        "duration_ms": 12,
        "warnings": [],
        "errors": [],
        "retryable": False,
    }
    data.update(overrides)
    return ProviderStatusV1.model_validate(data)


def _response(**overrides: Any) -> SearchResponseV1:
    offers = [
        FlightOfferV1.model_validate(offer)
        for offer in overrides.pop("offers", [_offer()])
    ]
    mixed_currency = overrides.pop(
        "mixed_currency", len({offer.currency for offer in offers}) > 1
    )
    data: dict[str, Any] = {
        "schema_version": "1",
        "status": SearchStatus.SUCCESS,
        "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
        "offers": offers,
        "warnings": [],
        "errors": [],
        "provider_statuses": [_provider_status()],
        "search_plan": SearchPlanV1(
            search_mode=SearchMode.EXACT,
            planned_candidate_count=1,
            executed_candidate_count=1,
            planned_provider_call_count=1,
            executed_provider_call_count=1,
            candidate_count_by_family={CandidateFamily.EXACT: 1},
            provider_call_count_by_family={CandidateFamily.EXACT: 1},
            truncated=False,
            truncated_families=[],
            candidate_families=[CandidateFamily.EXACT],
        ),
        "mixed_currency": mixed_currency,
        "currency_groups": [
            CurrencyGroupV1(
                currency=currency,
                offer_ids=[
                    offer.offer_id for offer in offers if offer.currency == currency
                ],
            )
            for currency in sorted({offer.currency for offer in offers})
        ],
        "currency_notes": (
            [] if not mixed_currency else ["Currencies are not comparable."]
        ),
        "candidates": None,
    }
    data.update(overrides)
    return SearchResponseV1.model_validate(data)


def _historical_comparison() -> dict[str, float | None]:
    return {"historical_low": None, "latest_price_amount": None}


def test_build_watchlist_request_uses_contract_v1_fields() -> None:
    request = build_watchlist_request(_watchlist())

    assert request.origin == "CXR"
    assert request.destination == "SGN"
    assert request.departure_date == "2026-07-10"
    assert request.return_date is None
    assert request.max_results == 5


def test_evaluate_watchlist_books_when_threshold_met() -> None:
    decision = evaluate_watchlist(
        response=_response(),
        watchlist=_watchlist(max_price_amount=1_300_000.0),
        historical_comparison=_historical_comparison(),
    )

    assert decision["decision"] == "book_now"
    assert decision["best_offer"]["offer_id"] == "fixture:1"
    assert decision["threshold_comparison"]["threshold_met"] is True
    assert decision["provider_confidence"] == "high"


def test_evaluate_watchlist_does_not_book_non_comparable_offer() -> None:
    response = _response(
        offers=[
            _offer(
                offer_id="fixture:non-comparable",
                comparable=False,
                price_amount=999_000.0,
            )
        ]
    )

    decision = evaluate_watchlist(
        response=response,
        watchlist=_watchlist(max_price_amount=1_300_000.0),
        historical_comparison=_historical_comparison(),
    )

    assert decision["decision"] == "watch"
    assert decision["best_offer"]["offer_id"] == "fixture:non-comparable"
    assert decision["threshold_comparison"]["threshold_met"] is True
    assert (
        "Best fare is below the threshold, but is not comparable enough "
        "for a booking decision."
        in decision["rationale"]
    )


def test_evaluate_watchlist_watches_without_threshold() -> None:
    decision = evaluate_watchlist(
        response=_response(),
        watchlist=_watchlist(max_price_amount=None),
        historical_comparison=_historical_comparison(),
    )

    assert decision["decision"] == "watch"
    assert decision["threshold_comparison"]["threshold_met"] is None
    assert "No max price threshold is configured." in decision["rationale"]


def test_evaluate_watchlist_skips_when_currency_cannot_be_compared() -> None:
    decision = evaluate_watchlist(
        response=_response(mixed_currency=True),
        watchlist=_watchlist(currency=None),
        historical_comparison=_historical_comparison(),
    )

    assert decision["decision"] == "skip"
    assert (
        "Mixed currencies cannot be compared without a watchlist currency."
        in decision["rationale"]
    )


def test_evaluate_watchlist_filters_max_stops() -> None:
    response = _response(
        offers=[
            _response()
            .offers[0]
            .model_copy(update={"stops": 1})
            .model_dump(mode="json")
        ]
    )

    decision = evaluate_watchlist(
        response=response,
        watchlist=_watchlist(max_stops=0),
        historical_comparison=_historical_comparison(),
    )

    assert decision["decision"] == "skip"
    assert (
        "No qualifying offer matched the watchlist constraints."
        in decision["rationale"]
    )
