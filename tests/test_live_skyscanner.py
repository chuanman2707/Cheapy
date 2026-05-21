from __future__ import annotations

import asyncio
from datetime import date, timedelta
import os

import pytest

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner.provider import create_provider


pytestmark = pytest.mark.live


_ALLOWED_SKYSCANNER_LIVE_FAILURE_TYPES = {
    "autosuggest_http_error",
    "autosuggest_parse_error",
    "autosuggest_transport_error",
    "blocked",
    "browserless_bootstrap_failed",
    "browserless_cookie_unavailable",
    "entity_ambiguous",
    "entity_not_found",
    "no_usable_results",
    "parse_error",
    "rate_limited",
    "search_http_error",
    "search_incomplete",
    "search_parse_error",
    "search_transport_error",
    "timeout",
    "transport_error",
    "unexpected_error",
}


def test_skyscanner_live_failure_allow_list_covers_safe_structured_failures() -> None:
    expected_failure_types = {
        "autosuggest_http_error",
        "autosuggest_parse_error",
        "autosuggest_transport_error",
        "blocked",
        "browserless_bootstrap_failed",
        "browserless_cookie_unavailable",
        "entity_ambiguous",
        "entity_not_found",
        "no_usable_results",
        "parse_error",
        "rate_limited",
        "search_http_error",
        "search_incomplete",
        "search_parse_error",
        "search_transport_error",
        "timeout",
        "transport_error",
        "unexpected_error",
    }

    assert _ALLOWED_SKYSCANNER_LIVE_FAILURE_TYPES == expected_failure_types


@pytest.mark.skipif(
    os.environ.get("CHEAPY_RUN_LIVE_TESTS") != "1"
    or not os.environ.get("BROWSERLESS_TOKEN", "").strip(),
    reason=(
        "Set CHEAPY_RUN_LIVE_TESTS=1 and BROWSERLESS_TOKEN to run Skyscanner "
        "live provider smoke tests."
    ),
)
def test_skyscanner_live_exact_one_way_smoke_returns_structured_result() -> None:
    provider = create_provider()
    departure_date = date.today() + timedelta(days=30)
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date=departure_date.isoformat(),
    )

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.provider_name == "skyscanner"
    assert result.capability == "exact_one_way"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    if result.status == ProviderStatusCode.SUCCESS:
        assert result.errors == []
    else:
        _assert_safe_failure_types(result)
    for offer in result.offers:
        assert offer.provider == "skyscanner"
        assert offer.actual_departure_date == request.departure_date


@pytest.mark.skipif(
    os.environ.get("CHEAPY_RUN_LIVE_TESTS") != "1"
    or not os.environ.get("BROWSERLESS_TOKEN", "").strip(),
    reason=(
        "Set CHEAPY_RUN_LIVE_TESTS=1 and BROWSERLESS_TOKEN to run Skyscanner "
        "live provider smoke tests."
    ),
)
def test_skyscanner_live_exact_round_trip_smoke_returns_structured_result() -> None:
    provider = create_provider()
    departure_date = date.today() + timedelta(days=30)
    return_date = departure_date + timedelta(days=7)
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date=departure_date.isoformat(),
        return_date=return_date.isoformat(),
    )

    result = asyncio.run(provider.search_exact_round_trip(request))

    assert result.provider_name == "skyscanner"
    assert result.capability == "exact_round_trip"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    if result.status == ProviderStatusCode.SUCCESS:
        assert result.errors == []
    else:
        _assert_safe_failure_types(result)
    for offer in result.offers:
        assert offer.provider == "skyscanner"
        assert offer.actual_departure_date == request.departure_date
        assert offer.actual_return_date == request.return_date


def _assert_safe_failure_types(result: object) -> None:
    errors = getattr(result, "errors")
    failure_types = {str(error.details.get("failure_type")) for error in errors}
    assert failure_types
    assert failure_types <= _ALLOWED_SKYSCANNER_LIVE_FAILURE_TYPES
