from __future__ import annotations

from cheapy.providers.traveloka.normalization.canonical import canonical_search_result


def test_canonical_search_result_maps_minor_unit_price() -> None:
    item = {
        "id": "tv-1",
        "fare": {
            "display": {
                "currencyValue": {"currency": "USD", "amount": "12345"},
                "numOfDecimalPoint": "2",
            }
        },
        "connectingFlightRoutes": [],
    }

    canonical = canonical_search_result(item)

    assert getattr(canonical, "payload")["id"] == "tv-1"
    assert getattr(canonical, "payload")["price"] == {
        "currency": "USD",
        "amount": 123.45,
    }
