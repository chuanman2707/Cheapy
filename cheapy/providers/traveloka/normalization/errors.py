"""Traveloka normalization error factories."""

from __future__ import annotations

from cheapy.models import ErrorCode, ErrorV1, Severity
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest


PROVIDER_NAME = "traveloka"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


def currency_unavailable_error(index: int, request: ProviderRequest) -> ErrorV1:
    return normalization_error(
        message_en="Provider result did not include a reliable currency.",
        failure_type="currency_unavailable",
        item_index=index,
        capability=capability_for_request(request),
    )


def return_details_unavailable_error(
    index: int,
    request: ProviderRequest,
) -> ErrorV1:
    return normalization_error(
        message_en=(
            "Traveloka priced the round trip but did not include return flight "
            "details in the captured payload."
        ),
        failure_type="return_details_unavailable",
        item_index=index,
        capability=capability_for_request(request),
    )


def selected_round_trip_error(
    failure_type: str,
    request: ProviderExactRoundTripRequest,
) -> ErrorV1:
    messages = {
        "selected_outbound_binding_unavailable": (
            "Traveloka selected outbound details could not be mapped safely."
        ),
        "selected_return_binding_unavailable": (
            "Traveloka selected return details could not be mapped safely."
        ),
        "final_round_trip_total_unavailable": (
            "Traveloka final selected round-trip total was unavailable."
        ),
    }
    return normalization_error(
        message_en=messages[failure_type],
        failure_type=failure_type,
        item_index=1,
        capability=EXACT_ROUND_TRIP_CAPABILITY,
    )


def parse_error(index: int, request: ProviderRequest, exc: Exception) -> ErrorV1:
    return normalization_error(
        message_en="Provider result could not be normalized.",
        failure_type="parse_error",
        item_index=index,
        capability=capability_for_request(request),
        exception_type=type(exc).__name__,
    )


def capability_for_request(request: ProviderRequest) -> str:
    if isinstance(request, ProviderExactRoundTripRequest):
        return EXACT_ROUND_TRIP_CAPABILITY
    return EXACT_ONE_WAY_CAPABILITY


def normalization_error(
    *,
    message_en: str,
    failure_type: str,
    item_index: int,
    capability: str,
    exception_type: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": capability,
        "failure_type": failure_type,
        "item_index": item_index,
        "exception_type": exception_type,
    }
    return ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=False,
    )
