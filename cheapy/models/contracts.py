"""Contract V1 models for Cheapy MCP tool inputs and outputs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EnumT = TypeVar("EnumT", bound=StrEnum)
_YYYY_MM_DD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_yyyy_mm_dd(value: str) -> str:
    if not _YYYY_MM_DD_RE.fullmatch(value):
        raise ValueError("Date must use YYYY-MM-DD format")

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format") from exc
    return value


def _validate_iso_like_datetime(value: str) -> str:
    if len(value) <= 10 or value[10] not in {"T", " "}:
        raise ValueError(
            "Date-time must include a time component, for example 2026-07-10T09:00:00"
        )

    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "Date-time must be ISO-like, for example 2026-07-10T09:00:00"
        ) from exc
    return value


def _coerce_str_enum(enum_type: type[EnumT], value: Any) -> Any:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            return value
    return value


def _coerce_str_enum_list(enum_type: type[EnumT], value: Any) -> Any:
    if isinstance(value, list):
        return [_coerce_str_enum(enum_type, item) for item in value]
    return value


def _coerce_str_enum_dict_keys(enum_type: type[EnumT], value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _coerce_str_enum(enum_type, key): count for key, count in value.items()
        }
    return value


class SearchMode(StrEnum):
    """Supported search modes."""

    EXACT = "exact"
    EXPANDED = "expanded"


class SearchStatus(StrEnum):
    """Top-level search response status."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"


class Severity(StrEnum):
    """Warning and error severity."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WarningCode(StrEnum):
    """Stable phase-1 warning codes."""

    MIXED_CURRENCY = "mixed_currency"
    SEARCH_TRUNCATED = "search_truncated"
    CANDIDATE_FAMILY_TRUNCATED = "candidate_family_truncated"
    FARE_DETAILS_NOT_COLLECTED = "fare_details_not_collected"
    SPLIT_TICKET = "split_ticket"
    SELF_TRANSFER = "self_transfer"
    NEARBY_AIRPORT_USED = "nearby_airport_used"
    FLEXIBLE_DATE_USED = "flexible_date_used"
    LOCAL_STORAGE_FAILED = "local_storage_failed"


class ErrorCode(StrEnum):
    """Stable phase-1 error codes."""

    PROVIDER_FAILED = "provider_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_BLOCKED = "provider_blocked"
    NO_PROVIDER_AVAILABLE = "no_provider_available"
    AIRPORT_AMBIGUOUS = "airport_ambiguous"
    AIRPORT_NOT_FOUND = "airport_not_found"


class ProviderStatusCode(StrEnum):
    """Provider execution status."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class CandidateFamily(StrEnum):
    """Search candidate family names."""

    EXACT = "exact"
    FLEXIBLE_DATES = "flexible_dates"
    NEARBY_ORIGIN = "nearby_origin"
    NEARBY_DESTINATION = "nearby_destination"
    SPLIT_TICKET = "split_ticket"


class StrictModel(BaseModel):
    """Base model for strict Contract V1 validation."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PassengersV1(StrictModel):
    """Passenger counts for phase 1."""

    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    infants_on_lap: int = Field(default=0, ge=0)
    infants_in_seat: int = Field(default=0, ge=0)


class SearchRequestV1(StrictModel):
    """Input contract for `search_cheapest_flights`."""

    schema_version: Literal["1"]
    origin: str = Field(
        min_length=1,
        description="Origin airport as a 3-letter IATA code. Cheapy tools only accept IATA codes.",
    )
    destination: str = Field(
        min_length=1,
        description="Destination airport as a 3-letter IATA code. Cheapy tools only accept IATA codes.",
    )
    departure_date: str = Field(
        description="Outbound departure date in YYYY-MM-DD format."
    )
    return_date: str | None = Field(
        default=None,
        description="Optional return date in YYYY-MM-DD format for round trips.",
    )
    search_mode: SearchMode = Field(
        default=SearchMode.EXACT,
        description="Search breadth to use, either exact request matching or expanded candidates.",
    )
    passengers: PassengersV1 = Field(
        default_factory=PassengersV1,
        description="Passenger counts for adults, children, and infants.",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of flight offers to return.",
    )

    @field_validator("departure_date", "return_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_yyyy_mm_dd(value)

    @field_validator("search_mode", mode="before")
    @classmethod
    def validate_search_mode(cls, value: Any) -> Any:
        return _coerce_str_enum(SearchMode, value)

    @model_validator(mode="after")
    def validate_return_date_order(self) -> Self:
        if self.return_date is None:
            return self
        departure = datetime.strptime(self.departure_date, "%Y-%m-%d")
        return_date = datetime.strptime(self.return_date, "%Y-%m-%d")
        if return_date < departure:
            raise ValueError("return_date must not be earlier than departure_date")
        return self


class WarningV1(StrictModel):
    """Machine-readable warning."""

    code: WarningCode
    severity: Severity
    message_en: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False

    @field_validator("code", mode="before")
    @classmethod
    def validate_code(cls, value: Any) -> Any:
        return _coerce_str_enum(WarningCode, value)

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, value: Any) -> Any:
        return _coerce_str_enum(Severity, value)


class ErrorV1(StrictModel):
    """Machine-readable error."""

    code: ErrorCode
    severity: Severity
    message_en: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False

    @field_validator("code", mode="before")
    @classmethod
    def validate_code(cls, value: Any) -> Any:
        return _coerce_str_enum(ErrorCode, value)

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, value: Any) -> Any:
        return _coerce_str_enum(Severity, value)


class SearchPlanV1(StrictModel):
    """Executed search plan and budget accounting."""

    search_mode: SearchMode
    planned_candidate_count: int = Field(ge=0)
    executed_candidate_count: int = Field(ge=0)
    planned_provider_call_count: int = Field(ge=0)
    executed_provider_call_count: int = Field(ge=0)
    candidate_count_by_family: dict[CandidateFamily, int]
    provider_call_count_by_family: dict[CandidateFamily, int]
    truncated: bool
    truncated_families: list[CandidateFamily]
    candidate_families: list[CandidateFamily]

    @field_validator("search_mode", mode="before")
    @classmethod
    def validate_search_mode(cls, value: Any) -> Any:
        return _coerce_str_enum(SearchMode, value)

    @field_validator(
        "candidate_count_by_family",
        "provider_call_count_by_family",
        mode="before",
    )
    @classmethod
    def validate_candidate_family_keys(cls, value: Any) -> Any:
        return _coerce_str_enum_dict_keys(CandidateFamily, value)

    @field_validator("truncated_families", "candidate_families", mode="before")
    @classmethod
    def validate_candidate_family_list(cls, value: Any) -> Any:
        return _coerce_str_enum_list(CandidateFamily, value)


class ProviderStatusV1(StrictModel):
    """Provider execution status."""

    provider_name: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    status: ProviderStatusCode
    planned_call_count: int = Field(ge=0)
    executed_call_count: int = Field(ge=0)
    succeeded_call_count: int = Field(ge=0)
    failed_call_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    warnings: list[WarningV1] = Field(default_factory=list)
    errors: list[ErrorV1] = Field(default_factory=list)
    retryable: bool = False

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: Any) -> Any:
        return _coerce_str_enum(ProviderStatusCode, value)


class OfferFlagsV1(StrictModel):
    """Simple phase-1 offer flags."""

    is_split_ticket: bool = False
    is_self_transfer: bool = False
    uses_nearby_origin: bool = False
    uses_nearby_destination: bool = False
    uses_flexible_departure_date: bool = False
    uses_flexible_return_date: bool = False
    has_long_connection: bool = False
    has_overnight_connection: bool = False
    has_many_stops: bool = False
    baggage_unknown: bool = True


class FlightLegV1(StrictModel):
    """A single flight leg."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_time: str
    arrival_time: str
    airline_code: str = Field(min_length=1)
    flight_number: str = Field(min_length=1)
    duration_minutes: int = Field(ge=0)

    @field_validator("departure_time", "arrival_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _validate_iso_like_datetime(value)


class FlightOfferV1(StrictModel):
    """Canonical flight offer returned by Cheapy."""

    offer_id: str = Field(min_length=1)
    price_amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    comparable: bool
    rank_within_currency: int | None = Field(default=None, ge=1)
    global_rank: int | None = Field(default=None, ge=1)
    provider: str = Field(min_length=1)
    requested_origin: str = Field(min_length=1)
    requested_destination: str = Field(min_length=1)
    actual_origin: str = Field(min_length=3, max_length=3)
    actual_destination: str = Field(min_length=3, max_length=3)
    nearby_origin_distance_km: float | None = Field(default=None, ge=0)
    nearby_destination_distance_km: float | None = Field(default=None, ge=0)
    requested_departure_date: str
    actual_departure_date: str
    departure_offset_days: int
    requested_return_date: str | None = None
    actual_return_date: str | None = None
    return_offset_days: int | None = None
    legs: list[FlightLegV1]
    total_duration_minutes: int = Field(ge=0)
    stops: int = Field(ge=0)
    flags: OfferFlagsV1
    fare_details_status: Literal["not_collected"]
    public_search_url: str | None = None

    @field_validator(
        "requested_departure_date",
        "actual_departure_date",
        "requested_return_date",
        "actual_return_date",
    )
    @classmethod
    def validate_offer_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_yyyy_mm_dd(value)

    @model_validator(mode="after")
    def validate_public_search_url(self) -> Self:
        if self.public_search_url is None:
            return self

        from cheapy.public_url_safety import validate_public_search_url

        public_search_url = validate_public_search_url(
            self.provider, self.public_search_url
        )
        if public_search_url is None:
            raise ValueError("public_search_url is not safe for provider")
        self.public_search_url = public_search_url
        return self


class CurrencyGroupV1(StrictModel):
    """Derived view over offers for a single currency."""

    currency: str = Field(min_length=3, max_length=3)
    offer_ids: list[str]


class AirportCandidateV1(StrictModel):
    """Airport clarification candidate."""

    iata: str = Field(min_length=3, max_length=3)
    name: str = Field(min_length=1)
    city: str | None = None
    country: str | None = None
    confidence: float = Field(ge=0, le=1)


class SearchResponseV1(StrictModel):
    """Output contract for `search_cheapest_flights`."""

    schema_version: Literal["1"]
    status: SearchStatus
    request_id: str = Field(min_length=1)
    offers: list[FlightOfferV1] = Field(
        description="Canonical ranked flight offers returned for the request."
    )
    warnings: list[WarningV1] = Field(
        description="Non-fatal warnings produced while planning or executing the search."
    )
    errors: list[ErrorV1] = Field(
        description="Fatal or provider-level errors produced during the search."
    )
    provider_statuses: list[ProviderStatusV1] = Field(
        description="Per-provider execution status and call accounting."
    )
    search_plan: SearchPlanV1 = Field(
        description="Executed candidate plan and search budget accounting."
    )
    mixed_currency: bool = Field(
        description="Whether returned offers contain more than one currency."
    )
    currency_groups: list[CurrencyGroupV1] = Field(
        description="Offer identifiers grouped by currency for comparison."
    )
    currency_notes: list[str] = Field(
        description="Human-readable notes about currency comparison limitations."
    )
    candidates: list[AirportCandidateV1] | None = Field(
        description="Airport clarification candidates when the request needs disambiguation, otherwise null."
    )

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: Any) -> Any:
        return _coerce_str_enum(SearchStatus, value)

    @model_validator(mode="after")
    def validate_currency_group_offer_ids(self) -> Self:
        offer_ids = {offer.offer_id for offer in self.offers}
        missing_offer_ids = [
            offer_id
            for group in self.currency_groups
            for offer_id in group.offer_ids
            if offer_id not in offer_ids
        ]
        if missing_offer_ids:
            raise ValueError(
                "currency_groups reference missing offer_ids: "
                + ", ".join(missing_offer_ids)
            )
        return self
