"""Skyscanner browser bootstrap session management."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import time

from cheapy import browser_bootstrap
from cheapy.browser_bootstrap import (
    BrowserBootstrapBlocked,
    BrowserBootstrapCookieUnavailable,
    BrowserBootstrapError,
    BrowserBootstrapSession,
    BrowserBootstrapTimeout,
)
from cheapy.models import ErrorCode
from cheapy.providers.skyscanner.adapter import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    SkyscannerConfig,
    SkyscannerProviderError,
    config_from_bootstrap_session,
    config_from_env,
)


class SkyscannerSessionError(SkyscannerProviderError):
    """Sanitized session/bootstrap error safe for provider-level mapping."""


BootstrapCookies = Callable[..., BrowserBootstrapSession]
Monotonic = Callable[[], float]


class SkyscannerSessionManager:
    def __init__(
        self,
        *,
        bootstrap_cookies: BootstrapCookies = browser_bootstrap.bootstrap_cookies,
        monotonic: Monotonic = time.monotonic,
        ttl_seconds: float = 300.0,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._bootstrap_cookies = bootstrap_cookies
        self._monotonic = monotonic
        self._ttl_seconds = ttl_seconds
        self._base_url = base_url.rstrip("/")
        self._cached_session: BrowserBootstrapSession | None = None
        self._cache_expires_monotonic = 0.0

    def config_for_call(
        self,
        env: Mapping[str, str],
        *,
        timeout_seconds: float,
        deadline_monotonic: float,
        force_refresh: bool = False,
    ) -> tuple[SkyscannerConfig, str]:
        if "CHEAPY_SKYSCANNER_COOKIE" in env:
            return (
                config_from_env(
                    env,
                    base_url=self._base_url,
                    timeout_seconds=timeout_seconds,
                    deadline_monotonic=deadline_monotonic,
                ),
                "env",
            )

        now = self._monotonic()
        if (
            not force_refresh
            and self._cached_session is not None
            and now < self._cache_expires_monotonic
        ):
            return (
                self._config_from_session(
                    self._cached_session,
                    timeout_seconds=timeout_seconds,
                    deadline_monotonic=deadline_monotonic,
                ),
                "cache",
            )

        session = self._bootstrap_session(env, deadline_monotonic=deadline_monotonic)
        config = self._config_from_session(
            session,
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        )
        self._cached_session = session
        self._cache_expires_monotonic = self._monotonic() + self._ttl_seconds
        return config, "bootstrap"

    def _bootstrap_session(
        self,
        env: Mapping[str, str],
        *,
        deadline_monotonic: float,
    ) -> BrowserBootstrapSession:
        try:
            return self._bootstrap_cookies(
                page_url=f"{self._base_url}/transport/flights/",
                deadline_monotonic=deadline_monotonic,
                user_agent=env.get("CHEAPY_SKYSCANNER_USER_AGENT") or None,
            )
        except BrowserBootstrapError as exc:
            raise _session_error_from_bootstrap_error(exc) from None

    def _config_from_session(
        self,
        session: BrowserBootstrapSession,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        deadline_monotonic: float | None = None,
    ) -> SkyscannerConfig:
        try:
            return config_from_bootstrap_session(
                session,
                base_url=self._base_url,
                timeout_seconds=timeout_seconds,
                deadline_monotonic=deadline_monotonic,
            )
        except SkyscannerProviderError as exc:
            if exc.failure_type != "browser_cookie_unavailable":
                raise
            raise SkyscannerSessionError(
                failure_type=exc.failure_type,
                message_en=exc.message_en,
                error_code=exc.error_code,
                retryable=exc.retryable,
                http_status_code=exc.http_status_code,
                exception_type=exc.exception_type,
            ) from None


def _session_error_from_bootstrap_error(
    exc: BrowserBootstrapError,
) -> SkyscannerSessionError:
    context = exc.context
    failure_type = context.failure_type or "browser_bootstrap_failed"
    http_status_code = context.http_status_code
    exception_type = context.exception_type

    if isinstance(exc, BrowserBootstrapTimeout):
        return SkyscannerSessionError(
            failure_type=failure_type or "timeout",
            message_en="Skyscanner browser bootstrap timed out.",
            error_code=ErrorCode.PROVIDER_TIMEOUT,
            retryable=True,
            http_status_code=http_status_code,
            exception_type=exception_type,
        )

    if isinstance(exc, BrowserBootstrapBlocked):
        rate_limited = failure_type == "rate_limited"
        return SkyscannerSessionError(
            failure_type=failure_type or "blocked",
            message_en="Skyscanner browser bootstrap was blocked.",
            error_code=(
                ErrorCode.PROVIDER_RATE_LIMITED
                if rate_limited
                else ErrorCode.PROVIDER_BLOCKED
            ),
            retryable=rate_limited,
            http_status_code=http_status_code,
            exception_type=exception_type,
        )

    if isinstance(exc, BrowserBootstrapCookieUnavailable):
        return SkyscannerSessionError(
            failure_type="browser_cookie_unavailable",
            message_en="Skyscanner browser session did not return usable cookies.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            http_status_code=http_status_code,
            exception_type=exception_type,
        )

    return SkyscannerSessionError(
        failure_type=failure_type or "browser_bootstrap_failed",
        message_en="Skyscanner browser bootstrap failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        http_status_code=http_status_code,
        exception_type=exception_type,
    )
