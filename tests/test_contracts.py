"""Contract V1 tests."""

import pytest
from pydantic import ValidationError

from cheapy.models import (
    CandidateFamily,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    PassengersV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
    Severity,
    WarningCode,
    WarningV1,
)


def test_search_request_defaults_to_exact_mode_and_one_adult() -> None:
    request = SearchRequestV1(
        schema_version="1",
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
        return_date=None,
    )

    assert request.search_mode == SearchMode.EXACT
    assert request.passengers == PassengersV1()
    assert request.max_results == 5


def test_search_request_rejects_non_iso_dates() -> None:
    with pytest.raises(ValidationError):
        SearchRequestV1(
            schema_version="1",
            origin="CXR",
            destination="SGN",
            departure_date="10/07/2026",
            return_date=None,
        )

    with pytest.raises(ValidationError):
        SearchRequestV1(
            schema_version="1",
            origin="CXR",
            destination="SGN",
            departure_date="2026-7-10",
            return_date=None,
        )


def test_search_request_rejects_string_passenger_counts() -> None:
    with pytest.raises(ValidationError):
        SearchRequestV1(
            schema_version="1",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            return_date=None,
            passengers={"adults": "1"},
        )


def test_response_uses_offers_as_canonical_source() -> None:
    offer = FlightOfferV1(
        offer_id="google_fli:offer-1",
        price_amount=120.5,
        currency="USD",
        comparable=True,
        rank_within_currency=1,
        global_rank=1,
        provider="google_fli",
        requested_origin="SGN",
        requested_destination="BKK",
        actual_origin="SGN",
        actual_destination="BKK",
        requested_departure_date="2026-07-10",
        actual_departure_date="2026-07-10",
        departure_offset_days=0,
        requested_return_date=None,
        actual_return_date=None,
        return_offset_days=None,
        legs=[
            FlightLegV1(
                origin="SGN",
                destination="BKK",
                departure_time="2026-07-10T09:00:00",
                arrival_time="2026-07-10T10:30:00",
                airline_code="VN",
                flight_number="VN601",
                duration_minutes=90,
            )
        ],
        total_duration_minutes=90,
        stops=0,
        flags=OfferFlagsV1(),
        fare_details_status="not_collected",
    )
    plan = SearchPlanV1(
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
    )
    status = ProviderStatusV1(
        provider_name="google_fli",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        planned_call_count=1,
        executed_call_count=1,
        succeeded_call_count=1,
        failed_call_count=0,
        duration_ms=120,
        warnings=[],
        errors=[],
        retryable=False,
    )
    warning = WarningV1(
        code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
        severity=Severity.WARNING,
        message_en="Fare details were not collected.",
        details={"offer_id": "google_fli:offer-1"},
        retryable=False,
    )

    response = SearchResponseV1(
        schema_version="1",
        status=SearchStatus.SUCCESS,
        request_id="req_123",
        offers=[offer],
        warnings=[warning],
        errors=[],
        provider_statuses=[status],
        search_plan=plan,
        mixed_currency=False,
        currency_groups=[],
        currency_notes=[],
        candidates=None,
    )

    assert response.offers[0].offer_id == "google_fli:offer-1"
    assert response.search_plan.truncated is False
    assert response.candidates is None


def test_search_response_rejects_top_level_search_mode() -> None:
    with pytest.raises(ValidationError):
        SearchResponseV1(
            schema_version="1",
            status="success",
            request_id="req_123",
            offers=[],
            warnings=[],
            errors=[],
            provider_statuses=[],
            search_plan={
                "search_mode": "exact",
                "planned_candidate_count": 0,
                "executed_candidate_count": 0,
                "planned_provider_call_count": 0,
                "executed_provider_call_count": 0,
                "candidate_count_by_family": {},
                "provider_call_count_by_family": {},
                "truncated": False,
                "truncated_families": [],
                "candidate_families": [],
            },
            mixed_currency=False,
            currency_groups=[],
            currency_notes=[],
            candidates=None,
            search_mode="exact",
        )
