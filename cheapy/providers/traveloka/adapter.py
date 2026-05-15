"""Browser capture adapter for the Traveloka research provider."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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
VISIBLE_OPTION_CLICK_TIMEOUT_MS = 10_000
_USD_PRICE_AFTER_MARKER_RE = re.compile(
    r"(?:USD|\$)\s*(\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_USD_PRICE_BEFORE_MARKER_RE = re.compile(
    r"(?<!:)(\d[\d,]*(?:\.\d+)?)\s*USD\b",
    re.IGNORECASE,
)
_VND_PRICE_AFTER_MARKER_RE = re.compile(
    r"(?:\u20ab|VND)\s*(\d[\d.,]*)",
    re.IGNORECASE,
)
_VND_PRICE_BEFORE_MARKER_RE = re.compile(
    r"(?<!:)(\d[\d.,]*)\s*(?:\u20ab|VND\b)",
    re.IGNORECASE,
)
_VISIBLE_OPTION_KEY_TEXT_RE = re.compile(
    r"\b(?:data-testid|flight id|id|offer id|offerid|itinerary id|itineraryid)"
    r"\s*[:=#]\s*([A-Za-z0-9][A-Za-z0-9._:-]{0,127})",
    re.IGNORECASE,
)
_STABLE_OPTION_KEY_ATTRIBUTES = (
    "data-testid",
    "data-test-id",
    "data-flight-id",
    "data-result-id",
    "data-offer-id",
    "data-itinerary-id",
    "id",
)

ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
BrowserLauncher = Callable[..., object]


@dataclass(frozen=True)
class TravelokaCaptureResult:
    payload: dict[str, object]
    source_path: str
    search_completed: bool
    timed_out: bool = False
    partial_failure_type: str | None = None


@dataclass(frozen=True)
class TravelokaSelectedRoundTripResult:
    outbound_payload: dict[str, object]
    return_payload: dict[str, object]
    selected_outbound_key: str | None
    selected_return_key: str | None
    final_total_amount: Decimal
    final_total_currency: str
    source_paths: tuple[str, ...]
    timed_out: bool = False


@dataclass(frozen=True)
class TravelokaVisibleOption:
    key: str | None
    airline_name: str | None
    departure_time_text: str | None
    arrival_time_text: str | None
    route_text: str | None
    price_amount: Decimal
    currency: str | None
    locator: object


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
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        return self._search_selected_round_trip(request)

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

            try:
                return _wait_for_capture(
                    state,
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    _raise_blocked_if_terminal_page(page.content())  # type: ignore[attr-defined]
                raise
        except TravelokaProviderError:
            raise
        except Exception as exc:
            if _is_timeout_exception(exc):
                raise _timeout_error(type(exc).__name__) from None
            raise _navigation_failed_error(type(exc).__name__) from None
        finally:
            _close_quietly(context)
            _close_quietly(browser)

    def _search_selected_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
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

            try:
                outbound_capture = _wait_for_capture(
                    state,
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    _raise_blocked_if_terminal_page(page.content())  # type: ignore[attr-defined]
                raise
            if outbound_capture.timed_out:
                return outbound_capture

            outbound_selection_timeout_ms = _remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if outbound_selection_timeout_ms <= 0:
                return _partial_round_trip_result(
                    outbound_capture,
                    "outbound_selection_unavailable",
                )
            outbound_option = _cheapest_visible_option(
                _visible_options_from_page(
                    page,
                    deadline=deadline,
                )
            )
            if outbound_option is None:
                return _partial_round_trip_result(
                    outbound_capture,
                    "outbound_selection_unavailable",
                )
            outbound_key = _bind_visible_option_to_payload(
                outbound_option,
                outbound_capture.payload,
            )
            if outbound_key is None:
                return _partial_round_trip_result(
                    outbound_capture,
                    "selected_outbound_binding_unavailable",
                )

            state.reset()
            _click_visible_option(
                outbound_option,
                timeout_ms=_remaining_timeout_ms(deadline),
            )
            try:
                return_capture = _wait_for_capture(
                    state,
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    return _partial_round_trip_result(
                        outbound_capture,
                        "return_capture_timeout",
                    )
                raise
            if return_capture.timed_out:
                return _partial_round_trip_result(
                    outbound_capture,
                    "return_capture_timeout",
                )

            return_selection_timeout_ms = _remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if return_selection_timeout_ms <= 0:
                return _partial_round_trip_result(
                    outbound_capture,
                    "return_selection_unavailable",
                )
            return_option = _cheapest_visible_option(
                _visible_options_from_page(
                    page,
                    deadline=deadline,
                )
            )
            if return_option is None:
                return _partial_round_trip_result(
                    outbound_capture,
                    "return_selection_unavailable",
                )
            return_key = _bind_visible_option_to_payload(
                return_option,
                return_capture.payload,
            )
            if return_key is None:
                return _partial_round_trip_result(
                    outbound_capture,
                    "selected_return_binding_unavailable",
                )

            return_click_timeout_ms = _remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if return_click_timeout_ms <= 0:
                return _partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )
            _click_visible_option(
                return_option,
                timeout_ms=return_click_timeout_ms,
            )
            if not _wait_after_return_selection(
                page,
                deadline,
                poll_interval_seconds=self._poll_interval_seconds,
            ):
                return _partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )
            final_total_timeout_ms = _remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if final_total_timeout_ms <= 0:
                return _partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )
            final_total = _read_final_total(page, deadline=deadline)
            if final_total is None:
                return _partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )

            final_amount, final_currency = final_total
            return TravelokaSelectedRoundTripResult(
                outbound_payload=outbound_capture.payload,
                return_payload=return_capture.payload,
                selected_outbound_key=outbound_key,
                selected_return_key=return_key,
                final_total_amount=final_amount,
                final_total_currency=final_currency,
                source_paths=(outbound_capture.source_path, return_capture.source_path),
                timed_out=False,
            )
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

    def reset(self) -> None:
        self.best_result = None
        self.completed = False

    def handle_response(self, response: object) -> None:
        response_url = str(getattr(response, "url", ""))
        if not _is_traveloka_first_party_url(response_url):
            return

        path = urlparse(response_url).path
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
                partial_failure_type=self.best_result.partial_failure_type,
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


def _cheapest_visible_option(
    options: Iterable[TravelokaVisibleOption],
) -> TravelokaVisibleOption | None:
    return min(
        options,
        key=lambda option: (
            option.price_amount,
            _visible_option_key_rank(option.key),
            option.key or "",
            option.airline_name or "",
        ),
        default=None,
    )


def _visible_option_key_rank(key: str | None) -> int:
    if not key:
        return 2
    if key.isdecimal():
        return 1
    return 0


def _parse_visible_price(text: str) -> tuple[Decimal, str]:
    normalized = " ".join(text.replace("\xa0", " ").split())

    vnd_amount = _price_amount_near_marker(
        normalized,
        _VND_PRICE_AFTER_MARKER_RE,
        _VND_PRICE_BEFORE_MARKER_RE,
    )
    if vnd_amount is not None:
        amount_text = "".join(
            character for character in vnd_amount if character.isdigit()
        )
        return Decimal(amount_text), "VND"

    usd_amount = _price_amount_near_marker(
        normalized,
        _USD_PRICE_AFTER_MARKER_RE,
        _USD_PRICE_BEFORE_MARKER_RE,
    )
    if usd_amount is not None:
        return Decimal(usd_amount.replace(",", "")), "USD"

    raise ValueError("visible price did not include a supported currency")


def _bind_visible_option_to_payload(
    option: TravelokaVisibleOption,
    payload: dict[str, object],
) -> str | None:
    if option.key is not None and option.key in _explicit_payload_item_ids(payload):
        return option.key
    return None


def _visible_options_from_page(
    page: object,
    *,
    timeout_ms: int = VISIBLE_OPTION_CLICK_TIMEOUT_MS,
    deadline: float | None = None,
) -> list[TravelokaVisibleOption]:
    timeout_ms = max(1, timeout_ms)
    try:
        cards = page.locator(  # type: ignore[attr-defined]
            "button:has-text('Choose'), button:has-text('Ch\u1ecdn')"
        )
        count = cards.count()
    except Exception:
        return []

    options: list[TravelokaVisibleOption] = []
    for index in range(count):
        try:
            locator = cards.nth(index)
        except Exception:
            continue
        key = _stable_key_from_locator(
            locator,
            timeout_ms=timeout_ms,
            deadline=deadline,
        )
        try:
            ancestor_locator = locator.locator("xpath=ancestor::*[self::div][1]")
            if key is None:
                key = _stable_key_from_locator(
                    ancestor_locator,
                    timeout_ms=timeout_ms,
                    deadline=deadline,
                )
            text_timeout_ms = _dom_operation_timeout_ms(
                timeout_ms=timeout_ms,
                deadline=deadline,
            )
            if text_timeout_ms is None:
                break
            text = ancestor_locator.inner_text(timeout=text_timeout_ms)
        except Exception:
            try:
                text_timeout_ms = _dom_operation_timeout_ms(
                    timeout_ms=timeout_ms,
                    deadline=deadline,
                )
                if text_timeout_ms is None:
                    break
                text = locator.inner_text(timeout=text_timeout_ms)
            except Exception:
                continue
        parsed = _visible_option_from_text(text, locator, key=key)
        if parsed is not None:
            options.append(parsed)
    return options


def _visible_option_from_text(
    text: str,
    locator: object,
    *,
    key: str | None = None,
) -> TravelokaVisibleOption | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    price_line = next(
        (
            line
            for line in reversed(lines)
            if any(
                marker in line.upper()
                for marker in ("US$", "USD", "VND", "\u20ab", "$")
            )
        ),
        None,
    )
    if price_line is None:
        return None
    try:
        amount, currency = _parse_visible_price(price_line)
    except Exception:
        return None
    return TravelokaVisibleOption(
        key=key or _stable_key_from_text(text),
        airline_name=lines[0] if lines else None,
        departure_time_text=None,
        arrival_time_text=None,
        route_text=None,
        price_amount=amount,
        currency=currency,
        locator=locator,
    )


def _stable_key_from_locator(
    locator: object,
    *,
    timeout_ms: int,
    deadline: float | None = None,
) -> str | None:
    for attribute_name in _STABLE_OPTION_KEY_ATTRIBUTES:
        attribute_timeout_ms = _dom_operation_timeout_ms(
            timeout_ms=timeout_ms,
            deadline=deadline,
        )
        if attribute_timeout_ms is None:
            return None
        value = _locator_attribute(
            locator,
            attribute_name,
            timeout_ms=attribute_timeout_ms,
        )
        if value is not None:
            return value
    return None


def _locator_attribute(
    locator: object,
    attribute_name: str,
    *,
    timeout_ms: int,
) -> str | None:
    get_attribute = getattr(locator, "get_attribute", None)
    if get_attribute is None:
        return None
    try:
        value = get_attribute(attribute_name, timeout=timeout_ms)
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _stable_key_from_text(text: str) -> str | None:
    match = _VISIBLE_OPTION_KEY_TEXT_RE.search(text)
    if match is None:
        return None
    return match.group(1)


def _read_final_total(
    page: object,
    *,
    timeout_ms: int = 1000,
    deadline: float | None = None,
) -> tuple[Decimal, str] | None:
    timeout_ms = max(1, timeout_ms)
    total_selectors = (
        "[data-testid*='selected'][data-testid*='total']",
        "[data-testid*='final'][data-testid*='total']",
        "[data-testid*='checkout'][data-testid*='total']",
        "[aria-label*='selected' i][aria-label*='total' i]",
        "[aria-label*='final' i][aria-label*='total' i]",
        "[aria-label*='checkout' i][aria-label*='total' i]",
        "text=/selected\\s+(?:final\\s+)?total/i",
        "text=/final\\s+total/i",
        "text=/checkout\\s+total/i",
    )
    for selector in total_selectors:
        try:
            locator = page.locator(selector).first()  # type: ignore[attr-defined]
            text_timeout_ms = _dom_operation_timeout_ms(
                timeout_ms=timeout_ms,
                deadline=deadline,
            )
            if text_timeout_ms is None:
                return None
            text = locator.inner_text(timeout=text_timeout_ms)
        except Exception:
            continue
        try:
            amount, currency = _parse_visible_price(text)
        except Exception:
            continue
        return amount, currency
    return None


def _dom_operation_timeout_ms(
    *,
    timeout_ms: int,
    deadline: float | None,
) -> int | None:
    if deadline is None:
        return max(1, timeout_ms)
    remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
    if remaining_ms <= 0:
        return None
    return remaining_ms


def _click_visible_option(
    option: TravelokaVisibleOption,
    *,
    timeout_ms: int = VISIBLE_OPTION_CLICK_TIMEOUT_MS,
) -> None:
    option.locator.click(timeout=timeout_ms)  # type: ignore[attr-defined]


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


def _is_traveloka_first_party_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        return False
    host = host.lower().rstrip(".")
    return host == "traveloka.com" or host.endswith(".traveloka.com")


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


def _price_amount_near_marker(
    text: str,
    after_marker_pattern: re.Pattern[str],
    before_marker_pattern: re.Pattern[str],
) -> str | None:
    after_marker_match = after_marker_pattern.search(text)
    if after_marker_match is not None:
        return after_marker_match.group(1)
    before_marker_match = before_marker_pattern.search(text)
    if before_marker_match is not None:
        return before_marker_match.group(1)
    return None


def _explicit_payload_item_ids(payload: object) -> set[str]:
    ids: set[str] = set()
    for path in (
        ("data", "searchResults"),
        ("data", "itineraries"),
        ("data", "flightSearchResult", "itineraries"),
        ("itineraries",),
        ("flightSearchResult", "itineraries"),
    ):
        items = _payload_list_at_path(payload, path)
        if items is None:
            continue
        for item in items:
            item_id = _explicit_item_id(item)
            if item_id is not None:
                ids.add(item_id)
    return ids


def _payload_list_at_path(payload: object, path: tuple[str, ...]) -> list[object] | None:
    current = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if isinstance(current, list):
        return list(current)
    if isinstance(current, tuple):
        return list(current)
    return None


def _explicit_item_id(item: object) -> str | None:
    if not isinstance(item, Mapping):
        return None
    for key in ("id", "offerId", "itineraryId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


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


def _wait_for_capture(
    state: _CaptureState,
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
) -> TravelokaCaptureResult:
    while not state.completed and monotonic() < deadline:
        remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]

    if state.best_result is None:
        raise _timeout_error()
    if state.completed:
        return state.best_result
    return TravelokaCaptureResult(
        payload=state.best_result.payload,
        source_path=state.best_result.source_path,
        search_completed=state.best_result.search_completed,
        timed_out=True,
        partial_failure_type=state.best_result.partial_failure_type,
    )


def _wait_after_return_selection(
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
) -> bool:
    remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
    if remaining_ms <= 0:
        return False
    wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
    if wait_ms <= 0:
        return False
    page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]
    return _remaining_timeout_ms(deadline, raise_on_expired=False) > 0


def _partial_round_trip_result(
    capture: TravelokaCaptureResult,
    failure_type: str,
) -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload=capture.payload,
        source_path=capture.source_path,
        search_completed=capture.search_completed,
        timed_out=capture.timed_out,
        partial_failure_type=failure_type,
    )


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
