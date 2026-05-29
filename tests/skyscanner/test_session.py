from __future__ import annotations

import json

import pytest

from cheapy.browser_bootstrap import (
    BrowserBootstrapBlocked,
    BrowserBootstrapCookieUnavailable,
    BrowserBootstrapErrorContext,
    BrowserBootstrapSession,
    BrowserBootstrapTimeout,
)
from cheapy.models import ErrorCode
from cheapy.providers.skyscanner.session import (
    SkyscannerSessionError,
    SkyscannerSessionManager,
)


def assert_no_sensitive_tokens(value: object) -> None:
    text = json.dumps(value, sort_keys=True, default=str).lower()
    for token in (
        "secret-cookie",
        "__secure-anon_token",
        "raw_payload",
        "raw payload",
        "header",
        "sessionid",
        "session=",
    ):
        assert token not in text


def test_env_cookie_override_returns_env_source_and_does_not_bootstrap() -> None:
    calls: list[dict[str, object]] = []

    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        calls.append(kwargs)
        raise AssertionError("bootstrap should not run for env cookie")

    manager = SkyscannerSessionManager(
        bootstrap_cookies=fake_bootstrap,
        monotonic=lambda: 100.0,
    )

    config, source = manager.config_for_call(
        {
            "CHEAPY_SKYSCANNER_COOKIE": "secret-cookie",
            "CHEAPY_SKYSCANNER_USER_AGENT": "EnvUA",
        },
        timeout_seconds=3.0,
        deadline_monotonic=120.0,
    )

    assert source == "env"
    assert calls == []
    assert config.cookie == "secret-cookie"
    assert config.user_agent == "EnvUA"
    assert config.timeout_seconds == 3.0
    assert config.deadline_monotonic == 120.0
    assert_no_sensitive_tokens(repr(config))


def test_empty_env_cookie_uses_bootstrap_path() -> None:
    calls: list[dict[str, object]] = []

    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        calls.append(kwargs)
        return BrowserBootstrapSession(
            cookie_header="boot-cookie=secret-cookie",
            user_agent="BootUA",
            created_monotonic=100.0,
        )

    manager = SkyscannerSessionManager(
        bootstrap_cookies=fake_bootstrap,
        monotonic=lambda: 100.0,
    )

    config, source = manager.config_for_call(
        {"CHEAPY_SKYSCANNER_COOKIE": "   "},
        timeout_seconds=3.0,
        deadline_monotonic=120.0,
    )

    assert source == "bootstrap"
    assert config.cookie == "boot-cookie=secret-cookie"
    assert config.user_agent == "BootUA"
    assert len(calls) == 1


def test_bootstrap_then_cache_reuse_passes_deadline_and_user_agent() -> None:
    now = 10.0
    calls: list[dict[str, object]] = []

    def fake_monotonic() -> float:
        return now

    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        calls.append(kwargs)
        return BrowserBootstrapSession(
            cookie_header="boot-cookie=secret-cookie",
            user_agent="BootUA",
            created_monotonic=now,
        )

    manager = SkyscannerSessionManager(
        bootstrap_cookies=fake_bootstrap,
        monotonic=fake_monotonic,
        ttl_seconds=300.0,
        base_url="https://example.test/",
    )

    first_config, first_source = manager.config_for_call(
        {"CHEAPY_SKYSCANNER_USER_AGENT": "EnvUA"},
        timeout_seconds=5.0,
        deadline_monotonic=20.0,
    )
    now = 20.0
    second_config, second_source = manager.config_for_call(
        {},
        timeout_seconds=6.0,
        deadline_monotonic=30.0,
    )

    assert first_source == "bootstrap"
    assert second_source == "cache"
    assert first_config.cookie == "boot-cookie=secret-cookie"
    assert second_config.cookie == "boot-cookie=secret-cookie"
    assert second_config.user_agent == "BootUA"
    assert second_config.timeout_seconds == 6.0
    assert second_config.deadline_monotonic == 30.0
    assert calls == [
        {
            "page_url": "https://example.test/transport/flights/",
            "deadline_monotonic": 20.0,
            "user_agent": "EnvUA",
        }
    ]


def test_force_refresh_bypasses_valid_cache_and_bootstraps() -> None:
    now = 10.0
    bootstrap_count = 0

    def fake_monotonic() -> float:
        return now

    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        nonlocal bootstrap_count
        bootstrap_count += 1
        return BrowserBootstrapSession(
            cookie_header=f"cookie-{bootstrap_count}=secret-cookie",
            user_agent=f"BootUA/{bootstrap_count}",
            created_monotonic=now,
        )

    manager = SkyscannerSessionManager(
        bootstrap_cookies=fake_bootstrap,
        monotonic=fake_monotonic,
        ttl_seconds=300.0,
    )

    first_config, first_source = manager.config_for_call(
        {},
        timeout_seconds=1.0,
        deadline_monotonic=20.0,
    )
    second_config, second_source = manager.config_for_call(
        {},
        timeout_seconds=1.0,
        deadline_monotonic=21.0,
        force_refresh=True,
    )

    assert first_source == "bootstrap"
    assert second_source == "bootstrap"
    assert first_config.cookie == "cookie-1=secret-cookie"
    assert second_config.cookie == "cookie-2=secret-cookie"
    assert bootstrap_count == 2


def test_ttl_expiry_refreshes_bootstrap_session() -> None:
    now = 0.0
    bootstrap_count = 0

    def fake_monotonic() -> float:
        return now

    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        nonlocal bootstrap_count
        bootstrap_count += 1
        return BrowserBootstrapSession(
            cookie_header=f"cookie-{bootstrap_count}=secret-cookie",
            user_agent=f"BootUA/{bootstrap_count}",
            created_monotonic=now,
        )

    manager = SkyscannerSessionManager(
        bootstrap_cookies=fake_bootstrap,
        monotonic=fake_monotonic,
        ttl_seconds=10.0,
    )

    first_config, first_source = manager.config_for_call(
        {},
        timeout_seconds=1.0,
        deadline_monotonic=5.0,
    )
    now = 5.0
    second_config, second_source = manager.config_for_call(
        {},
        timeout_seconds=1.0,
        deadline_monotonic=6.0,
    )
    now = 10.1
    third_config, third_source = manager.config_for_call(
        {},
        timeout_seconds=1.0,
        deadline_monotonic=12.0,
    )

    assert (first_source, second_source, third_source) == (
        "bootstrap",
        "cache",
        "bootstrap",
    )
    assert first_config.cookie == "cookie-1=secret-cookie"
    assert second_config.cookie == "cookie-1=secret-cookie"
    assert third_config.cookie == "cookie-2=secret-cookie"
    assert bootstrap_count == 2


def test_bootstrap_timeout_maps_to_safe_retryable_session_error() -> None:
    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        raise BrowserBootstrapTimeout(
            message_en="timed out with secret-cookie raw_payload",
            context=BrowserBootstrapErrorContext(
                failure_type="timeout",
                phase="navigate",
                exception_type="TimeoutError",
            ),
        )

    manager = SkyscannerSessionManager(bootstrap_cookies=fake_bootstrap)

    with pytest.raises(SkyscannerSessionError) as exc_info:
        manager.config_for_call({}, timeout_seconds=1.0, deadline_monotonic=2.0)

    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.retryable is True
    assert exc_info.value.exception_type == "TimeoutError"
    assert_no_sensitive_tokens(exc_info.value.__dict__)
    assert_no_sensitive_tokens(str(exc_info.value))


@pytest.mark.parametrize(
    ("status_code", "failure_type", "error_code", "retryable"),
    [
        (403, "blocked", ErrorCode.PROVIDER_BLOCKED, False),
        (429, "rate_limited", ErrorCode.PROVIDER_RATE_LIMITED, True),
    ],
)
def test_bootstrap_blocked_status_maps_to_provider_error_code(
    status_code: int,
    failure_type: str,
    error_code: ErrorCode,
    retryable: bool,
) -> None:
    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        raise BrowserBootstrapBlocked(
            message_en="blocked with secret-cookie header",
            context=BrowserBootstrapErrorContext(
                failure_type=failure_type,
                phase="navigate",
                http_status_code=status_code,
                exception_type="HTTPStatusError",
            ),
        )

    manager = SkyscannerSessionManager(bootstrap_cookies=fake_bootstrap)

    with pytest.raises(SkyscannerSessionError) as exc_info:
        manager.config_for_call({}, timeout_seconds=1.0, deadline_monotonic=2.0)

    assert exc_info.value.error_code == error_code
    assert exc_info.value.failure_type == failure_type
    assert exc_info.value.retryable is retryable
    assert exc_info.value.http_status_code == status_code
    assert exc_info.value.exception_type == "HTTPStatusError"
    assert_no_sensitive_tokens(exc_info.value.__dict__)


def test_browser_cookie_unavailable_maps_to_retryable_session_error() -> None:
    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        raise BrowserBootstrapCookieUnavailable(
            message_en="missing secret-cookie session",
            context=BrowserBootstrapErrorContext(
                failure_type="browser_cookie_unavailable",
                phase="cookie_read",
                exception_type="CookieError",
            ),
        )

    manager = SkyscannerSessionManager(bootstrap_cookies=fake_bootstrap)

    with pytest.raises(SkyscannerSessionError) as exc_info:
        manager.config_for_call({}, timeout_seconds=1.0, deadline_monotonic=2.0)

    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.failure_type == "browser_cookie_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.exception_type == "CookieError"
    assert_no_sensitive_tokens(str(exc_info.value))


def test_empty_browser_cookie_maps_to_retryable_session_error() -> None:
    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        return BrowserBootstrapSession(
            cookie_header="   ",
            user_agent="SecretUA",
            created_monotonic=1.0,
        )

    manager = SkyscannerSessionManager(bootstrap_cookies=fake_bootstrap)

    with pytest.raises(SkyscannerSessionError) as exc_info:
        manager.config_for_call({}, timeout_seconds=1.0, deadline_monotonic=2.0)

    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.failure_type == "browser_cookie_unavailable"
    assert exc_info.value.retryable is True
    assert_no_sensitive_tokens(exc_info.value.__dict__)
