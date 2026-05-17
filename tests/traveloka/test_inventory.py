from __future__ import annotations

from decimal import Decimal
from typing import get_type_hints

import pytest

from cheapy.providers.traveloka import browser_helpers
from cheapy.providers.traveloka import inventory as traveloka_inventory

from .fakes import (
    FakeLocator,
    LiveTravelokaCardLocator,
    LiveTravelokaInventoryPage,
    LocatorFakePage,
    _visible_option,
)

def test_traveloka_inventory_module_owns_visible_option_contract() -> None:
    option = traveloka_inventory.TravelokaVisibleOption(
        key="out-1",
        airline_name="Traveloka Air",
        departure_time_text=None,
        arrival_time_text=None,
        route_text=None,
        price_amount=Decimal("10.00"),
        currency="USD",
        locator=FakeLocator(),
    )

    assert traveloka_inventory.cheapest_visible_option([option]) == option


def test_cheapest_visible_option_returns_none_for_empty_options() -> None:
    assert traveloka_inventory.cheapest_visible_option([]) is None


def test_cheapest_visible_option_uses_lowest_price_then_stable_key_tie_break() -> None:
    options = [
        _visible_option(
            key="b-option",
            airline_name="Airline B",
            price_amount=Decimal("90"),
        ),
        _visible_option(
            key="c-option",
            airline_name="Airline C",
            price_amount=Decimal("100"),
        ),
        _visible_option(
            key="a-option",
            airline_name="Airline A",
            price_amount=Decimal("90"),
        ),
    ]

    cheapest = traveloka_inventory.cheapest_visible_option(options)

    assert cheapest is not None
    assert cheapest.key == "a-option"


def test_visible_option_optional_text_fields_accept_none_and_tie_break_safely() -> None:
    type_hints = get_type_hints(traveloka_inventory.TravelokaVisibleOption)
    for field_name in (
        "key",
        "airline_name",
        "departure_time_text",
        "arrival_time_text",
        "route_text",
        "currency",
    ):
        assert type_hints[field_name] == str | None

    option_with_none_fields = traveloka_inventory.TravelokaVisibleOption(
        key=None,
        airline_name=None,
        departure_time_text=None,
        arrival_time_text=None,
        route_text=None,
        price_amount=Decimal("90"),
        currency=None,
        locator=FakeLocator(),
    )
    other_option = traveloka_inventory.TravelokaVisibleOption(
        key="a-option",
        airline_name=None,
        departure_time_text="09:00",
        arrival_time_text="10:35",
        route_text="SGN-BKK",
        price_amount=Decimal("90"),
        currency="USD",
        locator=FakeLocator(),
    )

    cheapest = traveloka_inventory.cheapest_visible_option(
        [other_option, option_with_none_fields]
    )

    assert cheapest is other_option


def test_cheapest_visible_option_prefers_keyed_option_for_same_price() -> None:
    keyless_option = _visible_option(
        key=None,
        airline_name="Airline A",
        price_amount=Decimal("90"),
    )
    empty_key_option = _visible_option(
        key="",
        airline_name="Airline B",
        price_amount=Decimal("90"),
    )
    keyed_option = _visible_option(
        key="bindable-option",
        airline_name="Airline C",
        price_amount=Decimal("90"),
    )

    cheapest = traveloka_inventory.cheapest_visible_option(
        [keyless_option, keyed_option, empty_key_option]
    )

    assert cheapest is keyed_option


def test_cheapest_visible_option_prefers_non_numeric_key_for_same_price() -> None:
    numeric_fallback_option = _visible_option(
        key="1",
        airline_name="Airline A",
        price_amount=Decimal("90"),
    )
    keyed_option = _visible_option(
        key="out-1",
        airline_name="Airline B",
        price_amount=Decimal("90"),
    )

    cheapest = traveloka_inventory.cheapest_visible_option(
        [numeric_fallback_option, keyed_option]
    )

    assert cheapest is keyed_option


@pytest.mark.parametrize(
    ("text", "expected_amount", "expected_currency"),
    [
        ("USD 123.45", Decimal("123.45"), "USD"),
        ("$1,234.50", Decimal("1234.50"), "USD"),
        ("\u20ab1.234.000", Decimal("1234000"), "VND"),
    ],
)
def test_parse_visible_price_handles_usd_like_and_vnd_grouped_prices(
    text: str,
    expected_amount: Decimal,
    expected_currency: str,
) -> None:
    amount, currency = traveloka_inventory.parse_visible_price(text)

    assert amount == expected_amount
    assert currency == expected_currency


@pytest.mark.parametrize(
    ("text", "expected_amount", "expected_currency"),
    [
        ("09:00 10:35 USD 123.45", Decimal("123.45"), "USD"),
        ("09:00 10:35 \u20ab1.234.000", Decimal("1234000"), "VND"),
    ],
)
def test_parse_visible_price_uses_amount_near_supported_currency_marker(
    text: str,
    expected_amount: Decimal,
    expected_currency: str,
) -> None:
    amount, currency = traveloka_inventory.parse_visible_price(text)

    assert amount == expected_amount
    assert currency == expected_currency


def test_bind_visible_option_to_payload_returns_key_only_for_explicit_payload_id() -> None:
    payload = {
        "data": {
            "searchResults": [
                {"id": "out-1"},
                {"offerId": "out-offer-2"},
                {"itineraryId": "out-itinerary-3"},
                {"price": {"amount": "99", "currency": "USD"}},
            ]
        }
    }

    assert (
        traveloka_inventory.bind_visible_option_to_payload(
            _visible_option(key="out-offer-2"),
            payload,
        )
        == "out-offer-2"
    )
    assert (
        traveloka_inventory.bind_visible_option_to_payload(
            _visible_option(key="1"),
            payload,
        )
        is None
    )
    assert (
        traveloka_inventory.bind_visible_option_to_payload(
            _visible_option(key="missing"),
            payload,
        )
        is None
    )


def test_bind_visible_option_to_payload_ignores_numeric_payload_ids() -> None:
    payload = {"data": {"searchResults": [{"id": 1}]}}

    assert (
        traveloka_inventory.bind_visible_option_to_payload(
            _visible_option(key="1"),
            payload,
        )
        is None
    )


def test_visible_options_from_page_discovers_live_inventory_cards() -> None:
    expensive = LiveTravelokaCardLocator(
        container_id="eva-expensive",
        text=(
            "EVA Air\n"
            "21:40\n"
            "AMS\n"
            "35h 15m\n"
            "SGN\n"
            "USD 4,307.31/pax\n"
            "Round-trip price\n"
            "Choose\n"
            "Choose"
        ),
    )
    cheapest = LiveTravelokaCardLocator(
        container_id="qatar-cheapest",
        text=(
            "Qatar Airways\n"
            "16:15\n"
            "AMS\n"
            "42h 25m\n"
            "SGN\n"
            "USD 1,663.60/pax\n"
            "Round-trip price\n"
            "Choose\n"
            "Choose"
        ),
    )
    page = LiveTravelokaInventoryPage([expensive, cheapest])

    options = traveloka_inventory.visible_options_from_page(page, timeout_ms=123)

    assert [option.key for option in options] == [
        "eva-expensive",
        "qatar-cheapest",
    ]
    assert [option.price_amount for option in options] == [
        Decimal("4307.31"),
        Decimal("1663.60"),
    ]
    assert [option.currency for option in options] == ["USD", "USD"]
    assert traveloka_inventory.cheapest_visible_option(options) == options[1]
    assert expensive.inner_text_kwargs == [{"timeout": 123}]
    assert cheapest.inner_text_kwargs == [{"timeout": 123}]
    assert page.locator_calls[0] == "[data-testid^='flight-inventory-card-container-']"


def test_visible_options_from_page_uses_bounded_timeouts_for_text_and_attributes() -> None:
    locator = LiveTravelokaCardLocator(
        container_id="out-1",
        text="VietJet Air\nSGN - BKK\nUSD 120.00",
    )
    page = LocatorFakePage([], option_groups=[[locator]])

    options = traveloka_inventory.visible_options_from_page(page, timeout_ms=123)

    assert len(options) == 1
    assert options[0].key == "out-1"
    assert locator.inner_text_kwargs == [{"timeout": 123}]
    assert all(call["timeout"] == 123 for call in locator.get_attribute_kwargs)


def test_visible_options_from_page_caps_far_future_deadline_to_local_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locator = LiveTravelokaCardLocator(
        container_id="out-1",
        text="VietJet Air\nSGN - BKK\nUSD 120.00",
    )
    page = LocatorFakePage([], option_groups=[[locator]])
    monkeypatch.setattr(browser_helpers, "monotonic", lambda: 0.0)

    options = traveloka_inventory.visible_options_from_page(
        page,
        timeout_ms=123,
        deadline=999.0,
    )

    assert len(options) == 1
    assert locator.inner_text_kwargs == [{"timeout": 123}]
    assert all(call["timeout"] == 123 for call in locator.get_attribute_kwargs)


def test_visible_options_from_page_uses_fresh_remaining_deadline_for_each_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_locator = LiveTravelokaCardLocator(
        container_id="out-1",
        text="VietJet Air\nSGN - BKK\nUSD 120.00",
    )
    second_locator = LiveTravelokaCardLocator(
        container_id="out-2",
        text="VietJet Air\nSGN - BKK\nUSD 140.00",
    )
    page = LocatorFakePage([], option_groups=[[first_locator, second_locator]])
    now_values = iter([9.0, 9.1, 9.2, 9.3])
    monkeypatch.setattr(browser_helpers, "monotonic", lambda: next(now_values))

    options = traveloka_inventory.visible_options_from_page(page, deadline=10.0)

    assert [option.key for option in options] == ["out-1", "out-2"]
    assert first_locator.get_attribute_kwargs[0]["timeout"] == 1000
    assert first_locator.inner_text_kwargs[0]["timeout"] == 900
    assert second_locator.get_attribute_kwargs[0]["timeout"] == 800
    assert second_locator.inner_text_kwargs[0]["timeout"] == 700
