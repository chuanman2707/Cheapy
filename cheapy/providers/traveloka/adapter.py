"""HTTP adapter for the Traveloka research provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)


DEFAULT_BASE_URL = "https://www.traveloka.com/en-en/flight/fulltwosearch"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_CURRENCY = "USD"
DEFAULT_LOCALE = "en-en"
INITIAL_SEARCH_PATH = "/api/v2/flight/search/initial"
POLL_SEARCH_PATH = "/api/v2/flight/search/poll"
SUPPORTED_FARE_PATHS = {INITIAL_SEARCH_PATH, POLL_SEARCH_PATH}
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
USER_AGENT = "Cheapy/0.1 TravelokaResearchProvider"

ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
HTTPGet = Callable[[str, dict[str, str], float, int], "TravelokaHTTPResponse"]


@dataclass(frozen=True)
class TravelokaHTTPResponse:
    status_code: int
    body: bytes
    content_type: str
    final_url: str
    request_count: int = 1


@dataclass(frozen=True)
class TravelokaCaptureResult:
    payload: dict[str, object]
    source_path: str
    search_completed: bool
    timed_out: bool = False


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


class TravelokaAdapter:
    """Sync HTTP adapter around Traveloka public flight search surfaces."""

    configured_currency = DEFAULT_CURRENCY

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        http_get: HTTPGet | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be at least 1")
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._http_get = http_get if http_get is not None else _stdlib_http_get

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> dict[str, Any]:
        return self._search(request)

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> dict[str, Any]:
        return self._search(request)

    def _search(self, request: ProviderRequest) -> dict[str, Any]:
        url = build_search_url(request, base_url=self._base_url)
        headers = _headers()
        provider_error: TravelokaProviderError | None = None
        try:
            response = self._http_get(
                url,
                headers,
                self._timeout_seconds,
                self._max_response_bytes,
            )
        except TravelokaProviderError:
            raise
        except TimeoutError as exc:
            provider_error = _timeout_error(type(exc).__name__)
        except Exception as exc:
            provider_error = _transport_error(type(exc).__name__)

        if provider_error is not None:
            raise provider_error

        _raise_if_request_budget_exceeded(response.request_count)
        _raise_if_unsafe_final_url(response.final_url)
        _raise_for_status(response)
        _raise_if_too_large(response.body, self._max_response_bytes)
        _raise_if_blocked_body(response.body)
        return _parse_body(response)


def build_full_search_url(
    request: ProviderRequest,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    date_part = _traveloka_date(request.departure_date)
    if isinstance(request, ProviderExactRoundTripRequest):
        date_part = f"{date_part}.{_traveloka_date(request.return_date)}"
    params = {
        "ap": f"{request.origin}.{request.destination}",
        "dt": date_part,
        "ps": _passenger_spec(request),
        "sc": "ECONOMY",
        "funnelSource": "SEO-Homepage-SearchForm",
    }
    return f"{base_url}?{urlencode(params)}"


def build_search_url(request: ProviderRequest, *, base_url: str = DEFAULT_BASE_URL) -> str:
    """Legacy wrapper retained until the HTTP-only adapter is removed."""
    return build_full_search_url(request, base_url=base_url)


def _traveloka_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.day}-{parsed.month}-{parsed.year}"


def _passenger_spec(request: ProviderRequest) -> str:
    passengers = request.passengers
    return (
        f"{passengers.adults}."
        f"{passengers.children}."
        f"{passengers.infants_on_lap + passengers.infants_in_seat}"
    )


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": USER_AGENT,
    }


def _stdlib_http_get(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    max_bytes: int,
) -> TravelokaHTTPResponse:
    request = Request(url, headers=headers, method="GET")
    redirect_handler = _TravelokaRedirectHandler(max_requests=2)
    provider_error: TravelokaProviderError | None = None
    try:
        with _open_request(request, timeout_seconds, redirect_handler) as response:
            body = response.read(max_bytes + 1)
            return TravelokaHTTPResponse(
                status_code=response.status,
                body=body,
                content_type=response.headers.get("content-type", ""),
                final_url=response.url,
                request_count=redirect_handler.request_count,
            )
    except HTTPError as exc:
        try:
            body = exc.read(max_bytes + 1)
        finally:
            exc.close()
        return TravelokaHTTPResponse(
            status_code=exc.code,
            body=body,
            content_type=exc.headers.get("content-type", ""),
            final_url=exc.url,
            request_count=redirect_handler.request_count,
        )
    except TimeoutError as exc:
        provider_error = _timeout_error(type(exc).__name__)
    except URLError as exc:
        timeout_exception_type = _timeout_reason_exception_type(
            getattr(exc, "reason", None),
        )
        if timeout_exception_type is not None:
            provider_error = _timeout_error(timeout_exception_type)
        else:
            provider_error = _transport_error(type(exc).__name__)

    if provider_error is not None:
        raise provider_error
    raise RuntimeError("unreachable Traveloka HTTP adapter state")


def _open_request(
    request: Request,
    timeout_seconds: float,
    redirect_handler: "_TravelokaRedirectHandler",
):
    return build_opener(redirect_handler).open(request, timeout=timeout_seconds)


class _TravelokaRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, max_requests: int) -> None:
        super().__init__()
        self.max_requests = max_requests
        self.request_count = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.request_count >= self.max_requests:
            raise _request_budget_exceeded_error()
        _raise_if_unsafe_final_url(newurl)
        self.request_count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _timeout_error(exception_type: str) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="timeout",
        message_en="Traveloka request timed out.",
        error_code=ErrorCode.PROVIDER_TIMEOUT,
        retryable=True,
        exception_type=exception_type,
    )


def _transport_error(exception_type: str) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="transport_error",
        message_en="Traveloka transport failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def _timeout_reason_exception_type(reason: object) -> str | None:
    if isinstance(reason, TimeoutError):
        return type(reason).__name__
    if reason is None:
        return None
    reason_text = str(reason).lower()
    if "timeout" in reason_text or "timed out" in reason_text:
        return type(reason).__name__
    return None


def _raise_if_request_budget_exceeded(request_count: int) -> None:
    if request_count > 2:
        raise _request_budget_exceeded_error()


def _request_budget_exceeded_error() -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="request_budget_exceeded",
        message_en="Traveloka request budget was exceeded.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def _raise_if_unsafe_final_url(final_url: str) -> None:
    parsed = urlparse(final_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "www.traveloka.com"
        or _is_blocked_url_path(parsed.path)
    ):
        raise TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka redirected to an unsupported or blocked URL.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
        )


def _is_blocked_url_path(path: str) -> bool:
    lowered = path.lower()
    return any(
        marker in lowered
        for marker in (
            "captcha",
            "datadome",
            "challenge",
        )
    )


def _raise_for_status(response: TravelokaHTTPResponse) -> None:
    if response.status_code in {401, 403}:
        raise TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka blocked the request.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=response.status_code,
        )
    if response.status_code == 429:
        raise TravelokaProviderError(
            failure_type="rate_limited",
            message_en="Traveloka rate limited the request.",
            error_code=ErrorCode.PROVIDER_RATE_LIMITED,
            retryable=True,
            http_status_code=response.status_code,
        )
    if response.status_code == 408:
        raise TravelokaProviderError(
            failure_type="timeout",
            message_en="Traveloka request timed out.",
            error_code=ErrorCode.PROVIDER_TIMEOUT,
            retryable=True,
            http_status_code=response.status_code,
        )
    if response.status_code in {400, 404, 409, 422}:
        raise TravelokaProviderError(
            failure_type="bad_request",
            message_en="Traveloka rejected the request.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
            http_status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise TravelokaProviderError(
            failure_type="transport_error",
            message_en="Traveloka returned an HTTP error.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=response.status_code >= 500,
            http_status_code=response.status_code,
        )


def _raise_if_too_large(body: bytes, max_bytes: int) -> None:
    if len(body) > max_bytes:
        raise TravelokaProviderError(
            failure_type="response_too_large",
            message_en="Traveloka response exceeded the configured size limit.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )


def _raise_if_blocked_body(body: bytes) -> None:
    sample = body[:4096].decode("utf-8", errors="ignore").lower()
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
        raise TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka returned an access challenge.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
        )


def _parse_body(response: TravelokaHTTPResponse) -> dict[str, Any]:
    text = response.body.decode("utf-8", errors="replace")
    if "json" in response.content_type.lower() or text.lstrip().startswith(("{", "[")):
        parsed: object
        invalid_json = False
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            invalid_json = True
            parsed = None
        if invalid_json:
            raise _invalid_json_error()
        if not isinstance(parsed, dict) or not _is_supported_api_payload(parsed):
            raise _unsupported_response_error()
        return parsed
    raise _unsupported_response_error()


def _invalid_json_error() -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="invalid_json",
        message_en="Traveloka returned invalid JSON.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def _unsupported_response_error() -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="unsupported_response",
        message_en="Traveloka returned an unsupported response.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def _is_supported_api_payload(payload: dict[str, Any]) -> bool:
    for path in (
        ("data", "itineraries"),
        ("data", "flightSearchResult", "itineraries"),
        ("itineraries",),
        ("flightSearchResult", "itineraries"),
    ):
        value: object = payload
        for key in path:
            if not isinstance(value, dict):
                break
            value = value.get(key)
        else:
            if isinstance(value, list):
                return True
    return False
