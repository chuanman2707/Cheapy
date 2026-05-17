from __future__ import annotations

from decimal import Decimal

from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import inventory as traveloka_inventory

from .fakes import FakeLocator, ScrollableFakeLocator, _visible_option

def test_traveloka_activation_module_clicks_visible_option() -> None:
    locator = ScrollableFakeLocator()
    option = traveloka_inventory.TravelokaVisibleOption(
        key="out-1",
        airline_name=None,
        departure_time_text=None,
        arrival_time_text=None,
        route_text=None,
        price_amount=Decimal("10.00"),
        currency="USD",
        locator=locator,
    )

    traveloka_activation.click_visible_option(option, timeout_ms=1000)

    assert locator.evaluate_scripts == [
        traveloka_activation.TRAVELOKA_OPTION_ACTIVATION_SCRIPT
    ]
    assert locator.scroll_kwargs[0]["timeout"] == 1000


def test_click_visible_option_dispatches_traveloka_activation_sequence() -> None:
    locator = FakeLocator()
    option = _visible_option(key="out-1", locator=locator)

    traveloka_activation.click_visible_option(option, timeout_ms=3210)

    assert locator.click_kwargs == []
    assert locator.evaluate_calls == [{"timeout": 3210}]
    script = locator.evaluate_scripts[0]
    for event_name in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
        assert event_name in script


def test_click_visible_option_scrolls_and_caps_live_activation_timeout() -> None:
    locator = ScrollableFakeLocator()
    option = _visible_option(key="out-1", locator=locator)

    traveloka_activation.click_visible_option(option, timeout_ms=45_000)

    assert locator.scroll_kwargs == [{"timeout": 10_000}]
    assert locator.evaluate_calls == [{"timeout": 10_000}]
    assert locator.click_kwargs == []
    script = locator.evaluate_scripts[0]
    for event_name in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
        assert event_name in script
