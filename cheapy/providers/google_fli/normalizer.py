"""Normalize upstream fli results into Cheapy Contract V1 offers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest


PROVIDER_NAME = "google_fli"
CAPABILITY = "exact_one_way"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


def normalize_flights(
    flights: list[object],
    request: ProviderRequest,
    *,
    configured_currency: str | None = None,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    """Convert upstream fli flight result objects into Contract V1 offers."""
    offers: list[FlightOfferV1] = []
    errors: list[ErrorV1] = []
    for item_index, flight in enumerate(flights, start=1):
        try:
            offers.append(
                _normalize_flight(
                    flight,
                    request,
                    item_index=item_index,
                    rank=len(offers) + 1,
                    configured_currency=configured_currency,
                )
            )
        except _ItemNormalizationError as exc:
            errors.append(exc.error)
    return _rank_offers(offers), errors


def _rank_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers}
    if len(currencies) <= 1:
        return [
            offer.model_copy(
                update={
                    "comparable": True,
                    "rank_within_currency": rank,
                    "global_rank": rank,
                }
            )
            for rank, offer in enumerate(offers, start=1)
        ]

    currency_ranks: dict[str, int] = {}
    ranked: list[FlightOfferV1] = []
    for offer in offers:
        rank = currency_ranks.get(offer.currency, 0) + 1
        currency_ranks[offer.currency] = rank
        ranked.append(
            offer.model_copy(
                update={
                    "comparable": False,
                    "rank_within_currency": rank,
                    "global_rank": None,
                }
            )
        )
    return ranked


class _ItemNormalizationError(Exception):
    """Internal wrapper for a structured item-level normalization error."""

    def __init__(self, error: ErrorV1) -> None:
        super().__init__(error.message_en)
        self.error = error


def _normalize_flight(
    flight: object,
    request: ProviderRequest,
    *,
    item_index: int,
    rank: int,
    configured_currency: str | None,
) -> FlightOfferV1:
    try:
        parts, is_composite = _flight_parts(flight)
        if not parts:
            raise ValueError("flight tuple has no parts")
        _validate_round_trip_part_count(request, parts, is_composite=is_composite)
        if is_composite:
            part_legs = [_normalize_part_legs(part) for part in parts]
            _validate_composite_round_trip_chains(
                request,
                part_legs,
                is_composite=is_composite,
            )
        else:
            part_legs = []
        pricing_part = _pricing_part(parts)
        currency = _currency(pricing_part, configured_currency=configured_currency)
        if currency is None:
            raise _ItemNormalizationError(
                _currency_unavailable_error(item_index, request)
            )
        if not part_legs:
            part_legs = [_normalize_part_legs(part) for part in parts]
            _validate_non_composite_round_trip_chains(
                request,
                part_legs,
                is_composite=is_composite,
            )
        legs = [leg for legs_for_part in part_legs for leg in legs_for_part]
        if not legs:
            raise ValueError("flight has no legs")
        first_leg = legs[0]
        last_leg = legs[-1]
        price_amount = float(_attr(pricing_part, "price"))
        duration = sum(int(_attr(part, "duration")) for part in parts)
        stops = sum(int(_attr(part, "stops")) for part in parts)
        actual_departure_date = first_leg.departure_time[:10]
        actual_return_date = _round_trip_return_departure_date(
            request,
            part_legs,
            is_composite=is_composite,
        )
        if (
            isinstance(request, ProviderExactRoundTripRequest)
            and actual_return_date is None
        ):
            raise ValueError("round-trip result has no return leg")
        actual_origin = (
            request.origin
            if isinstance(request, ProviderExactRoundTripRequest)
            else first_leg.origin
        )
        actual_destination = (
            request.destination
            if isinstance(request, ProviderExactRoundTripRequest)
            else last_leg.destination
        )
        departure_offset_days = _date_offset(
            actual_departure_date, request.requested_departure_date
        )
        return_offset_days = (
            None
            if actual_return_date is None
            or not isinstance(request, ProviderExactRoundTripRequest)
            else _date_offset(actual_return_date, request.requested_return_date)
        )
        return_suffix = (
            f":{request.return_date}"
            if isinstance(request, ProviderExactRoundTripRequest)
            else ""
        )
        return FlightOfferV1(
            offer_id=(
                f"{PROVIDER_NAME}:{request.origin}-{request.destination}:"
                f"{request.departure_date}{return_suffix}:{item_index}"
            ),
            price_amount=price_amount,
            currency=currency,
            comparable=True,
            rank_within_currency=rank,
            global_rank=rank,
            provider=PROVIDER_NAME,
            requested_origin=request.origin,
            requested_destination=request.destination,
            actual_origin=actual_origin,
            actual_destination=actual_destination,
            nearby_origin_distance_km=None,
            nearby_destination_distance_km=None,
            requested_departure_date=request.requested_departure_date,
            actual_departure_date=actual_departure_date,
            departure_offset_days=departure_offset_days,
            requested_return_date=(
                request.requested_return_date
                if isinstance(request, ProviderExactRoundTripRequest)
                else None
            ),
            actual_return_date=actual_return_date,
            return_offset_days=return_offset_days,
            legs=legs,
            total_duration_minutes=duration,
            stops=stops,
            flags=OfferFlagsV1(
                uses_flexible_departure_date=departure_offset_days != 0,
                uses_flexible_return_date=return_offset_days not in (None, 0),
            ),
            fare_details_status="not_collected",
        )
    except _ItemNormalizationError:
        raise
    except Exception as exc:
        raise _ItemNormalizationError(_parse_error(item_index, request, exc)) from exc


def _flight_parts(flight: object) -> tuple[list[object], bool]:
    if isinstance(flight, (list, tuple)):
        return list(flight), True
    return [flight], False


def _validate_round_trip_part_count(
    request: ProviderRequest,
    parts: list[object],
    *,
    is_composite: bool,
) -> None:
    if not isinstance(request, ProviderExactRoundTripRequest):
        return
    if is_composite and len(parts) != 2:
        raise ValueError("round-trip composite result must include outbound and return parts")


def _validate_composite_round_trip_chains(
    request: ProviderRequest,
    part_legs: list[list[FlightLegV1]],
    *,
    is_composite: bool,
) -> None:
    if not isinstance(request, ProviderExactRoundTripRequest) or not is_composite:
        return
    outbound_legs, return_legs = part_legs
    if not _is_leg_chain(
        outbound_legs,
        start=request.origin,
        end=request.destination,
    ):
        raise ValueError("round-trip outbound part does not match request")
    if not _is_leg_chain(
        return_legs,
        start=request.destination,
        end=request.origin,
    ):
        raise ValueError("round-trip return part does not match request")


def _validate_non_composite_round_trip_chains(
    request: ProviderRequest,
    part_legs: list[list[FlightLegV1]],
    *,
    is_composite: bool,
) -> None:
    if not isinstance(request, ProviderExactRoundTripRequest) or is_composite:
        return
    legs = [leg for legs_for_part in part_legs for leg in legs_for_part]
    outbound_end_index = _chain_end_index(
        legs,
        start=request.origin,
        end=request.destination,
        start_index=0,
    )
    if outbound_end_index is None:
        raise ValueError("round-trip outbound legs do not match request")
    return_start_index = outbound_end_index + 1
    return_end_index = _chain_end_index(
        legs,
        start=request.destination,
        end=request.origin,
        start_index=return_start_index,
    )
    if return_end_index is None:
        raise ValueError("round-trip return legs do not match request")
    if return_end_index != len(legs) - 1:
        raise ValueError("round-trip result has unexpected trailing legs")


def _pricing_part(parts: list[object]) -> object:
    if len(parts) > 1:
        return parts[-1]
    return parts[0]


def _date_offset(actual: str, requested: str) -> int:
    return (date.fromisoformat(actual) - date.fromisoformat(requested)).days


def _round_trip_return_departure_date(
    request: ProviderRequest,
    part_legs: list[list[FlightLegV1]],
    *,
    is_composite: bool,
) -> str | None:
    if not isinstance(request, ProviderExactRoundTripRequest):
        return None
    if is_composite:
        return_part_legs = part_legs[1]
        if _is_leg_chain(
            return_part_legs,
            start=request.destination,
            end=request.origin,
        ):
            return return_part_legs[0].departure_time[:10]
        return None
    return _first_chain_departure_date(
        [leg for legs_for_part in part_legs for leg in legs_for_part],
        start=request.destination,
        end=request.origin,
    )


def _first_chain_departure_date(
    legs: list[FlightLegV1],
    *,
    start: str,
    end: str,
) -> str | None:
    for index, first_leg in enumerate(legs):
        if first_leg.origin != start:
            continue
        current_destination = first_leg.destination
        if current_destination == end:
            return first_leg.departure_time[:10]
        for next_leg in legs[index + 1 :]:
            if next_leg.origin != current_destination:
                break
            current_destination = next_leg.destination
            if current_destination == end:
                return first_leg.departure_time[:10]
    return None


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


def _is_leg_chain(
    legs: list[FlightLegV1],
    *,
    start: str,
    end: str,
) -> bool:
    if not legs or legs[0].origin != start or legs[-1].destination != end:
        return False
    return all(
        previous_leg.destination == next_leg.origin
        for previous_leg, next_leg in zip(legs, legs[1:])
    )


def _normalize_part_legs(part: object) -> list[FlightLegV1]:
    return [_normalize_leg(raw_leg) for raw_leg in _attr(part, "legs")]


def _normalize_leg(leg: object) -> FlightLegV1:
    departure_time = _iso_datetime(_attr(leg, "departure_datetime"))
    arrival_time = _iso_datetime(_attr(leg, "arrival_datetime"))
    return FlightLegV1(
        origin=_enum_value(_attr(leg, "departure_airport")),
        destination=_enum_value(_attr(leg, "arrival_airport")),
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=_enum_value(_attr(leg, "airline")),
        flight_number=str(_attr(leg, "flight_number")),
        duration_minutes=int(_attr(leg, "duration")),
    )


def _currency(flight: object, *, configured_currency: str | None) -> str | None:
    return _currency_code(getattr(flight, "currency", None)) or _currency_code(
        configured_currency
    )


def _currency_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    currency = value.strip().upper()
    if len(currency) == 3 and currency.isalpha():
        return currency
    return None


def _attr(value: object, name: str) -> Any:
    return getattr(value, name)


def _enum_value(value: object) -> str:
    enum_name = getattr(value, "name", None)
    if isinstance(enum_name, str):
        if len(enum_name) > 1 and enum_name.startswith("_") and enum_name[1].isdigit():
            return enum_name[1:]
        return enum_name
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _iso_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    return str(value)


def _currency_unavailable_error(index: int, request: ProviderRequest) -> ErrorV1:
    return _error(
        message_en="Provider result did not include a reliable currency.",
        failure_type="currency_unavailable",
        item_index=index,
        capability=_capability_for_request(request),
    )


def _parse_error(index: int, request: ProviderRequest, exc: Exception) -> ErrorV1:
    return _error(
        message_en="Provider result could not be normalized.",
        failure_type="parse_error",
        item_index=index,
        capability=_capability_for_request(request),
        exception_type=type(exc).__name__,
    )


def _capability_for_request(request: ProviderRequest) -> str:
    if isinstance(request, ProviderExactRoundTripRequest):
        return "exact_round_trip"
    return CAPABILITY


def _error(
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
    }
    if exception_type is not None:
        details["exception_type"] = exception_type
    return ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=False,
    )
