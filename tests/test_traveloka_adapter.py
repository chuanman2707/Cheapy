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
from cheapy.providers.traveloka.adapter import TravelokaAdapter, TravelokaProviderError


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

    def click(self, **kwargs: object) -> None:
        self.click_kwargs.append(kwargs)


class FakePage:
    def __init__(self, responses: list[FakeResponse], content: str | None = None) -> None:
        self.responses = responses
        self.handlers: dict[str, object] = {}
        self.goto_urls: list[str] = []
        self.wait_calls = 0
        self._content = content or "<html><body>flight search</body></html>"

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_urls.append(url)
        handler = self.handlers["response"]
        for response in self.responses:
            handler(response)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls += 1

    def content(self) -> str:
        return self._content


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
    result = traveloka_adapter.TravelokaCaptureResult(
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
    result = traveloka_adapter.TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
        timed_out=False,
        partial_failure_type="final_round_trip_total_unavailable",
    )

    assert result.partial_failure_type == "final_round_trip_total_unavailable"
    assert "http" not in result.partial_failure_type


def test_selected_round_trip_result_carries_final_total_and_safe_paths() -> None:
    result = traveloka_adapter.TravelokaSelectedRoundTripResult(
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


def test_click_visible_option_delegates_timeout_to_locator_click() -> None:
    locator = FakeLocator()
    option = _visible_option(key="out-1", locator=locator)

    traveloka_adapter._click_visible_option(option, timeout_ms=3210)

    assert locator.click_kwargs == [{"timeout": 3210}]


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

    assert result == traveloka_adapter.TravelokaCaptureResult(
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

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert result == traveloka_adapter.TravelokaCaptureResult(
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

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert result == traveloka_adapter.TravelokaCaptureResult(
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
    state.best_result = traveloka_adapter.TravelokaCaptureResult(
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

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert result == traveloka_adapter.TravelokaCaptureResult(
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
            self.best_result = traveloka_adapter.TravelokaCaptureResult(
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
