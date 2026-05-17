from __future__ import annotations

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.traveloka import adapter as traveloka_adapter
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.results import TravelokaCaptureResult

from .fakes import (
    FakeBrowser,
    FakeContext,
    FakePage,
    FakeResponse,
    _browser_for,
    _completed_payload,
    _one_way_request,
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
