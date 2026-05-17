from __future__ import annotations

from decimal import Decimal
from time import monotonic, sleep
from typing import get_type_hints
from urllib.parse import parse_qs, urlparse

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import adapter as traveloka_adapter
from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import browser_helpers
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import results as traveloka_results
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
from cheapy.providers.traveloka import urls as traveloka_urls
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        payload: dict[str, object] | Exception,
        status: int = 200,
    ) -> None:
        self.url = url
        self.status = status
        self._payload = payload

    def json(self) -> dict[str, object]:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeLocator:
    def __init__(self) -> None:
        self.click_kwargs: list[dict[str, object]] = []
        self.evaluate_calls: list[dict[str, object]] = []
        self.evaluate_scripts: list[str] = []
        self.clicked = False

    def click(self, **kwargs: object) -> None:
        self.clicked = True
        self.click_kwargs.append(kwargs)

    def evaluate(self, script: str, **kwargs: object) -> None:
        self.clicked = True
        self.evaluate_scripts.append(script)
        self.evaluate_calls.append(kwargs)


class ScrollableFakeLocator(FakeLocator):
    def __init__(self) -> None:
        super().__init__()
        self.scroll_kwargs: list[dict[str, object]] = []

    def scroll_into_view_if_needed(self, **kwargs: object) -> None:
        self.scroll_kwargs.append(kwargs)


class EmittingFakeLocator(FakeLocator):
    def __init__(self, on_click: object | None = None) -> None:
        super().__init__()
        self._on_click = on_click

    def click(self, **kwargs: object) -> None:
        super().click(**kwargs)
        if callable(self._on_click):
            self._on_click()

    def evaluate(self, script: str, **kwargs: object) -> None:
        super().evaluate(script, **kwargs)
        if callable(self._on_click):
            self._on_click()


class TextFakeLocator(EmittingFakeLocator):
    def __init__(
        self,
        *,
        text: str,
        attrs: dict[str, str] | None = None,
        on_click: object | None = None,
    ) -> None:
        super().__init__(on_click)
        self.text = text
        self.attrs = attrs or {}
        self.inner_text_kwargs: list[dict[str, object]] = []
        self.get_attribute_kwargs: list[dict[str, object]] = []

    def locator(self, selector: str) -> TextFakeLocator:
        return self

    def inner_text(self, **kwargs: object) -> str:
        self.inner_text_kwargs.append(kwargs)
        return self.text

    def get_attribute(self, name: str, **kwargs: object) -> str | None:
        self.get_attribute_kwargs.append({"name": name, **kwargs})
        return self.attrs.get(name)

    def first(self) -> TextFakeLocator:
        return self


class LiveTravelokaCardLocator(TextFakeLocator):
    def __init__(
        self,
        *,
        container_id: str,
        text: str,
        button: TextFakeLocator | None = None,
    ) -> None:
        super().__init__(
            text=text,
            attrs={"data-testid": f"flight-inventory-card-container-{container_id}"},
        )
        self.button = button or TextFakeLocator(
            text="Choose\nChoose",
            attrs={"data-testid": "flight-inventory-card-button", "role": "button"},
        )

    def locator(self, selector: str) -> object:
        if (
            "flight-inventory-card-button" in selector
            or "[role='button']" in selector
            or '[role="button"]' in selector
        ):
            return FakeLocatorCollection([self.button])
        return super().locator(selector)


class EmptyFakeLocator:
    def first(self) -> EmptyFakeLocator:
        return self

    def inner_text(self, **kwargs: object) -> str:
        raise RuntimeError("locator did not match")


class FakeLocatorCollection:
    def __init__(self, locators: list[TextFakeLocator]) -> None:
        self.locators = locators

    def count(self) -> int:
        return len(self.locators)

    def nth(self, index: int) -> TextFakeLocator:
        return self.locators[index]

    def first(self) -> TextFakeLocator:
        return self.nth(0)


class FakePage:
    def __init__(self, responses: list[FakeResponse], content: str | None = None) -> None:
        self.responses = responses
        self.handlers: dict[str, object] = {}
        self.goto_urls: list[str] = []
        self.wait_calls = 0
        self.url = ""
        self._content = content or "<html><body>flight search</body></html>"

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.url = url
        self.goto_urls.append(url)
        handler = self.handlers["response"]
        for response in self.responses:
            handler(response)

    def emit_response(self, response: FakeResponse) -> None:
        handler = self.handlers["response"]
        handler(response)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls += 1

    def content(self) -> str:
        return self._content


class LocatorFakePage(FakePage):
    def __init__(
        self,
        responses: list[FakeResponse],
        *,
        option_groups: list[list[TextFakeLocator]] | None = None,
        selector_locators: dict[str, object] | None = None,
    ) -> None:
        super().__init__(responses)
        self.option_groups = option_groups or []
        self.selector_locators = selector_locators or {}
        self.locator_calls: list[str] = []

    def locator(self, selector: str) -> object:
        self.locator_calls.append(selector)
        if selector == traveloka_inventory.INVENTORY_CARD_SELECTOR:
            if self.option_groups:
                return FakeLocatorCollection(self.option_groups.pop(0))
            if selector in self.selector_locators:
                return self.selector_locators[selector]
            return FakeLocatorCollection([])
        if selector in self.selector_locators:
            return self.selector_locators[selector]
        return EmptyFakeLocator()


class LiveTravelokaInventoryPage(FakePage):
    def __init__(self, cards: list[LiveTravelokaCardLocator]) -> None:
        super().__init__([])
        self.cards = cards
        self.locator_calls: list[str] = []

    def locator(self, selector: str) -> object:
        self.locator_calls.append(selector)
        if selector == traveloka_inventory.INVENTORY_CARD_SELECTOR:
            return FakeLocatorCollection(self.cards)
        return EmptyFakeLocator()


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False
        self.context_kwargs: dict[str, object] | None = None

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False
        self.launch_kwargs: dict[str, object] | None = None

    def new_context(self, **kwargs: object) -> FakeContext:
        self.context.context_kwargs = kwargs
        return self.context

    def close(self) -> None:
        self.closed = True


def _browser_for(page: FakePage) -> tuple[FakeContext, FakeBrowser]:
    context = FakeContext(page)
    return context, FakeBrowser(context)


def _completed_payload() -> dict[str, object]:
    return {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": "tv-1"}],
        }
    }


def _visible_option(
    *,
    key: str | None,
    airline_name: str | None = "VietJet Air",
    price_amount: Decimal = Decimal("100"),
    currency: str | None = "USD",
    locator: object | None = None,
):
    return traveloka_inventory.TravelokaVisibleOption(
        key=key,
        airline_name=airline_name,
        departure_time_text="09:00",
        arrival_time_text="10:35",
        route_text="SGN-BKK",
        price_amount=price_amount,
        currency=currency,
        locator=locator if locator is not None else FakeLocator(),
    )


def _inventory_card_option(
    *,
    key: str,
    amount: str = "100.00",
    currency: str = "USD",
    airline: str = "Traveloka Air",
    route_text: str = "SGN - BKK",
    on_click: object | None = None,
) -> LiveTravelokaCardLocator:
    button = TextFakeLocator(
        text="Choose",
        attrs={"data-testid": "flight-inventory-card-button", "role": "button"},
        on_click=on_click,
    )
    return LiveTravelokaCardLocator(
        container_id=key,
        text=f"{airline}\n{route_text}\n{currency} {amount}",
        button=button,
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
