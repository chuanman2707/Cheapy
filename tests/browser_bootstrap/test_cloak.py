from __future__ import annotations

import sys
import types
from time import monotonic

import pytest

from cheapy.browser_bootstrap import (
    BrowserBootstrapTimeout,
    BrowserNetworkCaptureUnavailable,
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
    assert exchange.request.sequence < exchange.response.sequence

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
    assert error.context.failure_type == "capture_unavailable"
    assert "secret-cookie" not in str(error)
    assert "secret-payload" not in str(error)
    assert "https://example.com" not in str(error)
    assert context.closed is True
    assert browser.closed is True


def test_navigation_timeout_maps_to_safe_bootstrap_timeout() -> None:
    page = FakePage(
        user_agent="Secret-UA/4.0",
        goto_exc=TimeoutError("raw timeout message https://example.com secret-cookie"),
    )
    context = FakeContext(
        page,
        cookies=[{"name": "session", "value": "secret-cookie"}],
    )
    browser = FakeBrowser(context)

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
