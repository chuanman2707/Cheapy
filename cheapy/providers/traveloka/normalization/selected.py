"""Normalize selected Traveloka round-trip captures into Contract V1 offers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from cheapy.models import ErrorV1, FlightLegV1, FlightOfferV1, OfferFlagsV1
from cheapy.providers.base import ProviderExactRoundTripRequest
from cheapy.providers.traveloka.normalization.canonical import (
    _TravelokaSearchResultItem,
)
from cheapy.providers.traveloka.normalization.errors import selected_round_trip_error
from cheapy.providers.traveloka.normalization.legs import normalize_leg
from cheapy.providers.traveloka.normalization.payloads import itinerary_items
from cheapy.providers.traveloka.normalization.routes import (
    _chain_end_index,
    date_offset,
    requested_departure_date,
    requested_destination,
    requested_origin,
    requested_return_date,
    total_duration_minutes,
)
from cheapy.providers.traveloka.results import TravelokaSelectedRoundTripResult


PROVIDER_NAME = "traveloka"


def normalize_selected_round_trip(
    result: TravelokaSelectedRoundTripResult,
    request: ProviderExactRoundTripRequest,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    currency = _currency_code(result.final_total_currency)
    if currency is None or not _valid_selected_total(result.final_total_amount):
        return _selected_failure_fallback(
            result,
            request,
            failure_type="final_round_trip_total_unavailable",
        )
    if result.selected_outbound_key is None:
        return _selected_failure_fallback(
            result,
            request,
            failure_type="selected_outbound_binding_unavailable",
        )
    if result.selected_return_key is None:
        return _selected_failure_fallback(
            result,
            request,
            failure_type="selected_return_binding_unavailable",
        )

    try:
        outbound = _selected_leg_item(
            result.outbound_payload,
            selected_key=result.selected_outbound_key,
            start=request.origin,
            end=request.destination,
            departure_date=request.departure_date,
        )
    except Exception:
        return _selected_failure_fallback(
            result,
            request,
            failure_type="selected_outbound_binding_unavailable",
        )

    try:
        return_leg = _selected_leg_item(
            result.return_payload,
            selected_key=result.selected_return_key,
            start=request.destination,
            end=request.origin,
            departure_date=request.return_date,
        )
    except Exception:
        return _selected_failure_fallback(
            result,
            request,
            failure_type="selected_return_binding_unavailable",
        )

    legs = [*outbound.legs, *return_leg.legs]
    actual_departure_date = outbound.legs[0].departure_time[:10]
    actual_return_date = return_leg.legs[0].departure_time[:10]
    departure_offset_days = date_offset(
        actual_departure_date,
        requested_departure_date(request),
    )
    return_offset_days = date_offset(actual_return_date, requested_return_date(request))
    offer = FlightOfferV1(
        offer_id=(
            f"{PROVIDER_NAME}:{request.origin}-{request.destination}:"
            f"{request.departure_date}:{request.return_date}:selected:"
            f"{outbound.item_id}:{return_leg.item_id}"
        ),
        price_amount=float(result.final_total_amount),
        currency=currency,
        comparable=True,
        rank_within_currency=1,
        global_rank=1,
        provider=PROVIDER_NAME,
        requested_origin=requested_origin(request),
        requested_destination=requested_destination(request),
        actual_origin=request.origin,
        actual_destination=request.destination,
        nearby_origin_distance_km=None,
        nearby_destination_distance_km=None,
        requested_departure_date=requested_departure_date(request),
        actual_departure_date=actual_departure_date,
        departure_offset_days=departure_offset_days,
        requested_return_date=requested_return_date(request),
        actual_return_date=actual_return_date,
        return_offset_days=return_offset_days,
        legs=legs,
        total_duration_minutes=(
            outbound.total_duration_minutes + return_leg.total_duration_minutes
        ),
        stops=outbound.stops + return_leg.stops,
        flags=OfferFlagsV1(
            uses_flexible_departure_date=departure_offset_days != 0,
            uses_flexible_return_date=return_offset_days != 0,
        ),
        fare_details_status="not_collected",
    )
    return [offer], []


def _valid_selected_total(amount: Decimal) -> bool:
    return amount.is_finite() and amount >= Decimal("0")


@dataclass(frozen=True)
class _SelectedLegItem:
    item_id: str
    legs: list[FlightLegV1]
    total_duration_minutes: int
    stops: int


def _selected_leg_item(
    payload: object,
    *,
    selected_key: str,
    start: str,
    end: str,
    departure_date: str,
) -> _SelectedLegItem:
    for item_index, item in enumerate(itinerary_items(payload), start=1):
        raw_item = item.payload if isinstance(item, _TravelokaSearchResultItem) else item
        if not isinstance(raw_item, Mapping):
            continue
        item_id = _item_id(raw_item, item_index)
        if item_id != selected_key:
            continue
        legs = [normalize_leg(segment) for segment in _raw_segments(raw_item)]
        end_index = _chain_end_index(legs, start=start, end=end, start_index=0)
        if end_index is None or end_index != len(legs) - 1:
            raise ValueError("selected leg route does not match request")
        if legs[0].departure_time[:10] != departure_date:
            raise ValueError("selected leg date does not match request")
        return _SelectedLegItem(
            item_id=item_id,
            legs=legs,
            total_duration_minutes=total_duration_minutes(raw_item, legs),
            stops=max(0, len(legs) - 1),
        )
    raise ValueError("selected key was not found")


def _selected_failure_fallback(
    result: TravelokaSelectedRoundTripResult,
    request: ProviderExactRoundTripRequest,
    *,
    failure_type: str,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload

    offers, errors = normalize_payload(result.outbound_payload, request)
    errors.append(selected_round_trip_error(failure_type, request))
    return offers, errors


def _currency_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    currency = value.strip().upper()
    if len(currency) == 3 and currency.isalpha():
        return currency
    return None


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


def _item_id(item: Mapping[str, object], item_index: int) -> str:
    for key in ("id", "offerId", "itineraryId"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return str(item_index)
