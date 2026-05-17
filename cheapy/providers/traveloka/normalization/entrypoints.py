"""Public Traveloka normalization entrypoints."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cheapy.models import ErrorV1, FlightOfferV1, OfferFlagsV1
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka.normalization.canonical import (
    _TravelokaSearchResultItem,
)
from cheapy.providers.traveloka.normalization.errors import (
    currency_unavailable_error,
    parse_error,
    return_details_unavailable_error,
)
from cheapy.providers.traveloka.normalization.legs import normalize_leg
from cheapy.providers.traveloka.normalization.payloads import itinerary_items
from cheapy.providers.traveloka.normalization.ranking import rank_offers
from cheapy.providers.traveloka.normalization.routes import (
    ValidatedRoute,
    date_offset,
    raw_round_trip_outbound_legs,
    requested_departure_date,
    requested_destination,
    requested_origin,
    requested_return_date,
    stops,
    total_duration_minutes,
    validate_exact_candidate_dates,
    validate_route,
)
from cheapy.providers.traveloka.normalization.selected import (
    normalize_selected_round_trip,
)


PROVIDER_NAME = "traveloka"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


def normalize_payload(
    payload: object,
    request: ProviderRequest,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    """Convert parsed Traveloka payload mappings into Contract V1 offers."""
    offers: list[FlightOfferV1] = []
    errors: list[ErrorV1] = []
    for item_index, item in enumerate(itinerary_items(payload), start=1):
        try:
            normalized = _normalize_item(
                item,
                request,
                item_index=item_index,
                rank=len(offers) + 1,
            )
            offers.append(normalized.offer)
            if normalized.return_details_unavailable:
                errors.append(
                    return_details_unavailable_error(item_index, request)
                )
        except _ItemNormalizationError as exc:
            errors.append(exc.error)
    return rank_offers(
        offers,
        sort_non_comparable=isinstance(request, ProviderExactRoundTripRequest),
    ), errors


class _ItemNormalizationError(Exception):
    """Internal wrapper for a structured item-level normalization error."""

    def __init__(self, error: ErrorV1) -> None:
        super().__init__(error.message_en)
        self.error = error


@dataclass(frozen=True)
class _NormalizedItem:
    offer: FlightOfferV1
    return_details_unavailable: bool = False


def _normalize_item(
    item: object,
    request: ProviderRequest,
    *,
    item_index: int,
    rank: int,
) -> _NormalizedItem:
    try:
        is_traveloka_search_result = isinstance(item, _TravelokaSearchResultItem)
        raw_item = item.payload if is_traveloka_search_result else item

        if not isinstance(raw_item, Mapping):
            raise ValueError("itinerary item must be a mapping")

        price = _price_mapping(raw_item)
        currency = _currency(price, raw_item)
        if currency is None:
            raise _ItemNormalizationError(
                currency_unavailable_error(item_index, request)
            )

        legs = [normalize_leg(segment) for segment in _raw_segments(raw_item)]
        if not legs:
            raise ValueError("itinerary item has no legs")

        force_raw_round_trip_partial = isinstance(
            request,
            ProviderExactRoundTripRequest,
        )
        if force_raw_round_trip_partial:
            legs = raw_round_trip_outbound_legs(request, legs)
            route = ValidatedRoute(
                outbound_end_index=len(legs) - 1,
                return_start_index=None,
                return_departure_date=None,
                return_details_unavailable=True,
            )
        else:
            route = validate_route(
                request,
                legs,
                allow_priced_round_trip_outbound_only=is_traveloka_search_result,
            )
        actual_departure_date = legs[0].departure_time[:10]
        actual_return_date = route.return_departure_date
        validate_exact_candidate_dates(
            request,
            actual_departure_date=actual_departure_date,
            actual_return_date=actual_return_date,
            allow_missing_return_details=route.return_details_unavailable,
        )
        departure_offset_days = date_offset(
            actual_departure_date,
            requested_departure_date(request),
        )
        return_offset_days = (
            None
            if actual_return_date is None
            or not isinstance(request, ProviderExactRoundTripRequest)
            else date_offset(actual_return_date, requested_return_date(request))
        )
        return_suffix = (
            f":{request.return_date}"
            if isinstance(request, ProviderExactRoundTripRequest)
            else ""
        )
        is_comparable = not route.return_details_unavailable

        return _NormalizedItem(
            offer=FlightOfferV1(
                offer_id=(
                    f"{PROVIDER_NAME}:{request.origin}-{request.destination}:"
                    f"{request.departure_date}{return_suffix}:{_item_id(raw_item, item_index)}"
                ),
                price_amount=_price_amount(price),
                currency=currency,
                comparable=is_comparable,
                rank_within_currency=rank if is_comparable else None,
                global_rank=rank if is_comparable else None,
                provider=PROVIDER_NAME,
                requested_origin=requested_origin(request),
                requested_destination=requested_destination(request),
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
                requested_departure_date=requested_departure_date(request),
                actual_departure_date=actual_departure_date,
                departure_offset_days=departure_offset_days,
                requested_return_date=(
                    requested_return_date(request)
                    if isinstance(request, ProviderExactRoundTripRequest)
                    else None
                ),
                actual_return_date=actual_return_date,
                return_offset_days=return_offset_days,
                legs=legs,
                total_duration_minutes=(
                    sum(leg.duration_minutes for leg in legs)
                    if route.return_details_unavailable
                    else total_duration_minutes(raw_item, legs)
                ),
                stops=(
                    route.outbound_end_index
                    if route.return_details_unavailable
                    else stops(raw_item, route, leg_count=len(legs))
                ),
                flags=OfferFlagsV1(
                    uses_flexible_departure_date=departure_offset_days != 0,
                    uses_flexible_return_date=return_offset_days not in (None, 0),
                ),
                fare_details_status="not_collected",
            ),
            return_details_unavailable=route.return_details_unavailable,
        )
    except _ItemNormalizationError:
        raise
    except Exception as exc:
        raise _ItemNormalizationError(parse_error(item_index, request, exc)) from exc


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


def _item_id(item: Mapping[str, object], item_index: int) -> str:
    for key in ("id", "offerId", "itineraryId"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return str(item_index)


__all__ = ["normalize_payload", "normalize_selected_round_trip"]
