from __future__ import annotations

from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload
from cheapy.providers.traveloka.normalization.payloads import itinerary_items

from .normalization_fixtures import _one_way_request, _segment


def test_itinerary_items_prefers_data_search_results() -> None:
    payload = {
        "data": {
            "searchResults": [
                {
                    "id": "tv-1",
                    "fare": {
                        "display": {
                            "currencyValue": {"currency": "USD", "amount": "12345"},
                            "numOfDecimalPoint": "2",
                        }
                    },
                    "connectingFlightRoutes": [],
                }
            ]
        }
    }

    items = itinerary_items(payload)

    assert len(items) == 1


def test_normalize_payload_discovers_recursive_offer_like_mapping() -> None:
    payload = {
        "data": {
            "unstructuredResults": {
                "groups": [
                    {
                        "id": "nested",
                        "price": {"amount": 88.5, "currency": "USD"},
                        "durationMinutes": 95,
                        "stops": 0,
                        "segments": [_segment()],
                    }
                ]
            }
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert [offer.offer_id for offer in offers] == [
        "traveloka:SGN-BKK:2026-07-10:nested"
    ]


def test_normalize_payload_prefers_direct_itineraries_over_recursive_fallback() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "direct",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                }
            ],
            "unstructuredResults": {
                "groups": [
                    {
                        "id": "nested",
                        "price": {"amount": 90.0, "currency": "USD"},
                        "durationMinutes": 95,
                        "stops": 0,
                        "segments": [_segment()],
                    }
                ]
            },
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert [offer.offer_id for offer in offers] == [
        "traveloka:SGN-BKK:2026-07-10:direct"
    ]
