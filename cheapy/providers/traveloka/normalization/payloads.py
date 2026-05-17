"""Discover itinerary items in Traveloka payloads."""

from __future__ import annotations

from collections.abc import Mapping

from cheapy.providers.traveloka.normalization.canonical import canonical_search_result


def itinerary_items(payload: object) -> list[object]:
    search_results = list_at_path(payload, ("data", "searchResults"))
    if search_results is not None:
        return [canonical_search_result(item) for item in search_results]

    for path in (
        ("data", "itineraries"),
        ("data", "flightSearchResult", "itineraries"),
        ("itineraries",),
        ("flightSearchResult", "itineraries"),
    ):
        items = list_at_path(payload, path)
        if items is not None:
            return items
    return list(_recursive_offer_items(payload))


def list_at_path(payload: object, path: tuple[str, ...]) -> list[object] | None:
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
    return "price" in value and _has_segment_list(value)


def _has_segment_list(value: Mapping[str, object]) -> bool:
    for key in ("segments", "legs"):
        item = value.get(key)
        if isinstance(item, (list, tuple)):
            return True
    return False
