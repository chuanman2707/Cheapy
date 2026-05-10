"""Provider-local request and result models."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cheapy.models import (
    ErrorV1,
    FlightOfferV1,
    PassengersV1,
    ProviderStatusCode,
    WarningV1,
)

_YYYY_MM_DD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProviderExactOneWayRequest(_ProviderModel):
    """Provider-local request for an exact one-way flight fixture."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: str
    passengers: PassengersV1 = Field(default_factory=PassengersV1)

    @field_validator("departure_date")
    @classmethod
    def validate_departure_date(cls, value: str) -> str:
        if not _YYYY_MM_DD_RE.fullmatch(value):
            raise ValueError("Date must use YYYY-MM-DD format")

        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Date must use YYYY-MM-DD format") from exc
        return value


class ProviderResult(_ProviderModel):
    """Provider-level result before orchestrator conversion."""

    provider_name: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    status: ProviderStatusCode
    offers: list[FlightOfferV1] = Field(default_factory=list)
    warnings: list[WarningV1] = Field(default_factory=list)
    errors: list[ErrorV1] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    retryable: bool = False

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: Any) -> Any:
        if isinstance(value, ProviderStatusCode):
            return value
        if isinstance(value, str):
            try:
                return ProviderStatusCode(value)
            except ValueError:
                return value
        return value


class FlightProvider(Protocol):
    """Async interface implemented by packaged flight providers."""

    name: str
    capabilities: tuple[str, ...]

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        """Return exact one-way provider results."""
