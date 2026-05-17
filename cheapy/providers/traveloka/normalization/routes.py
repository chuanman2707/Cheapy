"""Validate Traveloka normalized leg chains against provider requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from cheapy.models import FlightLegV1
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest


ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


@dataclass(frozen=True)
class ValidatedRoute:
    outbound_end_index: int
    return_start_index: int | None
    return_departure_date: str | None
    return_details_unavailable: bool = False


def validate_route(
    request: ProviderRequest,
    legs: list[FlightLegV1],
    *,
    allow_priced_round_trip_outbound_only: bool = False,
) -> ValidatedRoute:
    outbound_end_index = _chain_end_index(
        legs,
        start=request.origin,
        end=request.destination,
        start_index=0,
    )
    if outbound_end_index is None:
        raise ValueError("outbound legs do not match request")

    if not isinstance(request, ProviderExactRoundTripRequest):
        if outbound_end_index != len(legs) - 1:
            raise ValueError("one-way result has unexpected trailing legs")
        return ValidatedRoute(
            outbound_end_index=outbound_end_index,
            return_start_index=None,
            return_departure_date=None,
        )

    return_start_index = outbound_end_index + 1
    return_end_index = _chain_end_index(
        legs,
        start=request.destination,
        end=request.origin,
        start_index=return_start_index,
    )
    if return_end_index is None:
        if (
            allow_priced_round_trip_outbound_only
            and outbound_end_index == len(legs) - 1
        ):
            return ValidatedRoute(
                outbound_end_index=outbound_end_index,
                return_start_index=None,
                return_departure_date=None,
                return_details_unavailable=True,
            )
        raise ValueError("round-trip return legs do not match request")
    if return_end_index != len(legs) - 1:
        raise ValueError("round-trip result has unexpected trailing legs")
    return ValidatedRoute(
        outbound_end_index=outbound_end_index,
        return_start_index=return_start_index,
        return_departure_date=legs[return_start_index].departure_time[:10],
    )


def raw_round_trip_outbound_legs(
    request: ProviderExactRoundTripRequest,
    legs: list[FlightLegV1],
) -> list[FlightLegV1]:
    outbound_end_index = _chain_end_index(
        legs,
        start=request.origin,
        end=request.destination,
        start_index=0,
    )
    if outbound_end_index is None:
        raise ValueError("outbound legs do not match request")
    return legs[: outbound_end_index + 1]


def validate_exact_candidate_dates(
    request: ProviderRequest,
    *,
    actual_departure_date: str,
    actual_return_date: str | None,
    allow_missing_return_details: bool = False,
) -> None:
    if actual_departure_date != request.departure_date:
        raise ValueError("outbound departure date does not match exact request")
    if not isinstance(request, ProviderExactRoundTripRequest):
        return
    if allow_missing_return_details and actual_return_date is None:
        return
    if actual_return_date != request.return_date:
        raise ValueError("return departure date does not match exact request")


def _chain_end_index(
    legs: list[FlightLegV1],
    *,
    start: str,
    end: str,
    start_index: int,
) -> int | None:
    if start_index >= len(legs) or legs[start_index].origin != start:
        return None
    current_destination = legs[start_index].destination
    if current_destination == end:
        return start_index
    for index in range(start_index + 1, len(legs)):
        leg = legs[index]
        if leg.origin != current_destination:
            return None
        current_destination = leg.destination
        if current_destination == end:
            return index
    return None


def total_duration_minutes(
    item: Mapping[str, object],
    legs: list[FlightLegV1],
) -> int:
    for key in ("durationMinutes", "durationInMinutes", "duration"):
        if key in item:
            return int(item[key])
    return sum(leg.duration_minutes for leg in legs)


def stops(
    item: Mapping[str, object],
    route: ValidatedRoute,
    *,
    leg_count: int,
) -> int:
    for key in ("stops", "stopCount"):
        if key in item:
            return int(item[key])
    outbound_stops = route.outbound_end_index
    if route.return_start_index is None:
        return outbound_stops
    return_stops = max(0, leg_count - route.return_start_index - 1)
    return outbound_stops + return_stops


def date_offset(actual: str, requested: str) -> int:
    return (date.fromisoformat(actual) - date.fromisoformat(requested)).days


def requested_origin(request: ProviderRequest) -> str:
    if request.requested_origin is None:
        return request.origin
    return request.requested_origin


def requested_destination(request: ProviderRequest) -> str:
    if request.requested_destination is None:
        return request.destination
    return request.requested_destination


def requested_departure_date(request: ProviderRequest) -> str:
    if request.requested_departure_date is None:
        return request.departure_date
    return request.requested_departure_date


def requested_return_date(request: ProviderExactRoundTripRequest) -> str:
    if request.requested_return_date is None:
        return request.return_date
    return request.requested_return_date
