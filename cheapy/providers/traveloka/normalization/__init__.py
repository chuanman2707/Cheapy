"""Traveloka payload normalization package."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cheapy.models import ErrorV1, FlightOfferV1
    from cheapy.providers.base import (
        ProviderExactOneWayRequest,
        ProviderExactRoundTripRequest,
    )
    from cheapy.providers.traveloka.results import TravelokaSelectedRoundTripResult

    ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest

    normalize_payload: Callable[
        [object, ProviderRequest],
        tuple[list[FlightOfferV1], list[ErrorV1]],
    ]
    normalize_selected_round_trip: Callable[
        [TravelokaSelectedRoundTripResult, ProviderExactRoundTripRequest],
        tuple[list[FlightOfferV1], list[ErrorV1]],
    ]


__all__ = ["normalize_payload", "normalize_selected_round_trip"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from cheapy.providers.traveloka.normalization.entrypoints import (
        normalize_payload,
        normalize_selected_round_trip,
    )

    exports = {
        "normalize_payload": normalize_payload,
        "normalize_selected_round_trip": normalize_selected_round_trip,
    }
    globals().update(exports)
    return exports[name]
