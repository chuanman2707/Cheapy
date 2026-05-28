"""Skyscanner live provider."""

from __future__ import annotations

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)


class SkyscannerProvider:
    name = "skyscanner"
    capabilities = ("exact_one_way", "exact_round_trip")

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return _skipped_result("exact_one_way")

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        return _skipped_result("exact_round_trip")


def create_provider() -> SkyscannerProvider:
    return SkyscannerProvider()


def _skipped_result(capability: str) -> ProviderResult:
    return ProviderResult(
        provider_name="skyscanner",
        capability=capability,
        status=ProviderStatusCode.SKIPPED,
        offers=[],
        warnings=[],
        errors=[],
        duration_ms=0,
        retryable=False,
    )
