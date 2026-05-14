"""Normalize Traveloka payload dictionaries into Cheapy Contract V1 offers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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


PROVIDER_NAME = "traveloka"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


def normalize_payload(
    payload: object,
    request: ProviderRequest,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    """Convert parsed Traveloka payload mappings into Contract V1 offers."""
    offers: list[FlightOfferV1] = []
    errors: list[ErrorV1] = []
    for item_index, item in enumerate(_itinerary_items(payload), start=1):
        try:
            offers.append(
                _normalize_item(
                    item,
                    request,
                    item_index=item_index,
                    rank=len(offers) + 1,
                )
            )
        except _ItemNormalizationError as exc:
            errors.append(exc.error)
    return _rank_offers(offers), errors


def _rank_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
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


class _ItemNormalizationError(Exception):
    """Internal wrapper for a structured item-level normalization error."""

    def __init__(self, error: ErrorV1) -> None:
        super().__init__(error.message_en)
        self.error = error


def _normalize_item(
    item: object,
    request: ProviderRequest,
    *,
    item_index: int,
    rank: int,
) -> FlightOfferV1:
    try:
        if not isinstance(item, Mapping):
            raise ValueError("itinerary item must be a mapping")

        price = _price_mapping(item)
        currency = _currency(price, item)
        if currency is None:
            raise _ItemNormalizationError(
                _currency_unavailable_error(item_index, request)
            )

        legs = [_normalize_leg(segment) for segment in _raw_segments(item)]
        if not legs:
            raise ValueError("itinerary item has no legs")

        route = _validate_route(request, legs)
        actual_departure_date = legs[0].departure_time[:10]
        actual_return_date = route.return_departure_date
        _validate_exact_candidate_dates(
            request,
            actual_departure_date=actual_departure_date,
            actual_return_date=actual_return_date,
        )
        departure_offset_days = _date_offset(
            actual_departure_date,
            _requested_departure_date(request),
        )
        return_offset_days = (
            None
            if actual_return_date is None
            or not isinstance(request, ProviderExactRoundTripRequest)
            else _date_offset(actual_return_date, _requested_return_date(request))
        )
        return_suffix = (
            f":{request.return_date}"
            if isinstance(request, ProviderExactRoundTripRequest)
            else ""
        )

        return FlightOfferV1(
            offer_id=(
                f"{PROVIDER_NAME}:{request.origin}-{request.destination}:"
                f"{request.departure_date}{return_suffix}:{_item_id(item, item_index)}"
            ),
            price_amount=_price_amount(price),
            currency=currency,
            comparable=True,
            rank_within_currency=rank,
            global_rank=rank,
            provider=PROVIDER_NAME,
            requested_origin=_requested_origin(request),
            requested_destination=_requested_destination(request),
            actual_origin=(
                request.origin
                if isinstance(request, ProviderExactRoundTripRequest)
                else legs[0].origin
            ),
            actual_destination=(
                request.destination
                if isinstance(request, ProviderExactRoundTripRequest)
                else legs[-1].destination
            ),
            nearby_origin_distance_km=None,
            nearby_destination_distance_km=None,
            requested_departure_date=_requested_departure_date(request),
            actual_departure_date=actual_departure_date,
            departure_offset_days=departure_offset_days,
            requested_return_date=(
                _requested_return_date(request)
                if isinstance(request, ProviderExactRoundTripRequest)
                else None
            ),
            actual_return_date=actual_return_date,
            return_offset_days=return_offset_days,
            legs=legs,
            total_duration_minutes=_total_duration_minutes(item, legs),
            stops=_stops(item, route, leg_count=len(legs)),
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


def _itinerary_items(payload: object) -> list[object]:
    for path in (
        ("data", "itineraries"),
        ("data", "flightSearchResult", "itineraries"),
        ("itineraries",),
        ("flightSearchResult", "itineraries"),
    ):
        items = _list_at_path(payload, path)
        if items is not None:
            return items
    return list(_recursive_offer_items(payload))


def _list_at_path(payload: object, path: tuple[str, ...]) -> list[object] | None:
    current = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if isinstance(current, list):
        return list(current)
    if isinstance(current, tuple):
        return list(current)
    return None


def _recursive_offer_items(value: object) -> list[object]:
    if isinstance(value, Mapping):
        if _is_offer_like(value):
            return [value]
        items: list[object] = []
        for child in value.values():
            items.extend(_recursive_offer_items(child))
        return items
    if isinstance(value, (list, tuple)):
        items = []
        for child in value:
            items.extend(_recursive_offer_items(child))
        return items
    return []


def _is_offer_like(value: Mapping[str, object]) -> bool:
    return "price" in value and _segment_list(value) is not None


def _price_mapping(item: Mapping[str, object]) -> Mapping[str, object]:
    price = item.get("price")
    if not isinstance(price, Mapping):
        raise ValueError("itinerary item has no price mapping")
    return price


def _currency(
    price: Mapping[str, object],
    item: Mapping[str, object],
) -> str | None:
    for value in (
        price.get("currency"),
        price.get("currencyCode"),
        item.get("currency"),
        item.get("currencyCode"),
    ):
        currency = _currency_code(value)
        if currency is not None:
            return currency
    return None


def _currency_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    currency = value.strip().upper()
    if len(currency) == 3 and currency.isalpha():
        return currency
    return None


def _price_amount(price: Mapping[str, object]) -> float:
    for key in ("amount", "totalAmount", "total", "value"):
        if key in price:
            return float(price[key])
    raise ValueError("price mapping has no amount")


def _raw_segments(item: Mapping[str, object]) -> list[object]:
    segments = _segment_list(item)
    if segments is None:
        raise ValueError("itinerary item has no segments")
    return segments


def _segment_list(item: Mapping[str, object]) -> list[object] | None:
    for key in ("segments", "legs"):
        value = item.get(key)
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
    return None


def _normalize_leg(segment: object) -> FlightLegV1:
    if not isinstance(segment, Mapping):
        raise ValueError("segment must be a mapping")
    departure_time = _iso_datetime(
        _required_value(
            segment,
            "departureTime",
            "departureDateTime",
            "departure_datetime",
        )
    )
    arrival_time = _iso_datetime(
        _required_value(
            segment,
            "arrivalTime",
            "arrivalDateTime",
            "arrival_datetime",
        )
    )
    return FlightLegV1(
        origin=_string_value(
            _required_value(
                segment,
                "origin",
                "originCode",
                "departureAirport",
                "departureAirportCode",
            )
        ),
        destination=_string_value(
            _required_value(
                segment,
                "destination",
                "destinationCode",
                "arrivalAirport",
                "arrivalAirportCode",
            )
        ),
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=_string_value(
            _required_value(segment, "airlineCode", "carrierCode", "airline")
        ),
        flight_number=_string_value(
            _required_value(segment, "flightNumber", "flightNo", "number")
        ),
        duration_minutes=_duration_minutes(segment, departure_time, arrival_time),
    )


def _duration_minutes(
    segment: Mapping[str, object],
    departure_time: str,
    arrival_time: str,
) -> int:
    for key in ("durationMinutes", "durationInMinutes", "duration"):
        if key in segment:
            return int(segment[key])
    departure = datetime.fromisoformat(departure_time)
    arrival = datetime.fromisoformat(arrival_time)
    return int((arrival - departure).total_seconds() // 60)


def _required_value(segment: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in segment:
            value = segment[key]
            if value is not None:
                return value
    raise ValueError("segment field is missing")


def _string_value(value: object) -> str:
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


def _validate_route(request: ProviderRequest, legs: list[FlightLegV1]) -> "_ValidatedRoute":
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
        return _ValidatedRoute(
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
        raise ValueError("round-trip return legs do not match request")
    if return_end_index != len(legs) - 1:
        raise ValueError("round-trip result has unexpected trailing legs")
    return _ValidatedRoute(
        outbound_end_index=outbound_end_index,
        return_start_index=return_start_index,
        return_departure_date=legs[return_start_index].departure_time[:10],
    )


def _validate_exact_candidate_dates(
    request: ProviderRequest,
    *,
    actual_departure_date: str,
    actual_return_date: str | None,
) -> None:
    if actual_departure_date != request.departure_date:
        raise ValueError("outbound departure date does not match exact request")
    if not isinstance(request, ProviderExactRoundTripRequest):
        return
    if actual_return_date != request.return_date:
        raise ValueError("return departure date does not match exact request")


@dataclass(frozen=True)
class _ValidatedRoute:
    outbound_end_index: int
    return_start_index: int | None
    return_departure_date: str | None


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


def _total_duration_minutes(
    item: Mapping[str, object],
    legs: list[FlightLegV1],
) -> int:
    for key in ("durationMinutes", "durationInMinutes", "duration"):
        if key in item:
            return int(item[key])
    return sum(leg.duration_minutes for leg in legs)


def _stops(
    item: Mapping[str, object],
    route: "_ValidatedRoute",
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


def _item_id(item: Mapping[str, object], item_index: int) -> str:
    for key in ("id", "offerId", "itineraryId"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return str(item_index)


def _requested_origin(request: ProviderRequest) -> str:
    if request.requested_origin is None:
        return request.origin
    return request.requested_origin


def _requested_destination(request: ProviderRequest) -> str:
    if request.requested_destination is None:
        return request.destination
    return request.requested_destination


def _requested_departure_date(request: ProviderRequest) -> str:
    if request.requested_departure_date is None:
        return request.departure_date
    return request.requested_departure_date


def _requested_return_date(request: ProviderExactRoundTripRequest) -> str:
    if request.requested_return_date is None:
        return request.return_date
    return request.requested_return_date


def _date_offset(actual: str, requested: str) -> int:
    return (date.fromisoformat(actual) - date.fromisoformat(requested)).days


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
        return EXACT_ROUND_TRIP_CAPABILITY
    return EXACT_ONE_WAY_CAPABILITY


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
        "exception_type": exception_type,
    }
    return ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=False,
    )
