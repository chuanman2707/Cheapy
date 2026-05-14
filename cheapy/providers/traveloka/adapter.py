"""HTTP adapter for the Traveloka research provider."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)


DEFAULT_BASE_URL = "https://www.traveloka.com/en-en/flight"
DEFAULT_LOCALE = "en-en"
DEFAULT_CURRENCY = "USD"
DEFAULT_TIMEOUT_SECONDS = 20.0
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
            raise _timeout_error(type(exc).__name__) from None
        except Exception as exc:
            raise TravelokaProviderError(
                failure_type="transport_error",
                message_en="Traveloka transport failed.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=True,
                exception_type=type(exc).__name__,
            ) from None

        _raise_for_status(response)
        _raise_if_too_large(response.body, self._max_response_bytes)
        _raise_if_blocked_body(response.body)
        return _parse_body(response)


def build_search_url(request: ProviderRequest, *, base_url: str = DEFAULT_BASE_URL) -> str:
    trip = "roundtrip" if isinstance(request, ProviderExactRoundTripRequest) else "oneway"
    params = {
        "trip": trip,
        "origin": request.origin,
        "destination": request.destination,
        "departureDate": request.departure_date,
        "currency": DEFAULT_CURRENCY,
        "locale": DEFAULT_LOCALE,
        "cabin": "ECONOMY",
        "adults": str(request.passengers.adults),
        "children": str(request.passengers.children),
        "infantsInSeat": str(request.passengers.infants_in_seat),
        "infantsOnLap": str(request.passengers.infants_on_lap),
    }
    if isinstance(request, ProviderExactRoundTripRequest):
        params["returnDate"] = request.return_date
    return f"{base_url}?{urlencode(params)}"


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
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(max_bytes + 1)
            return TravelokaHTTPResponse(
                status_code=response.status,
                body=body,
                content_type=response.headers.get("content-type", ""),
                final_url=response.url,
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
        )
    except TimeoutError as exc:
        raise _timeout_error(type(exc).__name__) from None
    except URLError as exc:
        timeout_exception_type = _timeout_reason_exception_type(
            getattr(exc, "reason", None),
        )
        if timeout_exception_type is not None:
            raise _timeout_error(timeout_exception_type) from None
        raise TravelokaProviderError(
            failure_type="transport_error",
            message_en="Traveloka transport failed.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=type(exc).__name__,
        ) from None


def _timeout_error(exception_type: str) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="timeout",
        message_en="Traveloka request timed out.",
        error_code=ErrorCode.PROVIDER_TIMEOUT,
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
    if response.status_code >= 400:
        raise TravelokaProviderError(
            failure_type="transport_error",
            message_en="Traveloka returned an HTTP error.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
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
        "complete the captcha",
        "solve captcha",
        "automated bot traffic detected",
        "bot challenge",
        "robot check",
        "verify you are not a bot",
        "access challenge",
        "access denied",
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
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_html": text, "_content_type": response.content_type}
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
    return {"_html": text, "_content_type": response.content_type}
