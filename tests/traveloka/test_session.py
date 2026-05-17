from __future__ import annotations

from time import monotonic

import pytest

from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka import session as traveloka_session
from cheapy.providers.traveloka.errors import TravelokaProviderError
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


class FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.goto_calls: list[dict[str, object]] = []

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append(
            {"url": url, "wait_until": wait_until, "timeout": timeout}
        )


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    def new_context(self, **kwargs: object) -> FakeContext:
        assert kwargs == {"locale": "en-US"}
        return self.context

    def close(self) -> None:
        self.closed = True


class FailingContextBrowser:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.closed = False

    def new_context(self, **kwargs: object) -> object:
        assert kwargs == {"locale": "en-US"}
        raise self.exc

    def close(self) -> None:
        self.closed = True


class FailingGotoPage(FakePage):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        raise self.exc


def test_open_browser_session_sets_up_page_and_cleans_up() -> None:
    page = FakePage()
    context = FakeContext(page)
    browser = FakeBrowser(context)
    launches: list[dict[str, object]] = []

    def launch_browser(**kwargs: object) -> FakeBrowser:
        launches.append(kwargs)
        return browser

    recorder = TravelokaPhaseRecorder(clock=monotonic)

    with traveloka_session.open_browser_session(
        _request(),
        base_url="https://www.traveloka.com",
        timeout_seconds=5.0,
        launch_browser=launch_browser,
        phase_recorder=recorder,
    ) as opened:
        assert opened.page is page
        assert opened.state.best_result is None
        assert opened.deadline > monotonic()
        assert "response" in page.handlers
        assert page.goto_calls
        assert "ap=SGN.BKK" in page.goto_calls[0]["url"]

    assert context.closed is True
    assert browser.closed is True
    assert launches and launches[0]["headless"] is True
    assert {record.phase for record in recorder.records} >= {
        "browser_launch",
        "context_page_setup",
        "initial_navigation",
        "cleanup",
    }


def test_open_browser_session_maps_launch_failure_to_provider_error() -> None:
    def fail_launch(**kwargs: object) -> object:
        raise RuntimeError("browser not installed")

    with pytest.raises(TravelokaProviderError) as exc_info:
        with traveloka_session.open_browser_session(
            _request(),
            base_url="https://www.traveloka.com",
            timeout_seconds=1.0,
            launch_browser=fail_launch,
            phase_recorder=TravelokaPhaseRecorder(clock=monotonic),
        ):
            raise AssertionError("session should not open")

    assert exc_info.value.failure_type == "browser_unavailable"
    assert exc_info.value.exception_type == "RuntimeError"


def test_open_browser_session_maps_context_setup_failure_to_navigation_failed() -> None:
    browser = FailingContextBrowser(ValueError("secret context failure"))

    def launch_browser(**kwargs: object) -> FailingContextBrowser:
        return browser

    with pytest.raises(TravelokaProviderError) as exc_info:
        with traveloka_session.open_browser_session(
            _request(),
            base_url="https://www.traveloka.com",
            timeout_seconds=1.0,
            launch_browser=launch_browser,
            phase_recorder=TravelokaPhaseRecorder(clock=monotonic),
        ):
            raise AssertionError("session should not open")

    assert exc_info.value.failure_type == "navigation_failed"
    assert exc_info.value.exception_type == "ValueError"
    assert browser.closed is True


def test_open_browser_session_maps_navigation_failure_to_navigation_failed() -> None:
    page = FailingGotoPage(RuntimeError("secret navigation failure"))
    context = FakeContext(page)
    browser = FakeBrowser(context)

    def launch_browser(**kwargs: object) -> FakeBrowser:
        return browser

    with pytest.raises(TravelokaProviderError) as exc_info:
        with traveloka_session.open_browser_session(
            _request(),
            base_url="https://www.traveloka.com",
            timeout_seconds=1.0,
            launch_browser=launch_browser,
            phase_recorder=TravelokaPhaseRecorder(clock=monotonic),
        ):
            raise AssertionError("session should not open")

    assert exc_info.value.failure_type == "navigation_failed"
    assert exc_info.value.exception_type == "RuntimeError"
    assert context.closed is True
    assert browser.closed is True


def test_open_browser_session_maps_timeout_like_navigation_failure_to_timeout() -> None:
    page = FailingGotoPage(TimeoutError("secret timeout detail"))
    context = FakeContext(page)
    browser = FakeBrowser(context)

    def launch_browser(**kwargs: object) -> FakeBrowser:
        return browser

    with pytest.raises(TravelokaProviderError) as exc_info:
        with traveloka_session.open_browser_session(
            _request(),
            base_url="https://www.traveloka.com",
            timeout_seconds=1.0,
            launch_browser=launch_browser,
            phase_recorder=TravelokaPhaseRecorder(clock=monotonic),
        ):
            raise AssertionError("session should not open")

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.exception_type == "TimeoutError"
    assert context.closed is True
    assert browser.closed is True


def test_open_browser_session_does_not_map_user_code_after_yield() -> None:
    page = FakePage()
    context = FakeContext(page)
    browser = FakeBrowser(context)

    def launch_browser(**kwargs: object) -> FakeBrowser:
        return browser

    with pytest.raises(RuntimeError, match="workflow failed"):
        with traveloka_session.open_browser_session(
            _request(),
            base_url="https://www.traveloka.com",
            timeout_seconds=1.0,
            launch_browser=launch_browser,
            phase_recorder=TravelokaPhaseRecorder(clock=monotonic),
        ):
            raise RuntimeError("workflow failed")

    assert context.closed is True
    assert browser.closed is True
