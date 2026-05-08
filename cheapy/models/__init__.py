"""Public model exports for Cheapy."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_CONTRACT_EXPORTS = {
    "AirportCandidateV1",
    "CandidateFamily",
    "CurrencyGroupV1",
    "ErrorCode",
    "ErrorV1",
    "FlightLegV1",
    "FlightOfferV1",
    "OfferFlagsV1",
    "PassengersV1",
    "ProviderStatusCode",
    "ProviderStatusV1",
    "SearchMode",
    "SearchPlanV1",
    "SearchRequestV1",
    "SearchResponseV1",
    "SearchStatus",
    "Severity",
    "WarningCode",
    "WarningV1",
}

__all__ = sorted(_CONTRACT_EXPORTS)


def __getattr__(name: str) -> Any:
    """Lazily expose Contract V1 models once the contracts module exists."""
    if name not in _CONTRACT_EXPORTS:
        raise AttributeError(f"module 'cheapy.models' has no attribute {name!r}")

    try:
        contracts = import_module("cheapy.models.contracts")
    except ModuleNotFoundError as exc:
        if exc.name != "cheapy.models.contracts":
            raise
        raise ImportError(
            f"{name!r} is unavailable until cheapy.models.contracts is created"
        ) from exc

    value = getattr(contracts, name)
    globals()[name] = value
    return value
