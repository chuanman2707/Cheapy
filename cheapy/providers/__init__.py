"""Provider foundation for Cheapy."""

from __future__ import annotations

from cheapy.providers.base import (
    FlightProvider,
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)

__all__ = [
    "FlightProvider",
    "ProviderExactOneWayRequest",
    "ProviderExactRoundTripRequest",
    "ProviderResult",
]
