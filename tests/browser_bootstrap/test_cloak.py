from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
import sys
import types
from time import monotonic
from typing import get_type_hints

import pytest

from cheapy.browser_bootstrap import (
    BrowserBootstrapBlocked,
    BrowserBootstrapSession,
    BrowserBootstrapTimeout,
    BrowserBootstrapUnavailable,
    BrowserNetworkCapture,
    BrowserNetworkCaptureUnavailable,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
)
from cheapy.browser_bootstrap import cloak
from tests.browser_bootstrap.fakes import (
    FakeBrowser,
    FakeContext,
    FakePage,
    FakeRequest,
    FakeResponse,
    launcher_for,
)


def test_browser_bootstrap_types_are_frozen_redacted_and_tuple_typed() -> None:
    request = CapturedRequest(
        url="https://example.com/api/search",
        method="POST",
        sequence=7,
        headers={"cookie": "secret-cookie"},
        post_data="raw-body-secret",
    )
    response = CapturedResponse(
        url="https://example.com/api/search",
        status_code=200,
        payload={"token": "secret-payload"},
        sequence=7,
    )
    exchange = CapturedExchange(
        sequence=7,
        captured_monotonic=1.0,
        request=request,
        response=response,
    )
    capture = BrowserNetworkCapture(
        cookie_header="session=secret-cookie",
        user_agent="Secret-UA/1.0",
        exchanges=(exchange,),
        created_monotonic=2.0,
    )
    session = BrowserBootstrapSession(
        cookie_header="session=secret-cookie",
        user_agent="Secret-UA/1.0",
        created_monotonic=3.0,
    )

    request_hints = get_type_hints(CapturedRequest)
    capture_hints = get_type_hints(BrowserNetworkCapture)
    assert request_hints["headers"] == Mapping[str, str]
    assert request_hints["post_data"] == str | None
    assert capture_hints["exchanges"] == tuple[CapturedExchange, ...]
    assert isinstance(capture.exchanges, tuple)
    headers = {"cookie": "secret-cookie"}
    frozen_request = CapturedRequest(
        url="https://example.com/api/search",
        method="POST",
        sequence=8,
        headers=headers,
    )
    headers["cookie"] = "mutated-cookie"
    assert frozen_request.headers["cookie"] == "secret-cookie"
    with pytest.raises(TypeError):
        frozen_request.headers["cookie"] = "other-cookie"  # type: ignore[index]

    with pytest.raises(FrozenInstanceError):
        session.user_agent = "Other-UA"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        request.post_data = "other-body"  # type: ignore[misc]
    for rendered in (
        repr(request),
        repr(response),
        repr(exchange),
        repr(capture),
        repr(session),
    ):
        assert "secret-cookie" not in rendered
        assert "raw-body-secret" not in rendered
        assert "secret-payload" not in rendered
        assert "https://example.com" not in rendered
        assert "Secret-UA" not in rendered


def test_bootstrap_cookies_returns_redacted_session_and_closes_browser() -> None:
    page = FakePage(user_agent="Secret-UA/1.0")
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    session = cloak.bootstrap_cookies(
        "https://example.com/search",
        monotonic() + 5,
        user_agent="Secret-UA/1.0",
        launch_browser=launcher_for(browser),
    )

    assert session.cookie_header == "session=secret-cookie"
    assert session.user_agent == "Secret-UA/1.0"
    assert session.created_monotonic <= monotonic()
    assert context.context_kwargs == {"user_agent": "Secret-UA/1.0"}
    assert page.goto_calls[0]["wait_until"] == "domcontentloaded"
    assert context.closed is True
    assert browser.closed is True
    assert "secret-cookie" not in repr(session)
    assert "Secret-UA" not in repr(session)


def test_capture_first_party_requests_pairs_request_response_and_redacts_repr() -> None:
    request = FakeRequest(
        url="https://example.com/api/search",
        method="POST",
        headers={"cookie": "secret-cookie", "x-api-key": "secret-header"},
        post_data="raw-body-secret",
    )
    response = FakeResponse(
        url="https://example.com/api/search",
        status=200,
        payload={"token": "secret-payload"},
    )
    page = FakePage(events=[request, response], user_agent="Secret-UA/2.0")
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    capture = cloak.capture_first_party_requests(
        "https://example.com/search",
        monotonic() + 5,
        request_predicate=lambda captured: captured.method == "POST",
        response_predicate=lambda captured: captured.status_code == 200,
        launch_browser=launcher_for(browser),
    )

    assert capture.cookie_header == "session=secret-cookie"
    assert capture.user_agent == "Secret-UA/2.0"
    assert len(capture.exchanges) == 1
    exchange = capture.exchanges[0]
    assert exchange.request.url == "https://example.com/api/search"
    assert exchange.request.post_data == "raw-body-secret"
    assert exchange.response is not None
    assert exchange.response.status_code == 200
    assert exchange.response.payload == {"token": "secret-payload"}
    assert exchange.request.sequence == exchange.response.sequence
    assert isinstance(capture.exchanges, tuple)

    for rendered in (
        repr(capture),
        repr(exchange),
        repr(exchange.request),
        repr(exchange.response),
    ):
        assert "secret-cookie" not in rendered
        assert "raw-body-secret" not in rendered
        assert "secret-payload" not in rendered
        assert "https://example.com" not in rendered
        assert "Secret-UA" not in rendered


def test_capture_pairs_duplicate_url_responses_by_underlying_request_identity() -> None:
    first_request = FakeRequest(
        url="https://example.com/api/search",
        method="POST",
        post_data="first-body",
    )
    second_request = FakeRequest(
        url="https://example.com/api/search",
        method="POST",
        post_data="second-body",
    )
    page = FakePage(
        events=[
            first_request,
            second_request,
            FakeResponse(
                url="https://example.com/api/search",
                status=200,
                payload={"id": "second"},
                request=second_request,
            ),
            FakeResponse(
                url="https://example.com/api/search",
                status=200,
                payload={"id": "first"},
                request=first_request,
            ),
        ],
        user_agent="Secret-UA/2.5",
    )
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    capture = cloak.capture_first_party_requests(
        "https://example.com/search",
        monotonic() + 5,
        request_predicate=lambda captured: captured.method == "POST",
        response_predicate=lambda captured: captured.status_code == 200,
        launch_browser=launcher_for(browser),
    )

    assert [exchange.request.post_data for exchange in capture.exchanges] == [
        "first-body",
        "second-body",
    ]
    response_payloads = [
        exchange.response.payload
        for exchange in capture.exchanges
        if exchange.response is not None
    ]
    assert response_payloads == [{"id": "first"}, {"id": "second"}]
    for exchange in capture.exchanges:
        assert exchange.response is not None
        assert exchange.response.sequence == exchange.request.sequence


def test_capture_waits_for_late_request_response_before_reading_cookies() -> None:
    request = FakeRequest(
        url="https://example.com/api/search",
        method="POST",
        post_data="late-body",
    )
    response = FakeResponse(
        url="https://example.com/api/search",
        status=200,
        payload={"id": "late"},
        request=request,
    )
    page = FakePage(
        events=[],
        wait_events=[[request, response]],
        user_agent="Secret-UA/2.75",
    )
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    capture = cloak.capture_first_party_requests(
        "https://example.com/search",
        monotonic() + 5,
        request_predicate=lambda captured: captured.method == "POST",
        response_predicate=lambda captured: captured.status_code == 200,
        launch_browser=launcher_for(browser),
    )

    assert page.wait_calls
    assert len(capture.exchanges) == 1
    assert capture.exchanges[0].request.post_data == "late-body"
    assert capture.exchanges[0].response is not None
    assert capture.exchanges[0].response.payload == {"id": "late"}


def test_capture_with_response_predicate_keeps_unpaired_exchange_when_one_response_matches() -> None:
    unpaired_request = FakeRequest(
        url="https://example.com/api/search",
        method="POST",
        post_data="unpaired-body",
    )
    paired_request = FakeRequest(
        url="https://example.com/api/search",
        method="POST",
        post_data="paired-body",
    )
    page = FakePage(
        events=[
            unpaired_request,
            paired_request,
            FakeResponse(
                url="https://example.com/api/search",
                status=200,
                payload={"id": "paired"},
                request=paired_request,
            ),
        ],
        user_agent="Secret-UA/2.8",
    )
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    capture = cloak.capture_first_party_requests(
        "https://example.com/search",
        monotonic() + 5,
        request_predicate=lambda captured: captured.method == "POST",
        response_predicate=lambda captured: captured.status_code == 200,
        launch_browser=launcher_for(browser),
    )

    assert [exchange.request.post_data for exchange in capture.exchanges] == [
        "unpaired-body",
        "paired-body",
    ]
    assert capture.exchanges[0].response is None
    assert capture.exchanges[1].response is not None
    assert capture.exchanges[1].response.payload == {"id": "paired"}


def test_capture_without_matching_request_raises_safe_unavailable_error() -> None:
    page = FakePage(
        events=[
            FakeResponse(
                url="https://example.com/api/search",
                status=200,
                payload={"token": "secret-payload"},
            )
        ],
        user_agent="Secret-UA/3.0",
    )
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    with pytest.raises(BrowserNetworkCaptureUnavailable) as exc_info:
        cloak.capture_first_party_requests(
            "https://example.com/search",
            monotonic() + 5,
            request_predicate=lambda captured: captured.method == "POST",
            launch_browser=launcher_for(browser),
        )

    error = exc_info.value
    assert error.context.failure_type == "network_capture_unavailable"
    assert error.context.phase == "capture_wait"
    assert "secret-cookie" not in str(error)
    assert "secret-payload" not in str(error)
    assert "https://example.com" not in str(error)
    assert context.closed is True
    assert browser.closed is True


def test_capture_with_response_predicate_requires_matching_response() -> None:
    page = FakePage(
        events=[
            FakeRequest(
                url="https://example.com/api/search",
                method="POST",
                headers={"cookie": "secret-cookie"},
                post_data="raw-body-secret",
            )
        ],
        user_agent="Secret-UA/3.5",
    )
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    with pytest.raises(BrowserNetworkCaptureUnavailable) as exc_info:
        cloak.capture_first_party_requests(
            "https://example.com/search",
            monotonic() + 5,
            request_predicate=lambda captured: captured.method == "POST",
            response_predicate=lambda captured: captured.status_code == 200,
            launch_browser=launcher_for(browser),
        )

    error = exc_info.value
    assert error.context.failure_type == "network_capture_unavailable"
    assert error.context.phase == "capture_wait"
    assert "secret-cookie" not in str(error)
    assert "raw-body-secret" not in str(error)
    assert "https://example.com" not in str(error)
    assert context.closed is True
    assert browser.closed is True


def test_navigation_429_maps_to_rate_limited_failure_type() -> None:
    page = FakePage(user_agent="Secret-UA/3.75", navigation_status=429)
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    with pytest.raises(BrowserBootstrapBlocked) as exc_info:
        cloak.bootstrap_cookies(
            "https://example.com/search",
            monotonic() + 5,
            launch_browser=launcher_for(browser),
        )

    assert exc_info.value.context.failure_type == "rate_limited"
    assert exc_info.value.context.http_status_code == 429


def test_launch_error_maps_to_browser_bootstrap_failed_without_raw_message() -> None:
    def launch_browser(**kwargs: object) -> object:
        raise RuntimeError("raw launch failure https://example.com secret-cookie")

    with pytest.raises(BrowserBootstrapUnavailable) as exc_info:
        cloak.bootstrap_cookies(
            "https://example.com/search",
            monotonic() + 5,
            launch_browser=launch_browser,
        )

    error = exc_info.value
    assert error.context.failure_type == "browser_bootstrap_failed"
    assert error.context.phase == "launch"
    assert error.context.exception_type == "RuntimeError"
    assert "raw launch failure" not in str(error)
    assert "https://example.com" not in str(error)
    assert "secret-cookie" not in str(error)


def test_cleanup_only_failure_raises_safe_unavailable_and_uses_timeout() -> None:
    page = FakePage(user_agent="Secret-UA/3.9")
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
        close_exc=RuntimeError("raw close failure https://example.com secret-cookie"),
    )
    browser = FakeBrowser(context)

    with pytest.raises(BrowserBootstrapUnavailable) as exc_info:
        cloak.bootstrap_cookies(
            "https://example.com/search",
            monotonic() + 5,
            launch_browser=launcher_for(browser),
        )

    error = exc_info.value
    assert error.context.failure_type == "browser_bootstrap_failed"
    assert error.context.phase == "cleanup"
    assert error.context.exception_type == "RuntimeError"
    assert "raw close failure" not in str(error)
    assert "https://example.com" not in str(error)
    assert "secret-cookie" not in str(error)
    assert context.closed is True
    assert browser.closed is True
    assert context.close_timeout is not None
    assert browser.close_timeout is not None


def test_navigation_timeout_maps_to_safe_bootstrap_timeout() -> None:
    page = FakePage(
        user_agent="Secret-UA/4.0",
        goto_exc=TimeoutError("raw timeout message https://example.com secret-cookie"),
    )
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
        close_exc=RuntimeError("raw close failure https://example.com secret-cookie"),
    )
    browser = FakeBrowser(
        context,
        close_exc=RuntimeError("raw browser close failure secret-cookie"),
    )

    with pytest.raises(BrowserBootstrapTimeout) as exc_info:
        cloak.bootstrap_cookies(
            "https://example.com/search",
            monotonic() + 5,
            launch_browser=launcher_for(browser),
        )

    error = exc_info.value
    assert error.context.failure_type == "timeout"
    assert error.context.phase == "navigation"
    assert error.context.exception_type == "TimeoutError"
    assert "raw timeout message" not in str(error)
    assert "https://example.com" not in str(error)
    assert "secret-cookie" not in str(error)
    assert context.closed is True
    assert browser.closed is True


def test_user_agent_read_error_uses_canonical_phase_without_raw_message() -> None:
    class FailingUserAgentPage(FakePage):
        def evaluate(self, script: str) -> str:
            raise RuntimeError("raw user agent failure secret-cookie")

    page = FailingUserAgentPage(user_agent="Secret-UA/4.5")
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

    with pytest.raises(BrowserBootstrapUnavailable) as exc_info:
        cloak.bootstrap_cookies(
            "https://example.com/search",
            monotonic() + 5,
            launch_browser=launcher_for(browser),
        )

    error = exc_info.value
    assert error.context.failure_type == "browser_bootstrap_failed"
    assert error.context.phase == "user_agent_read"
    assert error.context.exception_type == "RuntimeError"
    assert "raw user agent failure" not in str(error)
    assert "secret-cookie" not in str(error)


def test_launch_browser_suppresses_dependency_console_noise(monkeypatch, capsys) -> None:
    fake_module = types.ModuleType("cloakbrowser")

    def fake_launch(**kwargs: object) -> dict[str, object]:
        print("Update available: cloakbrowser")
        print("debug browser setup", file=sys.stderr)
        return {"kwargs": kwargs}

    fake_module.launch = fake_launch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cloakbrowser", fake_module)

    result = cloak.launch_browser(headless=True, timeout=123)

    assert result == {"kwargs": {"headless": True, "timeout": 123}}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
