"""Provider-local errors for the Traveloka research provider."""

from __future__ import annotations

from cheapy.models import ErrorCode


class TravelokaProviderError(Exception):
    """Structured provider-local error safe to map into Contract V1."""

    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        error_code: ErrorCode,
        retryable: bool,
        http_status_code: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
        self.error_code = error_code
        self.retryable = retryable
        self.http_status_code = http_status_code
        self.exception_type = exception_type


def is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    type_name = type(exc).__name__.lower()
    module_name = type(exc).__module__.lower()
    return "timeout" in type_name or (
        "playwright" in module_name and "timeout" in type_name
    )


def timeout_error(exception_type: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="timeout",
        message_en="Traveloka request timed out.",
        error_code=ErrorCode.PROVIDER_TIMEOUT,
        retryable=True,
        exception_type=exception_type,
    )


def browser_unavailable_error(
    exception_type: str | None = None,
) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="browser_unavailable",
        message_en="Traveloka browser runtime is unavailable.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def navigation_failed_error(
    exception_type: str | None = None,
) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="navigation_failed",
        message_en="Traveloka browser navigation failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def blocked_error(http_status_code: int | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="blocked",
        message_en="Traveloka returned an access challenge.",
        error_code=ErrorCode.PROVIDER_BLOCKED,
        retryable=False,
        http_status_code=http_status_code,
    )


def rate_limited_error(http_status_code: int | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="rate_limited",
        message_en="Traveloka rate limited the request.",
        error_code=ErrorCode.PROVIDER_RATE_LIMITED,
        retryable=True,
        http_status_code=http_status_code,
    )


def transport_error(http_status_code: int | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="transport_error",
        message_en="Traveloka transport failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=http_status_code is None or http_status_code >= 500,
        http_status_code=http_status_code,
    )


def invalid_json_error(exception_type: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="invalid_json",
        message_en="Traveloka returned invalid JSON.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
        exception_type=exception_type,
    )


def unsupported_response_error() -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="unsupported_response",
        message_en="Traveloka returned an unsupported response.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def raise_blocked_if_terminal_page(content: str) -> None:
    sample = content[:4096].lower()
    blocked_markers = (
        "captcha required",
        "captcha challenge",
        "captcha-delivery",
        "complete the captcha",
        "solve captcha",
        "automated bot traffic detected",
        "bot challenge",
        "robot check",
        "verify you are not a bot",
        "access challenge",
        "access denied",
        "please enable js and disable any ad blocker",
        "unusual traffic",
    )
    if any(marker in sample for marker in blocked_markers):
        raise blocked_error()
