from __future__ import annotations

from cheapy.models import ErrorCode
from cheapy.providers.skyscanner.errors import (
    SkyscannerProviderError,
    blocked_error,
    browserless_bootstrap_failed,
    browserless_cookie_unavailable,
    entity_ambiguous_error,
    entity_not_found_error,
    no_usable_results_error,
    rate_limited_error,
    search_incomplete_error,
    unsupported_passengers_error,
)


def test_error_helpers_are_structured_and_safe() -> None:
    errors = [
        browserless_bootstrap_failed(exception_type="secret-token"),
        browserless_cookie_unavailable(),
        blocked_error(http_status_code=403),
        rate_limited_error(http_status_code=429),
        entity_not_found_error(),
        entity_ambiguous_error(),
        search_incomplete_error(),
        no_usable_results_error(search_attempts=97, cookie_refresh_count=31),
        unsupported_passengers_error(),
    ]

    assert all(isinstance(error, SkyscannerProviderError) for error in errors)
    assert [error.failure_type for error in errors] == [
        "browserless_bootstrap_failed",
        "browserless_cookie_unavailable",
        "blocked",
        "rate_limited",
        "entity_not_found",
        "entity_ambiguous",
        "search_incomplete",
        "no_usable_results",
        "unsupported_passengers",
    ]
    assert [error.error_code for error in errors] == [
        ErrorCode.PROVIDER_FAILED,
        ErrorCode.PROVIDER_FAILED,
        ErrorCode.PROVIDER_BLOCKED,
        ErrorCode.PROVIDER_RATE_LIMITED,
        ErrorCode.PROVIDER_FAILED,
        ErrorCode.PROVIDER_FAILED,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.PROVIDER_FAILED,
        ErrorCode.PROVIDER_FAILED,
    ]
    assert [error.retryable for error in errors] == [
        True,
        True,
        False,
        True,
        False,
        False,
        True,
        True,
        False,
    ]
    assert [error.message_en for error in errors] == [
        "Skyscanner Browserless bootstrap failed.",
        "Skyscanner Browserless bootstrap did not return cookies.",
        "Skyscanner returned an access challenge.",
        "Skyscanner rate limited the request.",
        "Skyscanner did not return an entity for the requested airport.",
        "Skyscanner returned multiple matching airport entities.",
        "Skyscanner search did not complete.",
        "Skyscanner did not return usable fare results.",
        "Skyscanner provider currently supports adult passengers only.",
    ]
    assert errors[0].exception_type == "secret-token"
    assert errors[1].exception_type is None
    assert errors[2].http_status_code == 403
    assert errors[3].http_status_code == 429
    assert errors[4].http_status_code is None
    assert errors[4].exception_type is None
    assert errors[5].http_status_code is None
    assert errors[5].exception_type is None
    assert errors[6].http_status_code is None
    assert errors[6].exception_type is None
    assert errors[7].search_attempts == 97
    assert errors[7].cookie_refresh_count == 31
    assert errors[8].search_attempts is None
    assert errors[8].cookie_refresh_count is None

    messages = "".join(error.message_en for error in errors)
    assert "URLError" not in messages
    assert "403" not in messages
    assert "429" not in messages
    assert "97" not in messages
    assert "31" not in messages
    assert "secret-token" not in messages
