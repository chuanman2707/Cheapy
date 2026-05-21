"""Safe provider-local errors for Skyscanner."""

from __future__ import annotations

from cheapy.models import ErrorCode


class SkyscannerProviderError(Exception):
    """Structured provider-local error safe to expose through Contract V1."""

    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        error_code: ErrorCode,
        retryable: bool,
        http_status_code: int | None = None,
        exception_type: str | None = None,
        search_attempts: int | None = None,
        cookie_refresh_count: int | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
        self.error_code = error_code
        self.retryable = retryable
        self.http_status_code = http_status_code
        self.exception_type = exception_type
        self.search_attempts = search_attempts
        self.cookie_refresh_count = cookie_refresh_count


def browserless_bootstrap_failed(
    *,
    exception_type: str | None = None,
) -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="browserless_bootstrap_failed",
        message_en="Skyscanner Browserless bootstrap failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def browserless_cookie_unavailable() -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="browserless_cookie_unavailable",
        message_en="Skyscanner Browserless bootstrap did not return cookies.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
    )


def blocked_error(http_status_code: int | None = None) -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="blocked",
        message_en="Skyscanner returned an access challenge.",
        error_code=ErrorCode.PROVIDER_BLOCKED,
        retryable=False,
        http_status_code=http_status_code,
    )


def rate_limited_error(http_status_code: int | None = None) -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="rate_limited",
        message_en="Skyscanner rate limited the request.",
        error_code=ErrorCode.PROVIDER_RATE_LIMITED,
        retryable=True,
        http_status_code=http_status_code,
    )


def entity_not_found_error() -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="entity_not_found",
        message_en="Skyscanner did not return an entity for the requested airport.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def entity_ambiguous_error() -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="entity_ambiguous",
        message_en="Skyscanner returned multiple matching airport entities.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def search_incomplete_error() -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="search_incomplete",
        message_en="Skyscanner search did not complete.",
        error_code=ErrorCode.PROVIDER_TIMEOUT,
        retryable=True,
    )


def no_usable_results_error(
    *,
    search_attempts: int,
    cookie_refresh_count: int,
) -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="no_usable_results",
        message_en="Skyscanner did not return usable fare results.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        search_attempts=search_attempts,
        cookie_refresh_count=cookie_refresh_count,
    )


def unsupported_passengers_error() -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="unsupported_passengers",
        message_en="Skyscanner provider currently supports adult passengers only.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )
