from __future__ import annotations

import pytest

from cheapy.browser_bootstrap import BrowserBootstrapCookieUnavailable
from cheapy.browser_bootstrap.cookies import cookie_header_from_browser_cookies


def test_cookie_header_serializes_usable_cookies_in_order() -> None:
    cookies = [
        {"name": "session", "value": "secret-cookie"},
        {"name": "locale", "value": "en-US"},
        {"name": "", "value": "skip-empty-name"},
        {"name": "empty", "value": ""},
    ]

    assert cookie_header_from_browser_cookies(cookies) == (
        "session=secret-cookie; locale=en-US"
    )


def test_cookie_header_filters_by_normalized_domain_suffix() -> None:
    cookies = [
        {"name": "root", "value": "1", "domain": ".example.com"},
        {"name": "sub", "value": "2", "domain": "www.example.com"},
        {"name": "other", "value": "3", "domain": "not-example.com"},
        {"name": "missing", "value": "4"},
    ]

    assert cookie_header_from_browser_cookies(
        cookies,
        domain_suffix=".example.com",
    ) == "root=1; sub=2"


def test_cookie_header_raises_safe_error_when_no_cookies_are_usable() -> None:
    with pytest.raises(BrowserBootstrapCookieUnavailable) as exc_info:
        cookie_header_from_browser_cookies(
            [
                {"name": "", "value": "secret-cookie"},
                {"name": "session", "value": "", "domain": ".example.com"},
                {"name": "other", "value": "secret-cookie", "domain": "other.com"},
            ],
            domain_suffix="example.com",
        )

    error = exc_info.value
    assert error.context.failure_type == "browser_cookie_unavailable"
    assert error.context.phase == "cookie_read"
    assert "secret-cookie" not in str(error)
    assert "example.com" not in str(error)
