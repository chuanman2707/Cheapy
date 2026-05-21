from __future__ import annotations

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactRoundTripRequest, ProviderResult
from scripts.benchmark_traveloka_live_matrix import matrix_record


def test_matrix_record_contains_required_fields() -> None:
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-12",
        return_date="2026-06-17",
    )
    result = ProviderResult(
        provider_name="traveloka",
        capability="exact_round_trip",
        status=ProviderStatusCode.SUCCESS,
        offers=[],
        warnings=[],
        errors=[],
        duration_ms=123,
        retryable=False,
    )

    record = matrix_record(
        run_label="baseline",
        request=request,
        result=result,
        duration_ms=456,
    )

    assert record == {
        "run_label": "baseline",
        "origin": "SGN",
        "destination": "BKK",
        "departure_date": "2026-06-12",
        "return_date": "2026-06-17",
        "status": "success",
        "offer_count": 0,
        "comparable_offer_count": 0,
        "failure_types": [],
        "duration_ms": 456,
    }
