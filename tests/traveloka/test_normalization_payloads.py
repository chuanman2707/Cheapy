from __future__ import annotations

from cheapy.providers.traveloka.normalization.payloads import itinerary_items


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
