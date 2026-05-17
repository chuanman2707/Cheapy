from __future__ import annotations

import asyncio
from datetime import date, timedelta
import os

import pytest

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactRoundTripRequest
from cheapy.providers.traveloka.provider import create_provider


pytestmark = pytest.mark.live


_ALLOWED_TRAVELOKA_LIVE_FAILURE_TYPES = {
    "return_details_unavailable",
    "parse_error",
    "currency_unavailable",
    "browser_unavailable",
    "navigation_failed",
    "invalid_json",
    "unsupported_response",
    "unexpected_error",
    "partial_failure",
    "outbound_selection_unavailable",
    "selected_outbound_binding_unavailable",
    "return_capture_timeout",
    "return_selection_unavailable",
    "selected_return_binding_unavailable",
    "final_round_trip_total_unavailable",
    "timeout",
    "blocked",
    "rate_limited",
    "transport_error",
    "no_usable_outbound_data",
}


def test_traveloka_live_failure_allow_list_covers_safe_structured_failures() -> None:
    # Keep the opt-in live smoke bounded while accepting safe provider- and
    # normalizer-level failures that real Traveloka runs can produce.
    expected_failure_types = {
        "return_details_unavailable",
        "parse_error",
        "currency_unavailable",
        "browser_unavailable",
        "navigation_failed",
        "invalid_json",
        "unsupported_response",
        "unexpected_error",
        "partial_failure",
        "outbound_selection_unavailable",
        "selected_outbound_binding_unavailable",
        "return_capture_timeout",
        "return_selection_unavailable",
        "selected_return_binding_unavailable",
        "final_round_trip_total_unavailable",
        "timeout",
        "blocked",
        "rate_limited",
        "transport_error",
        "no_usable_outbound_data",
    }

    assert _ALLOWED_TRAVELOKA_LIVE_FAILURE_TYPES == expected_failure_types


@pytest.mark.skipif(
    os.environ.get("CHEAPY_RUN_LIVE_TESTS") != "1",
    reason="Set CHEAPY_RUN_LIVE_TESTS=1 to run live provider smoke tests.",
)
def test_traveloka_live_exact_round_trip_smoke_returns_structured_result() -> None:
    provider = create_provider()
    departure_date = date.today() + timedelta(days=30)
    request = ProviderExactRoundTripRequest(
        origin="CXR",
        destination="HAN",
        departure_date=departure_date.isoformat(),
        return_date=(departure_date + timedelta(days=5)).isoformat(),
    )

    result = asyncio.run(provider.search_exact_round_trip(request))

    assert result.provider_name == "traveloka"
    assert result.capability == "exact_round_trip"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    if result.status == ProviderStatusCode.SUCCESS:
        assert len(result.offers) == 1
        assert result.offers[0].comparable is True
        assert result.offers[0].actual_return_date == request.return_date
        assert result.errors == []
    else:
        failure_types = {
            str(error.details.get("failure_type")) for error in result.errors
        }
        assert failure_types
        assert failure_types <= _ALLOWED_TRAVELOKA_LIVE_FAILURE_TYPES
        for offer in result.offers:
            assert offer.provider == "traveloka"
            assert offer.comparable is False
            assert offer.rank_within_currency is None
            assert offer.global_rank is None
