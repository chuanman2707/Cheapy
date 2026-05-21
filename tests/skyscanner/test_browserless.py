from __future__ import annotations

import inspect
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from cheapy.providers.skyscanner import browserless
from cheapy.providers.skyscanner.browserless import (
    BrowserlessResponse,
    BrowserlessSession,
    UrlopenBrowserlessClient,
    bootstrap_session,
    cookies_to_header,
)
from cheapy.providers.skyscanner.errors import SkyscannerProviderError


class FakeBrowserlessClient:
    def __init__(self, payload: object | Exception, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[dict[str, object]] = []

    def post_bql(
        self,
        *,
        endpoint: str,
        token: str,
        query: str,
        timeout_seconds: float,
    ) -> BrowserlessResponse:
        self.calls.append(
            {
                "endpoint": endpoint,
                "token": token,
                "query": query,
                "timeout_seconds": timeout_seconds,
            }
        )
        if isinstance(self.payload, Exception):
            raise self.payload
        return BrowserlessResponse(status_code=self.status_code, payload=self.payload)


def test_browserless_module_does_not_import_httpx() -> None:
    assert "httpx" not in inspect.getsource(browserless)


def test_cookies_to_header_joins_safe_cookie_pairs() -> None:
    cookies = [
        {"name": "traveller_context", "value": "abc"},
        {"name": "__Secure-anon_token", "value": "secret"},
        {"name": "", "value": "ignored"},
        {"name": "empty", "value": ""},
        {"name": "bad", "value": None},
        {"name": None, "value": "bad"},
    ]

    assert cookies_to_header(cookies) == "traveller_context=abc; __Secure-anon_token=secret"


def test_sensitive_values_are_omitted_from_repr() -> None:
    session = BrowserlessSession(
        cookie_header="traveller_context=secret-cookie",
        user_agent="Browserless-UA",
    )
    response = BrowserlessResponse(
        status_code=200,
        payload={"data": {"cookies": [{"name": "token", "value": "raw-payload-secret"}]}},
    )

    assert "secret-cookie" not in repr(session)
    assert "raw-payload-secret" not in repr(response)


def test_bootstrap_session_extracts_cookie_and_user_agent() -> None:
    client = FakeBrowserlessClient(
        {
            "data": {
                "cookies": [
                    {"name": "traveller_context", "value": "abc"},
                    {"name": "__Secure-anon_token", "value": "secret"},
                ],
                "userAgent": "Browserless-UA",
            }
        }
    )

    session = bootstrap_session(
        env={"BROWSERLESS_TOKEN": "browserless-secret-token"},
        client=client,
    )

    assert session == BrowserlessSession(
        cookie_header="traveller_context=abc; __Secure-anon_token=secret",
        user_agent="Browserless-UA",
    )
    assert client.calls[0]["token"] == "browserless-secret-token"
    assert "browserless-secret-token" not in str(client.calls[0]["query"])


def test_bootstrap_session_extracts_nested_browserql_cookies() -> None:
    client = FakeBrowserlessClient(
        {
            "data": {
                "cookies": {
                    "cookies": [
                        {"name": "traveller_context", "value": "abc"},
                        {"name": "__Secure-anon_token", "value": "secret"},
                    ]
                },
                "userAgent": {"time": 12},
            }
        }
    )

    session = bootstrap_session(
        env={"BROWSERLESS_TOKEN": "browserless-secret-token"},
        client=client,
    )

    assert session.cookie_header == (
        "traveller_context=abc; __Secure-anon_token=secret"
    )
    assert session.user_agent == browserless.DEFAULT_BROWSERLESS_USER_AGENT


def test_bootstrap_session_query_matches_browserql_cookie_schema() -> None:
    client = FakeBrowserlessClient(
        {
            "data": {
                "cookies": {"cookies": [{"name": "traveller_context", "value": "abc"}]},
            }
        }
    )

    bootstrap_session(
        env={"BROWSERLESS_TOKEN": "browserless-secret-token"},
        client=client,
    )

    query = str(client.calls[0]["query"])
    assert "userAgent(userAgent:" in query
    assert "cookies { cookies { name value } }" in query
    assert browserless.DEFAULT_BROWSERLESS_USER_AGENT in query
    assert "browserless-secret-token" not in query


def test_bootstrap_session_escapes_page_url_in_query_without_token() -> None:
    client = FakeBrowserlessClient(
        {
            "data": {
                "cookies": {"cookies": [{"name": "traveller_context", "value": "abc"}]},
                "userAgent": "Browserless-UA",
            }
        }
    )

    bootstrap_session(
        env={"BROWSERLESS_TOKEN": "browserless-secret-token"},
        client=client,
        page_url='https://example.invalid/path"with\\chars',
    )

    query = str(client.calls[0]["query"])
    assert 'goto(url: "https://example.invalid/path\\"with\\\\chars"' in query
    assert "browserless-secret-token" not in query


def test_bootstrap_session_maps_missing_cookie_without_leaking_token() -> None:
    client = FakeBrowserlessClient({"data": {"cookies": [], "userAgent": "Browserless-UA"}})

    with pytest.raises(SkyscannerProviderError) as exc_info:
        bootstrap_session(env={"BROWSERLESS_TOKEN": "browserless-secret-token"}, client=client)

    error = exc_info.value
    assert error.failure_type == "browserless_cookie_unavailable"
    assert "browserless-secret-token" not in str(error)


def test_bootstrap_session_maps_transport_error_without_leaking_token() -> None:
    client = FakeBrowserlessClient(URLError("token browserless-secret-token leaked"))

    with pytest.raises(SkyscannerProviderError) as exc_info:
        bootstrap_session(env={"BROWSERLESS_TOKEN": "browserless-secret-token"}, client=client)

    error = exc_info.value
    assert error.failure_type == "browserless_bootstrap_failed"
    assert error.exception_type == "URLError"
    assert "browserless-secret-token" not in str(error)


def test_bootstrap_session_maps_forbidden_status_to_blocked() -> None:
    client = FakeBrowserlessClient({}, status_code=403)

    with pytest.raises(SkyscannerProviderError) as exc_info:
        bootstrap_session(env={"BROWSERLESS_TOKEN": "browserless-secret-token"}, client=client)

    error = exc_info.value
    assert error.failure_type == "blocked"
    assert error.http_status_code == 403


def test_bootstrap_session_maps_too_many_requests_status_to_rate_limited() -> None:
    client = FakeBrowserlessClient({}, status_code=429)

    with pytest.raises(SkyscannerProviderError) as exc_info:
        bootstrap_session(env={"BROWSERLESS_TOKEN": "browserless-secret-token"}, client=client)

    error = exc_info.value
    assert error.failure_type == "rate_limited"
    assert error.http_status_code == 429


def test_bootstrap_session_maps_navigation_forbidden_status_to_blocked() -> None:
    client = FakeBrowserlessClient(
        {
            "data": {
                "goto": {"status": 403},
                "cookies": [{"name": "traveller_context", "value": "abc"}],
                "userAgent": "Browserless-UA",
            }
        }
    )

    with pytest.raises(SkyscannerProviderError) as exc_info:
        bootstrap_session(env={"BROWSERLESS_TOKEN": "browserless-secret-token"}, client=client)

    error = exc_info.value
    assert error.failure_type == "blocked"
    assert error.http_status_code == 403


def test_bootstrap_session_maps_navigation_rate_limit_status_to_rate_limited() -> None:
    client = FakeBrowserlessClient(
        {
            "data": {
                "goto": {"status": 429},
                "cookies": [{"name": "traveller_context", "value": "abc"}],
                "userAgent": "Browserless-UA",
            }
        }
    )

    with pytest.raises(SkyscannerProviderError) as exc_info:
        bootstrap_session(env={"BROWSERLESS_TOKEN": "browserless-secret-token"}, client=client)

    error = exc_info.value
    assert error.failure_type == "rate_limited"
    assert error.http_status_code == 429


def test_urlopen_client_returns_http_error_status_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(*_args: object, **_kwargs: object) -> object:
        raise HTTPError(
            url="https://example.invalid/?token=browserless-secret-token",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=BytesIO(b'{"errors":[{"message":"blocked"}]}'),
        )

    monkeypatch.setattr(browserless, "urlopen", fake_urlopen)

    response = UrlopenBrowserlessClient().post_bql(
        endpoint="https://example.invalid/bql",
        token="browserless-secret-token",
        query="query { userAgent }",
        timeout_seconds=1.0,
    )

    assert response.status_code == 403
    assert response.payload == {"errors": [{"message": "blocked"}]}
