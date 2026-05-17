from __future__ import annotations

from decimal import Decimal
from time import sleep
from typing import get_type_hints
from urllib.parse import parse_qs, urlparse

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import adapter as traveloka_adapter
from cheapy.providers.traveloka import results as traveloka_results
from cheapy.providers.traveloka.adapter import TravelokaAdapter, TravelokaProviderError
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
        if selector.startswith("button:has-text"):
            if not self.option_groups:
                return FakeLocatorCollection([])
            return FakeLocatorCollection(self.option_groups.pop(0))
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
        if selector == "[data-testid^='flight-inventory-card-container-']":
            return FakeLocatorCollection(self.cards)
        if selector.startswith("button:has-text"):
            return FakeLocatorCollection([])
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
    return traveloka_adapter.TravelokaVisibleOption(
        key=key,
        airline_name=airline_name,
        departure_time_text="09:00",
        arrival_time_text="10:35",
        route_text="SGN-BKK",
        price_amount=price_amount,
        currency=currency,
        locator=locator if locator is not None else FakeLocator(),
    )


def test_build_full_search_url_maps_one_way_request_to_traveloka_route() -> None:
    url = traveloka_adapter.build_full_search_url(
        _one_way_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.traveloka.com"
    assert parsed.path == "/en-en/flight/fulltwosearch"
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026"]
    assert params["ps"] == ["1.0.0"]
    assert params["sc"] == ["ECONOMY"]
    assert params["funnelSource"] == ["SEO-Homepage-SearchForm"]


def test_build_full_search_url_maps_round_trip_request_to_traveloka_route() -> None:
    url = traveloka_adapter.build_full_search_url(
        _round_trip_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    params = parse_qs(urlparse(url).query)
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026.17-7-2026"]
    assert params["ps"] == ["1.0.0"]
    assert params["sc"] == ["ECONOMY"]


def test_capture_result_carries_completion_and_timeout_state() -> None:
    result = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
        timed_out=True,
    )

    assert result.payload == {"data": {"searchResults": []}}
    assert result.source_path == "/api/v2/flight/search/initial"
    assert result.search_completed is False
    assert result.timed_out is True


def test_capture_result_carries_safe_partial_failure_metadata() -> None:
    result = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
        timed_out=False,
        partial_failure_type="final_round_trip_total_unavailable",
    )

    assert result.partial_failure_type == "final_round_trip_total_unavailable"
    assert "http" not in result.partial_failure_type


def test_selected_round_trip_result_carries_final_total_and_safe_paths() -> None:
    result = TravelokaSelectedRoundTripResult(
        outbound_payload={"data": {"searchResults": [{"id": "out-1"}]}},
        return_payload={"data": {"searchResults": [{"id": "ret-1"}]}},
        selected_outbound_key="out-1",
        selected_return_key="ret-1",
        final_total_amount=Decimal("321.09"),
        final_total_currency="USD",
        source_paths=(
            "/api/v2/flight/search/initial",
            "/api/v2/flight/search/poll",
        ),
    )

    assert result.final_total_amount == Decimal("321.09")
    assert result.final_total_currency == "USD"
    assert result.selected_outbound_key == "out-1"
    assert result.selected_return_key == "ret-1"
    assert all(path.startswith("/api/") for path in result.source_paths)
    assert all("?" not in path for path in result.source_paths)


def test_traveloka_result_contracts_live_in_results_module() -> None:
    capture = traveloka_results.TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    partial = traveloka_results.partial_round_trip_result(
        capture,
        "return_capture_timeout",
    )
    selected = traveloka_results.TravelokaSelectedRoundTripResult(
        outbound_payload={"data": {"searchResults": [{"id": "out-1"}]}},
        return_payload={"data": {"searchResults": [{"id": "ret-1"}]}},
        selected_outbound_key="out-1",
        selected_return_key="ret-1",
        final_total_amount=Decimal("123.45"),
        final_total_currency="USD",
        source_paths=("/api/v2/flight/search/initial", "/api/v2/flight/search/poll"),
    )

    assert partial.partial_failure_type == "return_capture_timeout"
    assert partial.payload == capture.payload
    assert selected.final_total_currency == "USD"


def test_phase_recorder_records_safe_phase_without_sensitive_metadata() -> None:
    now_values = iter([10.0, 10.125])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))

    with recorder.phase("initial_navigation"):
        pass

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "initial_navigation"
    assert record.duration_ms == 125
    assert record.success is True
    assert record.failure_type is None
    assert record.count is None
    assert not hasattr(record, "url")
    assert not hasattr(record, "headers")
    assert not hasattr(record, "payload")
    assert not hasattr(record, "cookies")


def test_phase_recorder_records_safe_failure_type() -> None:
    now_values = iter([20.0, 20.25])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))
    error = traveloka_adapter.TravelokaProviderError(
        failure_type="navigation_failed",
        message_en="Traveloka navigation failed at https://example.invalid/path",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
    )

    with pytest.raises(traveloka_adapter.TravelokaProviderError):
        with recorder.phase("initial_navigation"):
            raise error

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "initial_navigation"
    assert record.duration_ms == 250
    assert record.success is False
    assert record.failure_type == "navigation_failed"
    assert "example.invalid" not in str(record)


def test_phase_recorder_uses_safe_exception_type_without_message() -> None:
    now_values = iter([30.0, 30.5])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))

    with pytest.raises(RuntimeError):
        with recorder.phase("context_page_setup"):
            raise RuntimeError("failed at https://example.invalid/private")

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "context_page_setup"
    assert record.duration_ms == 500
    assert record.success is False
    assert record.failure_type == "runtimeerror"
    assert "example.invalid" not in str(record)
    assert "private" not in str(record)


def test_adapter_phase_timings_exposes_recorder_without_response_mutation() -> None:
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: object())
    result = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )

    assert adapter.phase_timings == ()

    with adapter._phase_recorder.phase("context_page_setup"):
        pass

    assert adapter.phase_timings[0].phase == "context_page_setup"
    assert result == TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    assert not hasattr(result, "phase_timings")


def test_cheapest_visible_option_returns_none_for_empty_options() -> None:
    assert traveloka_adapter._cheapest_visible_option([]) is None


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

    cheapest = traveloka_adapter._cheapest_visible_option(options)

    assert cheapest is not None
    assert cheapest.key == "a-option"


def test_visible_option_optional_text_fields_accept_none_and_tie_break_safely() -> None:
    type_hints = get_type_hints(traveloka_adapter.TravelokaVisibleOption)
    for field_name in (
        "key",
        "airline_name",
        "departure_time_text",
        "arrival_time_text",
        "route_text",
        "currency",
    ):
        assert type_hints[field_name] == str | None

    option_with_none_fields = traveloka_adapter.TravelokaVisibleOption(
        key=None,
        airline_name=None,
        departure_time_text=None,
        arrival_time_text=None,
        route_text=None,
        price_amount=Decimal("90"),
        currency=None,
        locator=FakeLocator(),
    )
    other_option = traveloka_adapter.TravelokaVisibleOption(
        key="a-option",
        airline_name=None,
        departure_time_text="09:00",
        arrival_time_text="10:35",
        route_text="SGN-BKK",
        price_amount=Decimal("90"),
        currency="USD",
        locator=FakeLocator(),
    )

    cheapest = traveloka_adapter._cheapest_visible_option(
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

    cheapest = traveloka_adapter._cheapest_visible_option(
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

    cheapest = traveloka_adapter._cheapest_visible_option(
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
    amount, currency = traveloka_adapter._parse_visible_price(text)

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
    amount, currency = traveloka_adapter._parse_visible_price(text)

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
        traveloka_adapter._bind_visible_option_to_payload(
            _visible_option(key="out-offer-2"),
            payload,
        )
        == "out-offer-2"
    )
    assert (
        traveloka_adapter._bind_visible_option_to_payload(
            _visible_option(key="1"),
            payload,
        )
        is None
    )
    assert (
        traveloka_adapter._bind_visible_option_to_payload(
            _visible_option(key="missing"),
            payload,
        )
        is None
    )


def test_bind_visible_option_to_payload_ignores_numeric_payload_ids() -> None:
    payload = {"data": {"searchResults": [{"id": 1}]}}

    assert (
        traveloka_adapter._bind_visible_option_to_payload(
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

    options = traveloka_adapter._visible_options_from_page(page, timeout_ms=123)

    assert [option.key for option in options] == [
        "eva-expensive",
        "qatar-cheapest",
    ]
    assert [option.price_amount for option in options] == [
        Decimal("4307.31"),
        Decimal("1663.60"),
    ]
    assert [option.currency for option in options] == ["USD", "USD"]
    assert traveloka_adapter._cheapest_visible_option(options) == options[1]
    assert expensive.inner_text_kwargs == [{"timeout": 123}]
    assert cheapest.inner_text_kwargs == [{"timeout": 123}]
    assert page.locator_calls[0] == "[data-testid^='flight-inventory-card-container-']"


def test_visible_options_from_page_uses_bounded_timeouts_for_text_and_attributes() -> None:
    locator = TextFakeLocator(
        text="VietJet Air\nSGN - BKK\nUSD 120.00",
        attrs={"data-testid": "out-1"},
    )
    page = LocatorFakePage([], option_groups=[[locator]])

    options = traveloka_adapter._visible_options_from_page(page, timeout_ms=123)

    assert len(options) == 1
    assert options[0].key == "out-1"
    assert locator.inner_text_kwargs == [{"timeout": 123}]
    assert all(call["timeout"] == 123 for call in locator.get_attribute_kwargs)


def test_visible_options_from_page_caps_far_future_deadline_to_local_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locator = TextFakeLocator(
        text="VietJet Air\nSGN - BKK\nUSD 120.00",
        attrs={"data-testid": "out-1"},
    )
    page = LocatorFakePage([], option_groups=[[locator]])
    monkeypatch.setattr(traveloka_adapter, "monotonic", lambda: 0.0)

    options = traveloka_adapter._visible_options_from_page(
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
    first_locator = TextFakeLocator(
        text="VietJet Air\nSGN - BKK\nUSD 120.00",
        attrs={"data-testid": "out-1"},
    )
    second_locator = TextFakeLocator(
        text="VietJet Air\nSGN - BKK\nUSD 140.00",
        attrs={"data-testid": "out-2"},
    )
    page = LocatorFakePage([], option_groups=[[first_locator, second_locator]])
    now_values = iter([9.0, 9.1, 9.2, 9.3])
    monkeypatch.setattr(traveloka_adapter, "monotonic", lambda: next(now_values))

    options = traveloka_adapter._visible_options_from_page(page, deadline=10.0)

    assert [option.key for option in options] == ["out-1", "out-2"]
    assert first_locator.get_attribute_kwargs[0]["timeout"] == 1000
    assert first_locator.inner_text_kwargs[0]["timeout"] == 900
    assert second_locator.get_attribute_kwargs[0]["timeout"] == 800
    assert second_locator.inner_text_kwargs[0]["timeout"] == 700


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

    result = traveloka_adapter._read_final_total(page, timeout_ms=456)

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
    monkeypatch.setattr(traveloka_adapter, "monotonic", lambda: next(now_values))

    result = traveloka_adapter._read_final_total(page, deadline=10.0)

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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
        Decimal("321.09"),
        "USD",
    )
    assert checkout_total.inner_text_kwargs == [{"timeout": 456}]
    assert selected_summary.inner_text_kwargs == []
    assert label_total.inner_text_kwargs == []


def test_read_final_total_cached_summary_selector_cannot_outrank_selected_total() -> None:
    selected_total = TextFakeLocator(text="Selected final total USD 321.09")
    summary_total = TextFakeLocator(text="Round-trip price USD 239.68/pax")
    cache = traveloka_adapter._FinalTotalSelectorCache(
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

    result = traveloka_adapter._read_final_total(
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
    cache = traveloka_adapter._FinalTotalSelectorCache(
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

    result = traveloka_adapter._read_final_total(
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
    cache = traveloka_adapter._FinalTotalSelectorCache(
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

    result = traveloka_adapter._read_final_total(
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._locator_texts(
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
    monkeypatch.setattr(traveloka_adapter, "monotonic", lambda: 0.0)

    assert traveloka_adapter._locator_texts(
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
    monkeypatch.setattr(traveloka_adapter, "monotonic", lambda: next(now_values))

    assert traveloka_adapter._locator_texts(
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

    result = traveloka_adapter._read_final_total(page, timeout_ms=456)

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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_final_total_reads_selected_summary_usd_symbol_round_trip_price() -> None:
    selected_summary = TextFakeLocator(text="Round-trip price US$ 239.68/pax")
    page = LocatorFakePage(
        [],
        selector_locators={"[data-testid='bundle-summary-tray']": selected_summary},
    )

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) is None


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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) is None


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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) is None


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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) is None


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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) == (
        Decimal("239.68"),
        "USD",
    )


def test_read_body_text_caps_timeout_when_deadline_is_far_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = TextFakeLocator(text="Your Flights")
    page = LocatorFakePage([], selector_locators={"body": body})
    monkeypatch.setattr(traveloka_adapter, "monotonic", lambda: 10.0)

    result = traveloka_adapter._read_body_text(
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

    assert traveloka_adapter._read_final_total(page, timeout_ms=456) is None


def test_wait_for_return_selection_transition_recognizes_selected_summary() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
                text="Return BKK to SGN\nChange return flight"
            )
        },
    )

    assert (
        traveloka_adapter._wait_for_return_selection_transition(
            page,
            deadline=traveloka_adapter.monotonic() + 1,
            poll_interval_seconds=0.001,
        )
        is True
    )


def test_wait_for_return_selection_transition_times_out_without_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = LocatorFakePage([], selector_locators={"body": TextFakeLocator(text="Choose")})
    monkeypatch.setattr(
        traveloka_adapter,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )

    assert (
        traveloka_adapter._wait_for_return_selection_transition(
            page,
            deadline=traveloka_adapter.monotonic() + 1,
            poll_interval_seconds=0.001,
        )
        is False
    )


def test_wait_for_return_selection_transition_ignores_preexisting_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker_text = "Return BKK to SGN\nChange return flight"
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
                text=marker_text
            ),
            "body": TextFakeLocator(text=f"Choose\n{marker_text}"),
        },
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )

    assert (
        traveloka_adapter._wait_for_return_selection_transition(
            page,
            deadline=traveloka_adapter.monotonic() + 1,
            poll_interval_seconds=0.001,
            before_marker_texts=(marker_text,),
            before_body_text=f"Choose\n{marker_text}",
        )
        is False
    )


def test_click_visible_option_dispatches_traveloka_activation_sequence() -> None:
    locator = FakeLocator()
    option = _visible_option(key="out-1", locator=locator)

    traveloka_adapter._click_visible_option(option, timeout_ms=3210)

    assert locator.click_kwargs == []
    assert locator.evaluate_calls == [{"timeout": 3210}]
    script = locator.evaluate_scripts[0]
    for event_name in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
        assert event_name in script


def test_click_visible_option_scrolls_and_caps_live_activation_timeout() -> None:
    locator = ScrollableFakeLocator()
    option = _visible_option(key="out-1", locator=locator)

    traveloka_adapter._click_visible_option(option, timeout_ms=45_000)

    assert locator.scroll_kwargs == [{"timeout": 10_000}]
    assert locator.evaluate_calls == [{"timeout": 10_000}]
    assert locator.click_kwargs == []
    script = locator.evaluate_scripts[0]
    for event_name in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
        assert event_name in script


def test_adapter_captures_completed_initial_fare_payload() -> None:
    payload = _completed_payload()
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/log",
                payload={"ignored": True},
            ),
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            ),
        ]
    )
    context, browser = _browser_for(page)
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: browser)

    result = adapter.search_exact_one_way(_one_way_request())

    assert result == TravelokaCaptureResult(
        payload=payload,
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
        timed_out=False,
    )
    assert page.goto_urls[0].startswith(
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
    )
    assert context.context_kwargs == {"locale": "en-US"}
    assert context.closed is True
    assert browser.closed is True


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
        traveloka_adapter,
        "_wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_read_final_total",
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
        traveloka_adapter,
        "_wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_read_final_total",
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
    ) -> list[traveloka_adapter.TravelokaVisibleOption]:
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
        traveloka_adapter,
        "_visible_options_from_page",
        visible_options,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_read_final_total",
        lambda page_arg, **kwargs: (Decimal("321.09"), "USD"),
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
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
        traveloka_adapter,
        "_wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    monkeypatch.setattr(traveloka_adapter, "_read_final_total", read_final_total)
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
        traveloka_adapter,
        "_wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    monkeypatch.setattr(traveloka_adapter, "_read_final_total", read_final_total)
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
                TextFakeLocator(
                    text="VietJet Air\nSGN - BKK\nUSD 120.00",
                    attrs={"data-testid": "out-1"},
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                )
            ],
            [
                TextFakeLocator(
                    text="VietJet Air\nBKK - SGN\nUSD 110.00",
                    attrs={"data-testid": "ret-1"},
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
        traveloka_adapter,
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
                TextFakeLocator(
                    text="VietJet Air\nSGN - BKK\nUSD 120.00",
                    attrs={"data-testid": "out-1"},
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                )
            ],
            [
                TextFakeLocator(
                    text="VietJet Air\nBKK - SGN\nUSD 110.00",
                    attrs={"data-testid": "ret-1"},
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
                TextFakeLocator(
                    text="Vietnam Airlines\nSGN - BKK\nUSD 120.00",
                    attrs={"data-testid": "out-1"},
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                )
            ],
            [
                TextFakeLocator(
                    text="Vietnam Airlines\nBKK - SGN\nUSD 110.00",
                    attrs={"data-testid": "ret-1"},
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
                TextFakeLocator(
                    text="Sky High\nSGN - BKK\nUSD 240.00",
                    attrs={"data-testid": "out-expensive"},
                ),
                TextFakeLocator(
                    text="VietJet Air\nSGN - BKK\nUSD 120.00",
                    attrs={"data-testid": "out-cheap"},
                    on_click=lambda: page.emit_response(
                        FakeResponse(
                            url="https://www.traveloka.com/api/v2/flight/search/poll",
                            payload=return_payload,
                        )
                    ),
                ),
            ],
            [
                TextFakeLocator(
                    text="Sky High\nBKK - SGN\nUSD 230.00",
                    attrs={"data-testid": "ret-expensive"},
                ),
                TextFakeLocator(
                    text="VietJet Air\nBKK - SGN\nUSD 110.00",
                    attrs={"data-testid": "ret-cheap"},
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
        traveloka_adapter,
        "_wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "_wait_for_capture",
        wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_read_final_total",
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
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
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
        traveloka_adapter,
        "_visible_options_from_page",
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


def test_outbound_transition_requires_exact_selected_url_fragment() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    page.url = "https://www.traveloka.com/en-en/flight/fulltwosearch#SCtv-10"

    assert (
        traveloka_adapter._outbound_selection_transitioned(
            page,
            "tv-1",
            before_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
            before_body_text="Your Flights",
        )
        is False
    )


def test_outbound_transition_accepts_exact_selected_fragment_from_different_baseline() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    page.url = "https://www.traveloka.com/en-en/flight/fulltwosearch#SCtv-1"

    assert (
        traveloka_adapter._outbound_selection_transitioned(
            page,
            "tv-1",
            before_url="https://www.traveloka.com/en-en/flight/fulltwosearch#SCtv-10",
            before_body_text="Your Flights",
        )
        is True
    )


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
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "_visible_options_from_page",
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
        traveloka_adapter,
        "_visible_options_from_page",
        lambda page_arg, **kwargs: options.pop(0),
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_read_final_total",
        lambda page_arg, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_adapter,
        "_wait_for_return_selection_transition",
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


def test_adapter_captures_completed_poll_fare_payload() -> None:
    payload = _completed_payload()
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=payload,
            )
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result == TravelokaCaptureResult(
        payload=payload,
        source_path="/api/v2/flight/search/poll",
        search_completed=True,
        timed_out=False,
    )


def test_adapter_keeps_non_empty_payload_when_completion_frame_is_empty() -> None:
    non_empty_payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [{"id": "tv-1"}],
        }
    }
    empty_completed_payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [],
        }
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=non_empty_payload,
            ),
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=empty_completed_payload,
            ),
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result == TravelokaCaptureResult(
        payload=non_empty_payload,
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
        timed_out=False,
    )


def test_capture_state_preserves_partial_failure_type_when_completion_upgrades_prior_result() -> None:
    partial_failure_type = "final_round_trip_total_unavailable"
    non_empty_payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [{"id": "tv-1"}],
        }
    }
    empty_completed_payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [],
        }
    }
    state = traveloka_adapter._CaptureState()
    state.best_result = TravelokaCaptureResult(
        payload=non_empty_payload,
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
        timed_out=False,
        partial_failure_type=partial_failure_type,
    )

    state.handle_response(
        FakeResponse(
            url="https://www.traveloka.com/api/v2/flight/search/poll",
            payload=empty_completed_payload,
        )
    )

    assert state.best_result is not None
    assert state.best_result.search_completed is True
    assert state.best_result.partial_failure_type == partial_failure_type


def test_adapter_uses_empty_completion_payload_when_no_offers_were_seen() -> None:
    empty_incomplete_payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [],
        }
    }
    empty_completed_payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [],
        }
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=empty_incomplete_payload,
            ),
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/poll",
                payload=empty_completed_payload,
            ),
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result == TravelokaCaptureResult(
        payload=empty_completed_payload,
        source_path="/api/v2/flight/search/poll",
        search_completed=True,
        timed_out=False,
    )


def test_adapter_returns_partial_payload_when_timeout_happens_after_offers() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [{"id": "tv-1"}],
        }
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == payload
    assert result.search_completed is False
    assert result.timed_out is True
    assert page.wait_calls > 0


def test_adapter_preserves_partial_failure_type_when_returning_timed_out_partial_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial_failure_type = "final_round_trip_total_unavailable"
    payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [{"id": "tv-1"}],
        }
    }

    class SeededCaptureState:
        def __init__(self) -> None:
            self.best_result = TravelokaCaptureResult(
                payload=payload,
                source_path="/api/v2/flight/search/initial",
                search_completed=False,
                timed_out=False,
                partial_failure_type=partial_failure_type,
            )
            self.completed = False

        def handle_response(self, response: object) -> None:
            return

    monkeypatch.setattr(traveloka_adapter, "_CaptureState", SeededCaptureState)
    page = FakePage([])
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == payload
    assert result.timed_out is True
    assert result.partial_failure_type == partial_failure_type


def test_adapter_raises_timeout_when_no_fare_payload_arrives() -> None:
    page = FakePage([])
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True


def test_adapter_maps_browser_launch_failure_to_browser_unavailable() -> None:
    def fail_launch(**kwargs: object) -> object:
        raise RuntimeError("raw launch secret")

    adapter = TravelokaAdapter(launch_browser=fail_launch)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "browser_unavailable"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is True
    assert "raw launch secret" not in str(exc_info.value)


def test_adapter_maps_browser_launch_timeout_to_timeout() -> None:
    def fail_launch(**kwargs: object) -> object:
        raise TimeoutError("raw launch timeout secret")

    adapter = TravelokaAdapter(launch_browser=fail_launch)

    with pytest.raises(TravelokaProviderError) as exc_info:
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

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert page.goto_urls == []
    assert context.closed is False
    assert browser.closed is True


@pytest.mark.parametrize(
    "ignored_url",
    [
        "https://www.traveloka.com/api/log",
        "https://www.traveloka.com/api/analytics",
        "https://www.traveloka.com/api/profile",
        "https://www.traveloka.com/api/autocomplete",
    ],
)
def test_adapter_ignores_non_fare_endpoints(ignored_url: str) -> None:
    payload = _completed_payload()
    page = FakePage(
        [
            FakeResponse(url=ignored_url, payload={"data": {"calendarPrices": []}}),
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            ),
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == payload


def test_adapter_ignores_supported_path_from_non_traveloka_host() -> None:
    page = FakePage(
        [
            FakeResponse(
                url="https://not-traveloka.example/api/v2/flight/search/initial",
                payload=_completed_payload(),
            )
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT


def test_adapter_rejects_unsupported_json_on_fare_endpoint() -> None:
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload={"data": {"calendarPrices": []}},
            )
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "unsupported_response"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is False


def test_adapter_rejects_invalid_json_from_fare_endpoint() -> None:
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=ValueError("raw json secret"),
            )
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "invalid_json"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is False
    assert "raw json secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("status", "failure_type", "error_code", "retryable"),
    [
        (401, "blocked", ErrorCode.PROVIDER_BLOCKED, False),
        (403, "blocked", ErrorCode.PROVIDER_BLOCKED, False),
        (429, "rate_limited", ErrorCode.PROVIDER_RATE_LIMITED, True),
        (500, "transport_error", ErrorCode.PROVIDER_FAILED, True),
        (503, "transport_error", ErrorCode.PROVIDER_FAILED, True),
    ],
)
def test_adapter_maps_fare_endpoint_http_status(
    status: int,
    failure_type: str,
    error_code: ErrorCode,
    retryable: bool,
) -> None:
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                status=status,
                payload={"raw": "body"},
            )
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page))
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == failure_type
    assert exc_info.value.error_code == error_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.http_status_code == status


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

    with pytest.raises(TravelokaProviderError) as exc_info:
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

    with pytest.raises(TravelokaProviderError) as exc_info:
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

    with pytest.raises(TravelokaProviderError) as exc_info:
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

    with pytest.raises(TravelokaProviderError) as exc_info:
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

    with pytest.raises(TravelokaProviderError) as exc_info:
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

    with pytest.raises(TravelokaProviderError) as exc_info:
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
