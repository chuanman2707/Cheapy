"""Browser capture adapter for the Traveloka research provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from time import monotonic
from typing import Callable
from urllib.parse import urlencode, urlparse

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

ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
BrowserLauncher = Callable[..., object]


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
    """Sync browser adapter around Traveloka flight search capture surfaces."""

    configured_currency = DEFAULT_CURRENCY

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = 0.25,
        launch_browser: BrowserLauncher | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0")
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._launch_browser = (
            launch_browser if launch_browser is not None else _default_launch_browser
        )

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> TravelokaCaptureResult:
        return self._search(request)

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult:
        return self._search(request)

    def _search(self, request: ProviderRequest) -> TravelokaCaptureResult:
        browser: object | None = None
        context: object | None = None
        state = _CaptureState()
        deadline = monotonic() + self._timeout_seconds
        try:
            try:
                browser = self._launch_browser(
                    headless=True,
                    timeout=_remaining_timeout_ms(deadline),
                )
            except Exception as exc:
                if _is_timeout_exception(exc):
                    raise _timeout_error(type(exc).__name__) from None
                raise _browser_unavailable_error(type(exc).__name__) from None

            _remaining_timeout_ms(deadline)
            context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
            _remaining_timeout_ms(deadline)
            page = context.new_page()  # type: ignore[attr-defined]
            _remaining_timeout_ms(deadline)
            page.on("response", state.handle_response)  # type: ignore[attr-defined]
            _remaining_timeout_ms(deadline)
            page.goto(  # type: ignore[attr-defined]
                build_full_search_url(request, base_url=self._base_url),
                wait_until="domcontentloaded",
                timeout=_remaining_timeout_ms(deadline),
            )

            while not state.completed and monotonic() < deadline:
                remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
                if remaining_ms <= 0:
                    break
                wait_ms = min(
                    round(self._poll_interval_seconds * 1000),
                    remaining_ms,
                )
                page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]

            if state.best_result is not None:
                if state.completed:
                    return state.best_result
                return TravelokaCaptureResult(
                    payload=state.best_result.payload,
                    source_path=state.best_result.source_path,
                    search_completed=state.best_result.search_completed,
                    timed_out=True,
                )

            _raise_blocked_if_terminal_page(page.content())  # type: ignore[attr-defined]
            raise _timeout_error()
        except TravelokaProviderError:
            raise
        except Exception as exc:
            if _is_timeout_exception(exc):
                raise _timeout_error(type(exc).__name__) from None
            raise _navigation_failed_error(type(exc).__name__) from None
        finally:
            _close_quietly(context)
            _close_quietly(browser)


class _CaptureState:
    def __init__(self) -> None:
        self.best_result: TravelokaCaptureResult | None = None
        self.completed = False

    def handle_response(self, response: object) -> None:
        path = urlparse(str(getattr(response, "url", ""))).path
        if path not in SUPPORTED_FARE_PATHS:
            return

        status = int(getattr(response, "status", 0))
        if status in {401, 403}:
            raise _blocked_error(status)
        if status == 429:
            raise _rate_limited_error(status)
        if status >= 500:
            raise _transport_error(status)

        payload: object
        try:
            payload = response.json()  # type: ignore[attr-defined]
        except Exception as exc:
            raise _invalid_json_error(type(exc).__name__) from None

        if not isinstance(payload, dict) or not _is_supported_fare_payload(payload):
            raise _unsupported_response_error()

        search_completed = _search_completed(payload)
        new_result = TravelokaCaptureResult(
            payload=payload,
            source_path=path,
            search_completed=search_completed,
            timed_out=False,
        )
        result_count = _search_result_count(payload)
        if self.best_result is None or result_count > 0:
            self.best_result = new_result
        elif search_completed and _search_result_count(self.best_result.payload) > 0:
            self.best_result = TravelokaCaptureResult(
                payload=self.best_result.payload,
                source_path=self.best_result.source_path,
                search_completed=True,
                timed_out=False,
            )
        elif search_completed:
            self.best_result = new_result
        self.completed = self.completed or search_completed


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
    """Legacy wrapper for callers that still use the old helper name."""
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


def _default_launch_browser(**kwargs: object) -> object:
    from cloakbrowser import launch

    return launch(**kwargs)


def _is_supported_fare_payload(payload: dict[str, object]) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    return isinstance(data.get("searchResults"), list)


def _search_result_count(payload: dict[str, object]) -> int:
    data = payload.get("data")
    if not isinstance(data, dict):
        return 0
    search_results = data.get("searchResults")
    if not isinstance(search_results, list):
        return 0
    return len(search_results)


def _search_completed(payload: dict[str, object]) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return False
    return meta.get("searchCompleted") is True


def _close_quietly(target: object | None) -> None:
    if target is None:
        return
    close = getattr(target, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        return


def _remaining_timeout_ms(deadline: float, *, raise_on_expired: bool = True) -> int:
    remaining_seconds = deadline - monotonic()
    if remaining_seconds <= 0:
        if not raise_on_expired:
            return 0
        raise _timeout_error()
    return max(1, round(remaining_seconds * 1000))


def _is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    type_name = type(exc).__name__.lower()
    module_name = type(exc).__module__.lower()
    return "timeout" in type_name or (
        "playwright" in module_name and "timeout" in type_name
    )


def _timeout_error(exception_type: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="timeout",
        message_en="Traveloka request timed out.",
        error_code=ErrorCode.PROVIDER_TIMEOUT,
        retryable=True,
        exception_type=exception_type,
    )


def _browser_unavailable_error(exception_type: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="browser_unavailable",
        message_en="Traveloka browser runtime is unavailable.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def _navigation_failed_error(exception_type: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="navigation_failed",
        message_en="Traveloka browser navigation failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def _blocked_error(http_status_code: int | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="blocked",
        message_en="Traveloka returned an access challenge.",
        error_code=ErrorCode.PROVIDER_BLOCKED,
        retryable=False,
        http_status_code=http_status_code,
    )


def _rate_limited_error(http_status_code: int | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="rate_limited",
        message_en="Traveloka rate limited the request.",
        error_code=ErrorCode.PROVIDER_RATE_LIMITED,
        retryable=True,
        http_status_code=http_status_code,
    )


def _transport_error(http_status_code: int | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="transport_error",
        message_en="Traveloka transport failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=http_status_code is None or http_status_code >= 500,
        http_status_code=http_status_code,
    )


def _invalid_json_error(exception_type: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="invalid_json",
        message_en="Traveloka returned invalid JSON.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
        exception_type=exception_type,
    )


def _unsupported_response_error() -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="unsupported_response",
        message_en="Traveloka returned an unsupported response.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def _raise_blocked_if_terminal_page(content: str) -> None:
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
        raise _blocked_error()
