"""Provider-local request and result models."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cheapy.models import (
    ErrorV1,
    FlightOfferV1,
    PassengersV1,
    ProviderStatusCode,
    WarningV1,
)

_YYYY_MM_DD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_yyyy_mm_dd(value: str) -> str:
    if not _YYYY_MM_DD_RE.fullmatch(value):
        raise ValueError("Date must use YYYY-MM-DD format")

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format") from exc
    return value


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProviderExactOneWayRequest(_ProviderModel):
    """Provider-local request for an exact one-way flight search."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: str
    requested_origin: str | None = Field(default=None, min_length=3, max_length=3)
    requested_destination: str | None = Field(default=None, min_length=3, max_length=3)
    requested_departure_date: str | None = None
    passengers: PassengersV1 = Field(default_factory=PassengersV1)

    @field_validator("departure_date", "requested_departure_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_yyyy_mm_dd(value)

    @model_validator(mode="after")
    def default_requested_fields(self) -> Self:
        if self.requested_origin is None:
            self.requested_origin = self.origin
        if self.requested_destination is None:
            self.requested_destination = self.destination
        if self.requested_departure_date is None:
            self.requested_departure_date = self.departure_date
        return self


class ProviderExactRoundTripRequest(_ProviderModel):
    """Provider-local request for an exact round-trip flight search."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: str
    return_date: str
    requested_origin: str | None = Field(default=None, min_length=3, max_length=3)
    requested_destination: str | None = Field(default=None, min_length=3, max_length=3)
    requested_departure_date: str | None = None
    requested_return_date: str | None = None
    passengers: PassengersV1 = Field(default_factory=PassengersV1)

    @field_validator(
        "departure_date",
        "return_date",
        "requested_departure_date",
        "requested_return_date",
    )
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_yyyy_mm_dd(value)

    @model_validator(mode="after")
    def default_and_validate_requested_fields(self) -> Self:
        if self.requested_origin is None:
            self.requested_origin = self.origin
        if self.requested_destination is None:
            self.requested_destination = self.destination
        if self.requested_departure_date is None:
            self.requested_departure_date = self.departure_date
        if self.requested_return_date is None:
            self.requested_return_date = self.return_date

        departure = datetime.strptime(self.departure_date, "%Y-%m-%d")
        return_date = datetime.strptime(self.return_date, "%Y-%m-%d")
        if return_date < departure:
            raise ValueError("return_date must not be earlier than departure_date")
        requested_departure = datetime.strptime(
            self.requested_departure_date,
            "%Y-%m-%d",
        )
        requested_return = datetime.strptime(self.requested_return_date, "%Y-%m-%d")
        if requested_return < requested_departure:
            raise ValueError(
                "requested_return_date must not be earlier than requested_departure_date"
            )
        return self


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

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        """Return exact round-trip provider results."""
