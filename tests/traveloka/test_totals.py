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


def test_read_final_total_prefers_explicit_selected_total_and_uses_bounded_timeout() -> None:
    stale_total = TextFakeLocator(text="Trip total USD 999.00")
    selected_total = TextFakeLocator(text="Selected final total USD 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "text=/total/i": stale_total,
            "[data-testid*='selected'][data-testid*='total']": selected_total,
        },
    )

    result = traveloka_totals.read_final_total(page, timeout_ms=456)

    assert result == (Decimal("321.09"), "USD")
    assert selected_total.inner_text_kwargs == [{"timeout": 456}]
    assert stale_total.inner_text_kwargs == []


def test_read_final_total_uses_fresh_remaining_deadline_for_each_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_total = TextFakeLocator(text="Selected final total unavailable")
    final_total = TextFakeLocator(text="Final total USD 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_total,
            "[data-testid*='final'][data-testid*='total']": final_total,
        },
    )
    now_values = iter([9.0, 9.0, 9.2, 9.2, 9.2, 9.2])
    monkeypatch.setattr(browser_helpers, "monotonic", lambda: next(now_values))

    result = traveloka_totals.read_final_total(page, deadline=10.0)

    assert result == (Decimal("321.09"), "USD")
    assert selected_total.inner_text_kwargs == [{"timeout": 1000}]
    assert final_total.inner_text_kwargs == [{"timeout": 800}]


def test_read_final_total_reads_explicit_final_total_after_addon() -> None:
    selected_total = TextFakeLocator(text="Addon USD 12.00\nFinal total USD 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_total,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )


def test_read_final_total_reads_scoped_price_only_total() -> None:
    checkout_total = TextFakeLocator(text="USD 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='checkout'][data-testid*='total']": checkout_total,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )


def test_read_final_total_reads_usd_symbol_final_total() -> None:
    final_total = TextFakeLocator(text="Final total US$ 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='final'][data-testid*='total']": final_total,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )


def test_read_final_total_prefers_final_total_over_addon_total() -> None:
    selected_total = TextFakeLocator(text="Addon total USD 12.00\nFinal total USD 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='checkout'][data-testid*='total']": selected_total,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )


def test_read_final_total_ignores_selected_addon_total_before_checkout() -> None:
    selected_addon = TextFakeLocator(text="Addon total USD 12.00")
    checkout_total = TextFakeLocator(text="Checkout total USD 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_addon,
            "[data-testid*='checkout'][data-testid*='total']": checkout_total,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )


def test_read_final_total_ignores_selected_price_only_before_checkout() -> None:
    selected_addon = TextFakeLocator(text="USD 12.00")
    checkout_total = TextFakeLocator(text="Checkout total USD 321.09")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_addon,
            "[data-testid*='checkout'][data-testid*='total']": checkout_total,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )


def test_read_final_total_prefers_checkout_total_over_summary_and_label() -> None:
    checkout_total = TextFakeLocator(text="Addon USD 12.00\nCheckout total USD 321.09")
    selected_summary = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    label_total = TextFakeLocator(text="Total USD 111.00/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='checkout'][data-testid*='total']": checkout_total,
            "[data-testid='bundle-summary-tray']": selected_summary,
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [label_total]
            ),
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )
    assert checkout_total.inner_text_kwargs == [{"timeout": 456}]
    assert selected_summary.inner_text_kwargs == []
    assert label_total.inner_text_kwargs == []


def test_read_final_total_cached_summary_selector_cannot_outrank_selected_total() -> None:
    selected_total = TextFakeLocator(text="Selected final total USD 321.09")
    summary_total = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    cache = traveloka_totals._FinalTotalSelectorCache(
        tier="summary",
        selector="#flight-search-result",
    )
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_total,
            "#flight-search-result": summary_total,
        },
    )

    result = traveloka_totals.read_final_total(
        page,
        timeout_ms=456,
        selector_cache=cache,
    )

    assert result == (Decimal("321.09"), "USD")
    assert selected_total.inner_text_kwargs == [{"timeout": 456}]
    assert summary_total.inner_text_kwargs == []
    assert cache.tier == "selected_total"
    assert cache.selector == "[data-testid*='selected'][data-testid*='total']"


def test_read_final_total_cached_label_selector_cannot_outrank_summary_total() -> None:
    summary_total = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    label_total = TextFakeLocator(text="Total USD 111.00/pax")
    cache = traveloka_totals._FinalTotalSelectorCache(
        tier="global_label",
        selector="[data-testid='label_fl_inventory_price']",
    )
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='bundle-summary-tray']": summary_total,
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [label_total]
            ),
        },
    )

    result = traveloka_totals.read_final_total(
        page,
        timeout_ms=456,
        selector_cache=cache,
    )

    assert result == (Decimal("239.68"), "USD")
    assert summary_total.inner_text_kwargs == [{"timeout": 456}]
    assert label_total.inner_text_kwargs == []
    assert cache.tier == "summary"
    assert cache.selector == "[data-testid='bundle-summary-tray']"


def test_read_final_total_cached_selector_reorders_only_inside_same_tier() -> None:
    final_total = TextFakeLocator(text="Final total USD 321.09")
    selected_total = TextFakeLocator(text="Selected total unavailable")
    cache = traveloka_totals._FinalTotalSelectorCache(
        tier="selected_total",
        selector="[data-testid*='final'][data-testid*='total']",
    )
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='selected'][data-testid*='total']": selected_total,
            "[data-testid*='final'][data-testid*='total']": final_total,
        },
    )

    result = traveloka_totals.read_final_total(
        page,
        timeout_ms=456,
        selector_cache=cache,
    )

    assert result == (Decimal("321.09"), "USD")
    assert final_total.inner_text_kwargs == [{"timeout": 456}]
    assert selected_total.inner_text_kwargs == []


def test_read_final_total_prefers_summary_round_trip_price_over_addon_total() -> None:
    selected_summary = TextFakeLocator(
        text="Addon total USD 12.00\nRound-trip price USD 239.68/pax"
    )
    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='bundle-summary-tray']": selected_summary},
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_final_total_prefers_later_summary_round_trip_price_over_addon_total() -> None:
    addon_summary = TextFakeLocator(text="Addon total USD 12.00")
    selected_summary = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='bundle-summary-tray']": FakeLocatorCollection(
                [addon_summary, selected_summary]
            )
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_final_total_prefers_round_trip_price_across_summary_selectors() -> None:
    addon_summary = TextFakeLocator(text="Addon total USD 12.00")
    selected_summary = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='bundle-summary-tray']": addon_summary,
            "[data-testid*='selected'][data-testid*='summary']": selected_summary,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_locator_texts_waits_on_first_locator_before_counting() -> None:
    first_price = TextFakeLocator(text="Total USD 123.00/pax")
    second_price = TextFakeLocator(text="Total USD 239.68/pax")

    class FirstThenCountCollection:
        def first(self) -> TextFakeLocator:
            return first_price

        def count(self) -> int:
            if not first_price.inner_text_kwargs:
                raise AssertionError("count called before first locator read")
            return 2

        def nth(self, index: int) -> TextFakeLocator:
            return [first_price, second_price][index]

    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='prices']": FirstThenCountCollection()},
    )

    assert browser_helpers.locator_texts(
        page,
        "[data-testid='prices']",
        timeout_ms=456,
        deadline=None,
    ) == ["Total USD 123.00/pax", "Total USD 239.68/pax"]


def test_locator_texts_caps_far_future_deadline_to_local_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    price = TextFakeLocator(text="Total USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='prices']": price},
    )
    monkeypatch.setattr(browser_helpers, "monotonic", lambda: 0.0)

    assert browser_helpers.locator_texts(
        page,
        "[data-testid='prices']",
        timeout_ms=456,
        deadline=999.0,
    ) == ["Total USD 239.68/pax"]
    assert price.inner_text_kwargs == [{"timeout": 456}]


def test_locator_texts_decrements_local_budget_with_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_price = TextFakeLocator(text="Total USD 123.00/pax")
    second_price = TextFakeLocator(text="Total USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='prices']": FakeLocatorCollection([first_price, second_price])
        },
    )
    now_values = iter([0.0, 0.0, 0.2, 0.2, 0.2, 0.2])
    monkeypatch.setattr(browser_helpers, "monotonic", lambda: next(now_values))

    assert browser_helpers.locator_texts(
        page,
        "[data-testid='prices']",
        timeout_ms=456,
        deadline=999.0,
    ) == ["Total USD 123.00/pax", "Total USD 239.68/pax"]
    assert first_price.inner_text_kwargs == [{"timeout": 456}]
    assert second_price.inner_text_kwargs == [{"timeout": 256}]


def test_read_final_total_reads_live_label_total_and_ignores_addon() -> None:
    price_label = TextFakeLocator(text="+ USD 0.00/pax\nTotal USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [price_label]
            )
        },
    )

    result = traveloka_totals.read_final_total(page, timeout_ms=456)

    assert result == (Decimal("239.68"), "USD")
    assert price_label.inner_text_kwargs == [{"timeout": 456}]


@pytest.mark.parametrize(
    "addon_total_text",
    [
        "Addon total USD 12.00",
        "Add-on total USD 12.00",
        "Add-ons total USD 12.00",
    ],
)
def test_read_final_total_reads_live_label_total_after_addon_total(
    addon_total_text: str,
) -> None:
    price_label = TextFakeLocator(text=f"{addon_total_text}\nTotal USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [price_label]
            )
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_final_total_reads_live_label_dotted_vnd_total() -> None:
    price_label = TextFakeLocator(text="+ VND 0/pax\nTotal VND 1.234.567/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [price_label]
            )
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("1234567"),
        "VND",
    )


def test_read_final_total_reads_selected_summary_round_trip_price() -> None:
    selected_summary = TextFakeLocator(
        text=(
            "Departure SGN to BKK\n"
            "Return BKK to SGN\n"
            "Round-trip price USD 239.68/pax"
        )
    )
    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='bundle-summary-tray']": selected_summary},
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_final_total_reads_selected_summary_usd_symbol_round_trip_price() -> None:
    selected_summary = TextFakeLocator(text="Round-trip price US$ 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='bundle-summary-tray']": selected_summary},
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


@pytest.mark.parametrize(
    "addon_total_text",
    [
        "Addon total USD 12.00",
        "Add-on total USD 12.00",
        "Add-ons total USD 12.00",
    ],
)
def test_read_final_total_ignores_summary_addon_total_without_round_trip_price(
    addon_total_text: str,
) -> None:
    selected_summary = TextFakeLocator(text=addon_total_text)
    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='bundle-summary-tray']": selected_summary},
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) is None


def test_read_final_total_prefers_selected_summary_over_global_label_total() -> None:
    unselected_label = TextFakeLocator(text="Total USD 999.00/pax")
    selected_summary = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [unselected_label]
            ),
            "[data-testid='bundle-summary-tray']": selected_summary,
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_final_total_ignores_conflicting_global_label_totals() -> None:
    unselected_label = TextFakeLocator(text="Total USD 999.00/pax")
    selected_label = TextFakeLocator(text="Total USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [unselected_label, selected_label]
            )
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) is None


def test_read_final_total_ignores_duplicate_global_label_totals() -> None:
    first_label = TextFakeLocator(text="Total USD 239.68/pax")
    second_label = TextFakeLocator(text="Total USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [first_label, second_label]
            )
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) is None


def test_read_final_total_ignores_duplicate_label_totals_in_same_text() -> None:
    price_label = TextFakeLocator(
        text="Total USD 239.68/pax\nTotal USD 239.68/pax"
    )
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [price_label]
            )
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) is None


def test_read_final_total_ignores_unselected_round_trip_card_price() -> None:
    unselected_card_price = TextFakeLocator(
        text="Round-trip price USD 999.00/pax\nChoose\nChoose"
    )
    selected_price = TextFakeLocator(text="+ USD 0.00/pax\nTotal USD 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='label_fl_inventory_price']": FakeLocatorCollection(
                [unselected_card_price, selected_price]
            )
        },
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_body_text_caps_timeout_when_deadline_is_far_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = TextFakeLocator(text="Your Flights")
    page = LocatorFakePage([], selector_locators={"body": body})
    monkeypatch.setattr(browser_helpers, "monotonic", lambda: 10.0)

    result = browser_helpers.read_body_text(
        page,
        timeout_ms=250,
        deadline=999.0,
    )

    assert result == "Your Flights"
    assert body.inner_text_kwargs == [{"timeout": 250}]


def test_read_final_total_ignores_ambiguous_generic_total() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={"text=/total/i": TextFakeLocator(text="Trip total USD 999.00")},
    )

    assert traveloka_totals.read_final_total(page, timeout_ms=456) is None
