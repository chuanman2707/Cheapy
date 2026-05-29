"""Shared browser bootstrap primitives for Cheapy providers."""

from cheapy.browser_bootstrap.cloak import (
    bootstrap_cookies,
    capture_first_party_requests,
    launch_browser,
)
from cheapy.browser_bootstrap.cookies import cookie_header_from_browser_cookies
from cheapy.browser_bootstrap.errors import (
    BrowserBootstrapBlocked,
    BrowserBootstrapCookieUnavailable,
    BrowserBootstrapError,
    BrowserBootstrapErrorContext,
    BrowserBootstrapTimeout,
    BrowserBootstrapUnavailable,
    BrowserNetworkCaptureUnavailable,
    blocked_error,
    capture_unavailable_error,
    cookie_unavailable_error,
    timeout_error,
    unavailable_error,
)
from cheapy.browser_bootstrap.types import (
    BrowserBootstrapSession,
    BrowserLauncher,
    BrowserNetworkCapture,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
    RequestPredicate,
    ResponsePredicate,
)

__all__ = [
    "BrowserBootstrapBlocked",
    "BrowserBootstrapCookieUnavailable",
    "BrowserBootstrapError",
    "BrowserBootstrapErrorContext",
    "BrowserBootstrapSession",
    "BrowserBootstrapTimeout",
    "BrowserBootstrapUnavailable",
    "BrowserLauncher",
    "BrowserNetworkCapture",
    "BrowserNetworkCaptureUnavailable",
    "CapturedExchange",
    "CapturedRequest",
    "CapturedResponse",
    "RequestPredicate",
    "ResponsePredicate",
    "blocked_error",
    "bootstrap_cookies",
    "capture_first_party_requests",
    "capture_unavailable_error",
    "cookie_header_from_browser_cookies",
    "cookie_unavailable_error",
    "launch_browser",
    "timeout_error",
    "unavailable_error",
]
