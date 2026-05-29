"""Safe browser bootstrap errors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserBootstrapErrorContext:
    failure_type: str
    phase: str
    http_status_code: int | None = None
    exception_type: str | None = None


class BrowserBootstrapError(Exception):
    """Base class for browser bootstrap failures with safe context."""

    def __init__(
        self,
        *,
        message_en: str,
        context: BrowserBootstrapErrorContext,
    ) -> None:
        super().__init__(message_en)
        self.message_en = message_en
        self.context = context


class BrowserBootstrapUnavailable(BrowserBootstrapError):
    """Browser bootstrap runtime or lifecycle setup is unavailable."""


class BrowserBootstrapTimeout(BrowserBootstrapError):
    """Browser bootstrap exceeded its local deadline."""


class BrowserBootstrapBlocked(BrowserBootstrapError):
    """Browser bootstrap hit a blocked or rate-limited navigation."""


class BrowserBootstrapCookieUnavailable(BrowserBootstrapError):
    """Browser bootstrap did not produce a usable cookie header."""


class BrowserNetworkCaptureUnavailable(BrowserBootstrapError):
    """Browser network capture did not produce a usable exchange."""


def unavailable_error(
    *,
    phase: str,
    exception_type: str | None = None,
    http_status_code: int | None = None,
) -> BrowserBootstrapUnavailable:
    return BrowserBootstrapUnavailable(
        message_en="Browser bootstrap runtime is unavailable.",
        context=BrowserBootstrapErrorContext(
            failure_type="unavailable",
            phase=phase,
            http_status_code=http_status_code,
            exception_type=exception_type,
        ),
    )


def timeout_error(
    *,
    phase: str,
    exception_type: str | None = None,
) -> BrowserBootstrapTimeout:
    return BrowserBootstrapTimeout(
        message_en="Browser bootstrap timed out.",
        context=BrowserBootstrapErrorContext(
            failure_type="timeout",
            phase=phase,
            exception_type=exception_type,
        ),
    )


def blocked_error(
    *,
    phase: str,
    http_status_code: int | None = None,
    exception_type: str | None = None,
) -> BrowserBootstrapBlocked:
    return BrowserBootstrapBlocked(
        message_en="Browser bootstrap navigation was blocked.",
        context=BrowserBootstrapErrorContext(
            failure_type="blocked",
            phase=phase,
            http_status_code=http_status_code,
            exception_type=exception_type,
        ),
    )


def cookie_unavailable_error(
    *,
    phase: str,
    exception_type: str | None = None,
) -> BrowserBootstrapCookieUnavailable:
    return BrowserBootstrapCookieUnavailable(
        message_en="Browser bootstrap did not produce usable cookies.",
        context=BrowserBootstrapErrorContext(
            failure_type="cookie_unavailable",
            phase=phase,
            exception_type=exception_type,
        ),
    )


def capture_unavailable_error(
    *,
    phase: str,
    exception_type: str | None = None,
) -> BrowserNetworkCaptureUnavailable:
    return BrowserNetworkCaptureUnavailable(
        message_en="Browser network capture did not find a matching request.",
        context=BrowserBootstrapErrorContext(
            failure_type="capture_unavailable",
            phase=phase,
            exception_type=exception_type,
        ),
    )
