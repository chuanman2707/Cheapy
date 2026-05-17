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
    state = traveloka_capture.CaptureState()
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


def test_traveloka_capture_state_lives_in_capture_module() -> None:
    state = traveloka_capture.CaptureState()
    response = FakeResponse(
        url="https://www.traveloka.com/api/v2/flight/search/initial",
        payload={"data": {"meta": {"searchCompleted": True}, "searchResults": []}},
    )

    state.handle_response(response)

    assert state.completed is True
    assert state.best_result is not None
    assert state.best_result.source_path == "/api/v2/flight/search/initial"
    assert not hasattr(traveloka_adapter, "CaptureState")
    assert not hasattr(traveloka_adapter, "explicit_payload_item_ids")
    assert not hasattr(traveloka_adapter, "wait_for_capture")


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

    monkeypatch.setattr(traveloka_capture, "CaptureState", SeededCaptureState)
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

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True


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

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
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

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
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

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
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

    with pytest.raises(traveloka_errors.TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == failure_type
    assert exc_info.value.error_code == error_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.http_status_code == status
