"""Normalize Traveloka payload dictionaries into Cheapy Contract V1 offers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka.adapter import TravelokaSelectedRoundTripResult


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
            normalized = _normalize_item(
                item,
                request,
                item_index=item_index,
                rank=len(offers) + 1,
            )
            offers.append(normalized.offer)
            if normalized.return_details_unavailable:
                errors.append(
                    _return_details_unavailable_error(item_index, request)
                )
        except _ItemNormalizationError as exc:
            errors.append(exc.error)
    return _rank_offers(
        offers,
        sort_non_comparable=isinstance(request, ProviderExactRoundTripRequest),
    ), errors


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
    departure_offset_days = _date_offset(
        actual_departure_date,
        _requested_departure_date(request),
    )
    return_offset_days = _date_offset(actual_return_date, _requested_return_date(request))
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
        requested_origin=_requested_origin(request),
        requested_destination=_requested_destination(request),
        actual_origin=request.origin,
        actual_destination=request.destination,
        nearby_origin_distance_km=None,
        nearby_destination_distance_km=None,
        requested_departure_date=_requested_departure_date(request),
        actual_departure_date=actual_departure_date,
        departure_offset_days=departure_offset_days,
        requested_return_date=_requested_return_date(request),
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


def _rank_offers(
    offers: list[FlightOfferV1],
    *,
    sort_non_comparable: bool = False,
) -> list[FlightOfferV1]:
    if sort_non_comparable and all(not offer.comparable for offer in offers):
        offers = sorted(
            offers,
            key=lambda offer: (offer.currency, offer.price_amount, offer.offer_id),
        )

    ranked: list[FlightOfferV1] = []
    comparable_rank = 0
    for offer in offers:
        if not offer.comparable:
            ranked.append(
                offer.model_copy(
                    update={
                        "comparable": False,
                        "rank_within_currency": None,
                        "global_rank": None,
                    }
                )
            )
            continue

        comparable_rank += 1
        ranked.append(
            offer.model_copy(
                update={
                    "comparable": True,
                    "rank_within_currency": comparable_rank,
                    "global_rank": comparable_rank,
                }
            )
        )
    return ranked


class _ItemNormalizationError(Exception):
    """Internal wrapper for a structured item-level normalization error."""

    def __init__(self, error: ErrorV1) -> None:
        super().__init__(error.message_en)
        self.error = error


@dataclass(frozen=True)
class _TravelokaSearchResultItem:
    payload: Mapping[str, object]


@dataclass(frozen=True)
class _NormalizedItem:
    offer: FlightOfferV1
    return_details_unavailable: bool = False


@dataclass(frozen=True)
class _SelectedLegItem:
    item_id: str
    legs: list[FlightLegV1]
    total_duration_minutes: int
    stops: int


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
                _currency_unavailable_error(item_index, request)
            )

        legs = [_normalize_leg(segment) for segment in _raw_segments(raw_item)]
        if not legs:
            raise ValueError("itinerary item has no legs")

        force_raw_round_trip_partial = isinstance(
            request,
            ProviderExactRoundTripRequest,
        )
        if force_raw_round_trip_partial:
            legs = _raw_round_trip_outbound_legs(request, legs)
            route = _ValidatedRoute(
                outbound_end_index=len(legs) - 1,
                return_start_index=None,
                return_departure_date=None,
                return_details_unavailable=True,
            )
        else:
            route = _validate_route(
                request,
                legs,
                allow_priced_round_trip_outbound_only=is_traveloka_search_result,
            )
        actual_departure_date = legs[0].departure_time[:10]
        actual_return_date = route.return_departure_date
        _validate_exact_candidate_dates(
            request,
            actual_departure_date=actual_departure_date,
            actual_return_date=actual_return_date,
            allow_missing_return_details=route.return_details_unavailable,
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
                total_duration_minutes=(
                    sum(leg.duration_minutes for leg in legs)
                    if route.return_details_unavailable
                    else _total_duration_minutes(raw_item, legs)
                ),
                stops=(
                    route.outbound_end_index
                    if route.return_details_unavailable
                    else _stops(raw_item, route, leg_count=len(legs))
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
        raise _ItemNormalizationError(_parse_error(item_index, request, exc)) from exc


def _itinerary_items(payload: object) -> list[object]:
    search_results = _list_at_path(payload, ("data", "searchResults"))
    if search_results is not None:
        return [_canonical_search_result(item) for item in search_results]

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


def _selected_leg_item(
    payload: object,
    *,
    selected_key: str,
    start: str,
    end: str,
    departure_date: str,
) -> _SelectedLegItem:
    for item_index, item in enumerate(_itinerary_items(payload), start=1):
        raw_item = item.payload if isinstance(item, _TravelokaSearchResultItem) else item
        if not isinstance(raw_item, Mapping):
            continue
        item_id = _item_id(raw_item, item_index)
        if item_id != selected_key:
            continue
        legs = [_normalize_leg(segment) for segment in _raw_segments(raw_item)]
        end_index = _chain_end_index(legs, start=start, end=end, start_index=0)
        if end_index is None or end_index != len(legs) - 1:
            raise ValueError("selected leg route does not match request")
        if legs[0].departure_time[:10] != departure_date:
            raise ValueError("selected leg date does not match request")
        return _SelectedLegItem(
            item_id=item_id,
            legs=legs,
            total_duration_minutes=_total_duration_minutes(raw_item, legs),
            stops=max(0, len(legs) - 1),
        )
    raise ValueError("selected key was not found")


def _selected_failure_fallback(
    result: TravelokaSelectedRoundTripResult,
    request: ProviderExactRoundTripRequest,
    *,
    failure_type: str,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    offers, errors = normalize_payload(result.outbound_payload, request)
    errors.append(_selected_round_trip_error(failure_type, request))
    return offers, errors


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


def _canonical_search_result(item: object) -> object:
    if not isinstance(item, Mapping):
        return item

    canonical: dict[str, object] = {}
    if item.get("id") not in (None, ""):
        canonical["id"] = item["id"]

    price = _traveloka_search_result_price(item)
    if price:
        canonical["price"] = price

    metadata = item.get("flightMetadata")
    if isinstance(metadata, Mapping):
        if metadata.get("tripDuration") is not None:
            canonical["durationMinutes"] = metadata["tripDuration"]
        if metadata.get("totalNumStop") is not None:
            canonical["stops"] = metadata["totalNumStop"]

    segments = _traveloka_search_result_segments(item)
    if segments is not None:
        canonical["segments"] = segments

    return _TravelokaSearchResultItem(canonical)


def _traveloka_search_result_price(item: Mapping[str, object]) -> dict[str, object]:
    partial_price: dict[str, object] = {}
    for price in (
        _traveloka_price_at_path(item, ("fare", "display")),
        _traveloka_price_at_path(
            item,
            ("flightMetadata", "totalCombinedPrice"),
        ),
    ):
        if "amount" in price and "currency" in price:
            return price
        if price and not partial_price:
            partial_price = price
    return partial_price


def _traveloka_price_at_path(
    item: Mapping[str, object],
    path: tuple[str, ...],
) -> dict[str, object]:
    value: object = item
    for key in path:
        if not isinstance(value, Mapping):
            return {}
        value = value.get(key)
    if not isinstance(value, Mapping):
        return {}

    currency_value = value.get("currencyValue")
    if not isinstance(currency_value, Mapping):
        return {}

    price: dict[str, object] = {}
    if currency_value.get("currency") is not None:
        price["currency"] = currency_value["currency"]
    if currency_value.get("amount") is not None:
        price["amount"] = _minor_units_amount(
            currency_value["amount"],
            value.get("numOfDecimalPoint"),
        )
    return price


def _minor_units_amount(amount: object, decimal_points: object) -> object:
    try:
        scale = int(decimal_points) if decimal_points is not None else 0
        return float(Decimal(str(amount)).scaleb(-scale))
    except Exception:
        return amount


def _traveloka_search_result_segments(
    item: Mapping[str, object],
) -> list[dict[str, object]] | None:
    routes = item.get("connectingFlightRoutes")
    if not isinstance(routes, list):
        return None

    segments: list[dict[str, object]] = []
    for route in routes:
        if not isinstance(route, Mapping):
            continue
        route_segments = route.get("segments")
        if not isinstance(route_segments, list):
            continue
        for segment in route_segments:
            if isinstance(segment, Mapping):
                segments.append(_canonical_search_result_segment(segment))
    return segments


def _canonical_search_result_segment(
    segment: Mapping[str, object],
) -> dict[str, object]:
    canonical: dict[str, object] = {}
    for source_key, target_key in (
        ("departureAirport", "origin"),
        ("arrivalAirport", "destination"),
        ("airlineCode", "airlineCode"),
        ("flightNumber", "flightNumber"),
        ("durationMinutes", "durationMinutes"),
    ):
        if segment.get(source_key) is not None:
            canonical[target_key] = segment[source_key]

    departure_time = _traveloka_datetime(
        segment.get("departureDate"),
        segment.get("departureTime"),
    )
    if departure_time is not None:
        canonical["departureTime"] = departure_time

    arrival_time = _traveloka_datetime(
        segment.get("arrivalDate"),
        segment.get("arrivalTime"),
    )
    if arrival_time is not None:
        canonical["arrivalTime"] = arrival_time

    return canonical


def _traveloka_datetime(date_value: object, time_value: object) -> str | None:
    if not isinstance(date_value, Mapping) or not isinstance(time_value, Mapping):
        return None
    try:
        timestamp = datetime(
            int(date_value["year"]),
            int(date_value["month"]),
            int(date_value["day"]),
            int(time_value["hour"]),
            int(time_value["minute"]),
        )
    except Exception:
        return None
    return timestamp.isoformat(timespec="seconds")


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


def _validate_route(
    request: ProviderRequest,
    legs: list[FlightLegV1],
    *,
    allow_priced_round_trip_outbound_only: bool = False,
) -> "_ValidatedRoute":
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
        if (
            allow_priced_round_trip_outbound_only
            and outbound_end_index == len(legs) - 1
        ):
            return _ValidatedRoute(
                outbound_end_index=outbound_end_index,
                return_start_index=None,
                return_departure_date=None,
                return_details_unavailable=True,
            )
        raise ValueError("round-trip return legs do not match request")
    if return_end_index != len(legs) - 1:
        raise ValueError("round-trip result has unexpected trailing legs")
    return _ValidatedRoute(
        outbound_end_index=outbound_end_index,
        return_start_index=return_start_index,
        return_departure_date=legs[return_start_index].departure_time[:10],
    )


def _raw_round_trip_outbound_legs(
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


def _validate_exact_candidate_dates(
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


@dataclass(frozen=True)
class _ValidatedRoute:
    outbound_end_index: int
    return_start_index: int | None
    return_departure_date: str | None
    return_details_unavailable: bool = False


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


def _return_details_unavailable_error(
    index: int,
    request: ProviderRequest,
) -> ErrorV1:
    return _error(
        message_en=(
            "Traveloka priced the round trip but did not include return flight "
            "details in the captured payload."
        ),
        failure_type="return_details_unavailable",
        item_index=index,
        capability=_capability_for_request(request),
    )


def _selected_round_trip_error(
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
    return _error(
        message_en=messages[failure_type],
        failure_type=failure_type,
        item_index=1,
        capability=EXACT_ROUND_TRIP_CAPABILITY,
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
