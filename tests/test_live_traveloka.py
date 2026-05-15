from __future__ import annotations

import asyncio
from datetime import date, timedelta
import os

import pytest

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactRoundTripRequest
from cheapy.providers.traveloka.provider import create_provider


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CHEAPY_RUN_LIVE_TESTS") != "1",
        reason="Set CHEAPY_RUN_LIVE_TESTS=1 to run live provider smoke tests.",
    ),
]


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
    for offer in result.offers:
        assert offer.provider == "traveloka"
        assert offer.price_amount > 0
        assert len(offer.currency) == 3
        assert offer.currency.isalpha()
        assert offer.currency.isupper()
