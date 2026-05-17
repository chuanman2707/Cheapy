"""Canonicalize Traveloka search result payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class _TravelokaSearchResultItem:
    payload: Mapping[str, object]


def canonical_search_result(item: object) -> object:
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
