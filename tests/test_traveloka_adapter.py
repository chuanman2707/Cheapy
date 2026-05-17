from __future__ import annotations

from decimal import Decimal
from time import sleep

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)


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


def test_round_trip_default_waits_conservatively_for_capture_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage([])
    captures = [
        TravelokaCaptureResult(
            payload={
                "data": {
                    "meta": {"searchCompleted": True},
                    "searchResults": [{"id": "out-1"}],
                }
            },
            source_path="/api/v2/flight/search/initial",
            search_completed=True,
        ),
        TravelokaCaptureResult(
            payload={
                "data": {
                    "meta": {"searchCompleted": True},
                    "searchResults": [{"id": "ret-1"}],
                }
            },
            source_path="/api/v2/flight/search/poll",
            search_completed=True,
        ),
    ]
    capture_calls = 0
    options = [
        [_visible_option(key="out-1")],
        [_visible_option(key="ret-1")],
    ]

    def wait_for_capture(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
    ) -> object:
        nonlocal capture_calls
        capture_calls += 1
        return captures.pop(0)

    monkeypatch.setattr(
        traveloka_capture,
        "wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_totals,
        "read_final_total",
        lambda page_arg, **kwargs: (Decimal("321.09"), "USD"),
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaSelectedRoundTripResult)
    assert capture_calls == 2


def test_round_trip_fast_env_is_ignored_and_uses_conservative_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage([])
    captures = [
        TravelokaCaptureResult(
            payload={
                "data": {
                    "meta": {"searchCompleted": True},
                    "searchResults": [{"id": "out-1"}],
                }
            },
            source_path="/api/v2/flight/search/initial",
            search_completed=True,
        ),
        TravelokaCaptureResult(
            payload={
                "data": {
                    "meta": {"searchCompleted": True},
                    "searchResults": [{"id": "ret-1"}],
                }
            },
            source_path="/api/v2/flight/search/poll",
            search_completed=True,
        ),
    ]
    capture_calls = 0
    options = [
        [_visible_option(key="out-1")],
        [_visible_option(key="ret-1")],
    ]

    def wait_for_capture(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
    ) -> object:
        nonlocal capture_calls
        capture_calls += 1
        return captures.pop(0)

    monkeypatch.setenv("TRAVELOKA_FAST_STABLE_OPTIONS", "1")
    monkeypatch.setattr(
        traveloka_capture,
        "wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_totals,
        "read_final_total",
        lambda page_arg, **kwargs: (Decimal("321.09"), "USD"),
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaSelectedRoundTripResult)
    assert result.selected_outbound_key == "out-1"
    assert result.selected_return_key == "ret-1"
    assert capture_calls == 2


def test_round_trip_selects_cheapest_visible_outbound_and_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": "out-expensive"}, {"id": "out-cheap"}],
        }
    }
    return_payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": "ret-expensive"}, {"id": "ret-cheap"}],
        }
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ]
    )
    outbound_expensive_click = EmittingFakeLocator()
    outbound_cheap_click = EmittingFakeLocator(
        lambda: page.emit_response(
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=return_payload,
            )
        )
    )
    return_expensive_click = EmittingFakeLocator()
    return_cheap_click = EmittingFakeLocator()
    visible_call_count = 0

    def visible_options(
        page_arg: object,
        **kwargs: object,
    ) -> list[traveloka_inventory.TravelokaVisibleOption]:
        nonlocal visible_call_count
        visible_call_count += 1
        if visible_call_count == 1:
            return [
                _visible_option(
                    key="out-expensive",
                    price_amount=Decimal("220.00"),
                    locator=outbound_expensive_click,
                ),
                _visible_option(
                    key="out-cheap",
                    price_amount=Decimal("120.00"),
                    locator=outbound_cheap_click,
                ),
            ]
        return [
            _visible_option(
                key="ret-expensive",
                price_amount=Decimal("210.00"),
                locator=return_expensive_click,
            ),
            _visible_option(
                key="ret-cheap",
                price_amount=Decimal("110.00"),
                locator=return_cheap_click,
            ),
        ]

    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        visible_options,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_totals,
        "read_final_total",
        lambda page_arg, **kwargs: (Decimal("321.09"), "USD"),
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_return_selection_transition",
        lambda page_arg, deadline, **kwargs: True,
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaSelectedRoundTripResult)
    assert result.outbound_payload == outbound_payload
    assert result.return_payload == return_payload
    assert result.selected_outbound_key == "out-cheap"
    assert result.selected_return_key == "ret-cheap"
    assert result.final_total_amount == Decimal("321.09")
    assert result.final_total_currency == "USD"
    assert result.source_paths == (
        "/api/v2/flight/search/initial",
        "/api/v2/flight/search/poll",
    )
    assert outbound_expensive_click.clicked is False
    assert outbound_cheap_click.clicked is True
    assert return_expensive_click.clicked is False
    assert return_cheap_click.clicked is True


def test_round_trip_uses_bounded_timeout_for_final_total_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    page = FakePage([])
    captures = [
        TravelokaCaptureResult(
            payload=outbound_payload,
            source_path="/api/v2/flight/search/initial",
            search_completed=True,
        ),
        TravelokaCaptureResult(
            payload=return_payload,
            source_path="/api/v2/flight/search/poll",
            search_completed=True,
        ),
    ]
    options = [
        [_visible_option(key="out-1", locator=EmittingFakeLocator())],
        [_visible_option(key="ret-1", locator=EmittingFakeLocator())],
    ]
    final_total_kwargs: list[dict[str, object]] = []

    def wait_for_capture(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
    ) -> TravelokaCaptureResult:
        return captures.pop(0)

    def read_final_total(
        page_arg: object,
        **kwargs: object,
    ) -> tuple[Decimal, str]:
        final_total_kwargs.append(kwargs)
        return Decimal("321.09"), "USD"

    monkeypatch.setattr(
        traveloka_capture,
        "wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    monkeypatch.setattr(traveloka_totals, "read_final_total", read_final_total)
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=3,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaSelectedRoundTripResult)
    assert final_total_kwargs
    assert final_total_kwargs[0]["timeout_ms"] <= 250


def test_round_trip_polls_until_final_total_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    page = FakePage([])
    captures = [
        TravelokaCaptureResult(
            payload=outbound_payload,
            source_path="/api/v2/flight/search/initial",
            search_completed=True,
        ),
        TravelokaCaptureResult(
            payload=return_payload,
            source_path="/api/v2/flight/search/poll",
            search_completed=True,
        ),
    ]
    options = [
        [_visible_option(key="out-1", locator=EmittingFakeLocator())],
        [_visible_option(key="ret-1", locator=EmittingFakeLocator())],
    ]
    final_total_results: list[tuple[Decimal, str] | None] = [
        None,
        (Decimal("321.09"), "USD"),
    ]

    def wait_for_capture(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
    ) -> TravelokaCaptureResult:
        return captures.pop(0)

    def read_final_total(
        page_arg: object,
        **kwargs: object,
    ) -> tuple[Decimal, str] | None:
        return final_total_results.pop(0)

    monkeypatch.setattr(
        traveloka_capture,
        "wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    monkeypatch.setattr(traveloka_totals, "read_final_total", read_final_total)
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=3,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaSelectedRoundTripResult)
    assert page.wait_calls >= 1


def test_round_trip_rejects_preexisting_return_marker_without_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    marker_text = "Return BKK to SGN\nChange return flight"
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ],
        option_groups=[
            [
                _inventory_card_option(
                    key="out-1",
                    amount="120.00",
                    airline="VietJet Air",
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                )
            ],
            [
                _inventory_card_option(
                    key="ret-1",
                    amount="110.00",
                    airline="VietJet Air",
                    route_text="BKK - SGN",
                )
            ],
        ],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
                text=marker_text
            ),
            "[data-testid*='selected'][data-testid*='total']": TextFakeLocator(
                text="Selected final total USD 321.09"
            ),
        },
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == outbound_payload
    assert result.partial_failure_type == "final_round_trip_total_unavailable"


def test_round_trip_rejects_stale_summary_total_after_return_transition() -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    body = TextFakeLocator(text="Choose")
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ],
        option_groups=[
            [
                _inventory_card_option(
                    key="out-1",
                    amount="120.00",
                    airline="VietJet Air",
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                )
            ],
            [
                _inventory_card_option(
                    key="ret-1",
                    amount="110.00",
                    airline="VietJet Air",
                    route_text="BKK - SGN",
                    on_click=lambda: setattr(
                        body,
                        "text",
                        "Choose\nChange return flight",
                    ),
                )
            ],
        ],
        selector_locators={
            "body": body,
            "[data-testid='bundle-summary-tray']": TextFakeLocator(
                text="Round-trip price USD 999.00/pax"
            ),
        },
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == outbound_payload
    assert result.partial_failure_type == "final_round_trip_total_unavailable"


def test_round_trip_reads_live_flight_search_result_summary_total() -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    body = TextFakeLocator(text="Choose")
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ],
        option_groups=[
            [
                _inventory_card_option(
                    key="out-1",
                    amount="120.00",
                    airline="Vietnam Airlines",
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                )
            ],
            [
                _inventory_card_option(
                    key="ret-1",
                    amount="110.00",
                    airline="Vietnam Airlines",
                    route_text="BKK - SGN",
                    on_click=lambda: (
                        setattr(
                            body,
                            "text",
                            (
                                "Change return flight\n"
                                "Round-trip price USD 250.86/pax\n"
                                "SGN - BKK USD 250.86/pax\n"
                                "BKK - SGN USD 0.00/pax"
                            ),
                        ),
                        page.selector_locators.update(
                            {
                                "#flight-search-result": TextFakeLocator(
                                    text=(
                                        "Your Flights\n"
                                        "Change departure flight\n"
                                        "Change return flight\n"
                                        "Round-trip price USD 250.86/pax\n"
                                        "SGN - BKK USD 250.86/pax\n"
                                        "BKK - SGN USD 0.00/pax"
                                    )
                                )
                            }
                        ),
                    ),
                )
            ],
        ],
        selector_locators={"body": body},
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaSelectedRoundTripResult)
    assert result.selected_outbound_key == "out-1"
    assert result.selected_return_key == "ret-1"
    assert result.final_total_amount == Decimal("250.86")
    assert result.final_total_currency == "USD"


def test_round_trip_default_helpers_bind_locator_attributes_and_select_final_total() -> None:
    outbound_payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": "out-expensive"}, {"id": "out-cheap"}],
        }
    }
    return_payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": "ret-expensive"}, {"id": "ret-cheap"}],
        }
    }
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ],
        option_groups=[
            [
                _inventory_card_option(
                    key="out-expensive",
                    amount="240.00",
                    airline="Sky High",
                ),
                _inventory_card_option(
                    key="out-cheap",
                    amount="120.00",
                    airline="VietJet Air",
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                ),
            ],
            [
                _inventory_card_option(
                    key="ret-expensive",
                    amount="230.00",
                    airline="Sky High",
                    route_text="BKK - SGN",
                ),
                _inventory_card_option(
                    key="ret-cheap",
                    amount="110.00",
                    airline="VietJet Air",
                    route_text="BKK - SGN",
                    on_click=lambda: page.selector_locators.update(
                        {
                            "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
                                text="Return BKK to SGN\nChange return flight"
                            ),
                            "[data-testid*='selected'][data-testid*='total']": TextFakeLocator(
                                text="Selected final total USD 321.09"
                            ),
                        }
                    ),
                ),
            ],
        ],
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaSelectedRoundTripResult)
    assert result.selected_outbound_key == "out-cheap"
    assert result.selected_return_key == "ret-cheap"
    assert result.final_total_amount == Decimal("321.09")
    assert result.final_total_currency == "USD"


def test_round_trip_returns_timeout_partial_when_outbound_capture_is_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [{"id": "out-1"}],
        }
    }
    page = FakePage([])
    outbound_click = EmittingFakeLocator()

    def wait_for_capture(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
    ) -> TravelokaCaptureResult:
        return TravelokaCaptureResult(
            payload=payload,
            source_path="/api/v2/flight/search/initial",
            search_completed=False,
            timed_out=True,
        )

    monkeypatch.setattr(
        traveloka_capture,
        "wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: [_visible_option(key="out-1", locator=outbound_click)],
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type is None
    assert result.timed_out is True
    assert outbound_click.clicked is False


def test_round_trip_returns_return_capture_partial_when_return_capture_is_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": False}, "searchResults": [{"id": "ret-1"}]}
    }
    body = TextFakeLocator(text="Your Flights")
    page = LocatorFakePage(
        [],
        selector_locators={"body": body},
    )
    outbound_click = EmittingFakeLocator(
        lambda: setattr(body, "text", "Your Flights\nChange departure flight")
    )
    return_click = EmittingFakeLocator()
    captures = [
        TravelokaCaptureResult(
            payload=outbound_payload,
            source_path="/api/v2/flight/search/initial",
            search_completed=True,
            timed_out=False,
        ),
        TravelokaCaptureResult(
            payload=return_payload,
            source_path="/api/v2/flight/search/poll",
            search_completed=False,
            timed_out=True,
        ),
    ]
    options = [
        [_visible_option(key="out-1", locator=outbound_click)],
        [_visible_option(key="ret-1", locator=return_click)],
    ]

    def wait_for_capture(
        state: object,
        page_arg: object,
        deadline: float,
        *,
        poll_interval_seconds: float,
    ) -> TravelokaCaptureResult:
        return captures.pop(0)

    monkeypatch.setattr(
        traveloka_capture,
        "wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
    )
    monkeypatch.setattr(
        traveloka_totals,
        "read_final_total",
        lambda page_arg, **kwargs: (Decimal("321.09"), "USD"),
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == outbound_payload
    assert result.source_path == "/api/v2/flight/search/initial"
    assert result.partial_failure_type == "return_capture_timeout"
    assert return_click.clicked is False


def test_round_trip_returns_partial_when_outbound_selection_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ]
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: [],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "outbound_selection_unavailable"


def test_round_trip_returns_partial_when_selected_outbound_cannot_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ]
    )
    outbound_click = EmittingFakeLocator()
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(
                key="missing-outbound",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "selected_outbound_binding_unavailable"
    assert outbound_click.clicked is False


def test_round_trip_returns_partial_when_return_capture_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    body = TextFakeLocator(text="Your Flights")
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ],
        selector_locators={"body": body},
    )
    outbound_click = EmittingFakeLocator(
        lambda: setattr(body, "text", "Your Flights\nChange departure flight")
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(
                key="tv-1",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "return_capture_timeout"
    assert outbound_click.clicked is True


def test_round_trip_returns_partial_when_outbound_activation_does_not_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    outbound_click = EmittingFakeLocator()
    monkeypatch.setattr(
        traveloka_selection,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(
                key="tv-1",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "outbound_selection_transition_unavailable"
    assert outbound_click.clicked is True


def test_round_trip_keeps_return_capture_timeout_after_outbound_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    body = TextFakeLocator(text="Your Flights")
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ],
        selector_locators={"body": body},
    )
    outbound_click = EmittingFakeLocator(
        lambda: setattr(body, "text", "Your Flights\nChange departure flight")
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(
                key="tv-1",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "return_capture_timeout"
    assert outbound_click.clicked is True


def test_round_trip_ignores_duplicate_outbound_payload_after_noop_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    outbound_click = EmittingFakeLocator(
        lambda: page.emit_response(
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=payload,
            )
        )
    )
    monkeypatch.setattr(
        traveloka_selection,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: [
            _visible_option(
                key="tv-1",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ],
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "outbound_selection_transition_unavailable"
    assert outbound_click.clicked is True


def test_round_trip_ignores_preexisting_selected_hash_after_noop_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _completed_payload()
    page = LocatorFakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    outbound_click = EmittingFakeLocator(
        lambda: setattr(
            page,
            "url",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?noop=1#SCtv-1",
        )
    )
    monkeypatch.setattr(
        traveloka_selection,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )

    def visible_options(page_arg: object, **kwargs: object) -> list[object]:
        page.url = "https://www.traveloka.com/en-en/flight/fulltwosearch#SCtv-1"
        return [
            _visible_option(
                key="tv-1",
                price_amount=Decimal("120.00"),
                locator=outbound_click,
            )
        ]

    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        visible_options,
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == payload
    assert result.partial_failure_type == "outbound_selection_transition_unavailable"
    assert outbound_click.clicked is True






def test_round_trip_returns_partial_when_return_selection_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ]
    )
    outbound_click = EmittingFakeLocator(
        lambda: page.emit_response(
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=return_payload,
            )
        )
    )
    options = [
        [_visible_option(key="out-1", locator=outbound_click)],
        [],
    ]
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == outbound_payload
    assert result.source_path == "/api/v2/flight/search/initial"
    assert result.partial_failure_type == "return_selection_unavailable"
    assert outbound_click.clicked is True


def test_round_trip_returns_partial_when_selected_return_cannot_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ]
    )
    outbound_click = EmittingFakeLocator(
        lambda: page.emit_response(
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=return_payload,
            )
        )
    )
    return_click = EmittingFakeLocator()
    options = [
        [_visible_option(key="out-1", locator=outbound_click)],
        [_visible_option(key="missing-return", locator=return_click)],
    ]
    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == outbound_payload
    assert result.source_path == "/api/v2/flight/search/initial"
    assert result.partial_failure_type == "selected_return_binding_unavailable"
    assert return_click.clicked is False


def test_round_trip_returns_partial_when_final_total_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "out-1"}]}
    }
    return_payload = {
        "data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "ret-1"}]}
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=outbound_payload,
            )
        ]
    )
    outbound_click = EmittingFakeLocator(
        lambda: page.emit_response(
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=return_payload,
            )
        )
    )
    return_click = EmittingFakeLocator()
    options = [
        [_visible_option(key="out-1", locator=outbound_click)],
        [_visible_option(key="ret-1", locator=return_click)],
    ]

    monkeypatch.setattr(
        traveloka_inventory,
        "visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_totals,
        "read_final_total",
        lambda page_arg, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_selection,
        "wait_for_return_selection_transition",
        lambda page_arg, deadline, **kwargs: True,
        raising=False,
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert isinstance(result, TravelokaCaptureResult)
    assert result.payload == outbound_payload
    assert result.source_path == "/api/v2/flight/search/initial"
    assert result.partial_failure_type == "final_round_trip_total_unavailable"
    assert outbound_click.clicked is True
    assert return_click.clicked is True


















def test_adapter_maps_browser_launch_failure_to_browser_unavailable() -> None:
    def fail_launch(**kwargs: object) -> object:
        raise RuntimeError("raw launch secret")

    adapter = TravelokaAdapter(launch_browser=fail_launch)

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "browser_unavailable"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is True
    assert "raw launch secret" not in str(exc_info.value)


def test_adapter_maps_browser_launch_timeout_to_timeout() -> None:
    def fail_launch(**kwargs: object) -> object:
        raise TimeoutError("raw launch timeout secret")

    adapter = TravelokaAdapter(launch_browser=fail_launch)

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True
    assert "raw launch timeout secret" not in str(exc_info.value)


def test_adapter_passes_timeout_to_browser_launch() -> None:
    payload = _completed_payload()
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ]
    )
    captured_kwargs: list[dict[str, object]] = []

    def launch(**kwargs: object) -> FakeBrowser:
        captured_kwargs.append(kwargs)
        return FakeBrowser(FakeContext(page))

    adapter = TravelokaAdapter(
        launch_browser=launch,
        timeout_seconds=2,
    )

    adapter.search_exact_one_way(_one_way_request())

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["headless"] is True
    assert isinstance(captured_kwargs[0]["timeout"], int | float)
    assert 0 < captured_kwargs[0]["timeout"] <= 2000


def test_adapter_checks_deadline_after_launch_before_navigation() -> None:
    page = FakePage([])
    context, browser = _browser_for(page)

    def slow_launch(**kwargs: object) -> FakeBrowser:
        sleep(0.02)
        return browser

    adapter = TravelokaAdapter(
        launch_browser=slow_launch,
        timeout_seconds=0.001,
        poll_interval_seconds=0.001,
    )

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert page.goto_urls == []
    assert context.closed is False
    assert browser.closed is True












def test_adapter_closes_browser_when_response_handler_raises_provider_error() -> None:
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                status=403,
                payload={"raw": "body"},
            )
        ]
    )
    context, browser = _browser_for(page)
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: browser)

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "blocked"
    assert context.closed is True
    assert browser.closed is True


def test_adapter_blocks_terminal_captcha_page_when_no_payload_arrives() -> None:
    page = FakePage([], content="<html><body>captcha required</body></html>")
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.retryable is False


def test_adapter_maps_navigation_failure_after_launch() -> None:
    class FailingPage(FakePage):
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            raise RuntimeError("raw navigation secret")

    page = FailingPage([])
    context, browser = _browser_for(page)
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: browser)

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "navigation_failed"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is True
    assert "raw navigation secret" not in str(exc_info.value)
    assert context.closed is True
    assert browser.closed is True


def test_adapter_maps_navigation_timeout_after_launch() -> None:
    class TimeoutPage(FakePage):
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            raise TimeoutError("raw navigation timeout secret")

    page = TimeoutPage([])
    context, browser = _browser_for(page)
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: browser)

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True
    assert "raw navigation timeout secret" not in str(exc_info.value)
    assert context.closed is True
    assert browser.closed is True


def test_adapter_maps_context_timeout_after_launch() -> None:
    class TimeoutBrowser(FakeBrowser):
        def new_context(self, **kwargs: object) -> FakeContext:
            raise TimeoutError("raw context timeout secret")

    page = FakePage([])
    context = FakeContext(page)
    browser = TimeoutBrowser(context)
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: browser)

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True
    assert "raw context timeout secret" not in str(exc_info.value)
    assert context.closed is False
    assert browser.closed is True


def test_adapter_does_not_classify_runtime_error_message_as_timeout() -> None:
    class FailingPage(FakePage):
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            raise RuntimeError("timeout configuration invalid")

    page = FailingPage([])
    context, browser = _browser_for(page)
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: browser)

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "navigation_failed"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert context.closed is True
    assert browser.closed is True


def test_adapter_rejects_invalid_timeout_seconds() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        TravelokaAdapter(timeout_seconds=0)


@pytest.mark.parametrize("poll_interval_seconds", [0, -0.01])
def test_adapter_rejects_invalid_poll_interval_seconds(
    poll_interval_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        TravelokaAdapter(poll_interval_seconds=poll_interval_seconds)
