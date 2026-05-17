from __future__ import annotations

from decimal import Decimal

import pytest

from cheapy.providers.traveloka import browser_helpers
from cheapy.providers.traveloka import totals as traveloka_totals

from .fakes import FakeLocatorCollection, LocatorFakePage, TextFakeLocator

def test_traveloka_totals_module_reads_final_total() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid*='checkout'][data-testid*='total']": FakeLocatorCollection(
                [TextFakeLocator(text="Checkout total USD 321.09")]
            )
        },
    )

    assert traveloka_totals.read_final_total(page) == (Decimal("321.09"), "USD")


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
