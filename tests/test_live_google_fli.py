from __future__ import annotations

import asyncio
from datetime import date, timedelta
import os

import pytest

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.google_fli.provider import create_provider


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CHEAPY_RUN_LIVE_TESTS") != "1",
        reason="Set CHEAPY_RUN_LIVE_TESTS=1 to run live provider smoke tests.",
    ),
]


def test_google_fli_live_smoke_returns_structured_result() -> None:
    provider = create_provider()
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date=(date.today() + timedelta(days=30)).isoformat(),
    )

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.provider_name == "google_fli"
    assert result.capability == "exact_one_way"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    for offer in result.offers:
        assert offer.provider == "google_fli"


def test_google_fli_live_exact_round_trip_smoke_returns_structured_result() -> None:
    provider = create_provider()
    departure_date = date.today() + timedelta(days=30)
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date=departure_date.isoformat(),
        return_date=(departure_date + timedelta(days=7)).isoformat(),
    )

    result = asyncio.run(provider.search_exact_round_trip(request))

    assert result.provider_name == "google_fli"
    assert result.capability == "exact_round_trip"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    for offer in result.offers:
        assert offer.provider == "google_fli"
