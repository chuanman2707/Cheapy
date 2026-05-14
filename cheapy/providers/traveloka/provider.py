"""Traveloka live research provider."""

from __future__ import annotations

from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)


PROVIDER_NAME = "traveloka"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"


class TravelokaProvider:
    """Live provider backed by a conservative Traveloka HTTP research adapter."""

    name = PROVIDER_NAME
    capabilities = (EXACT_ONE_WAY_CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return _adapter_unavailable_result(EXACT_ONE_WAY_CAPABILITY)

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        return _adapter_unavailable_result(EXACT_ROUND_TRIP_CAPABILITY)


def create_provider() -> TravelokaProvider:
    return TravelokaProvider()


def _adapter_unavailable_result(capability: str) -> ProviderResult:
    started = perf_counter()
    return ProviderResult(
        provider_name=PROVIDER_NAME,
        capability=capability,
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Traveloka provider adapter is unavailable.",
                details={
                    "provider": PROVIDER_NAME,
                    "capability": capability,
                    "failure_type": "adapter_unavailable",
                },
                retryable=False,
            )
        ],
        duration_ms=max(0, round((perf_counter() - started) * 1000)),
        retryable=False,
    )
