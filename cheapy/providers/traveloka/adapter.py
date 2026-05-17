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
from cheapy.providers.traveloka.timing import (
    TravelokaPhaseRecorder,
    TravelokaPhaseTiming,
)


DEFAULT_BASE_URL = "https://www.traveloka.com/en-en/flight/fulltwosearch"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_CURRENCY = "USD"
DEFAULT_LOCALE = "en-en"
INITIAL_SEARCH_PATH = "/api/v2/flight/search/initial"
POLL_SEARCH_PATH = "/api/v2/flight/search/poll"
SUPPORTED_FARE_PATHS = {INITIAL_SEARCH_PATH, POLL_SEARCH_PATH}
VISIBLE_OPTION_CLICK_TIMEOUT_MS = 10_000
SELECTION_TRANSITION_TIMEOUT_MS = 10_000
FINAL_TOTAL_READ_TIMEOUT_MS = 250
INVENTORY_CARD_TEST_ID_PREFIX = "flight-inventory-card-container-"
INVENTORY_CARD_SELECTOR = (
    f"[data-testid^='{INVENTORY_CARD_TEST_ID_PREFIX}']"
)
INVENTORY_CARD_BUTTON_SELECTOR = (
    "[data-testid='flight-inventory-card-button'], "
    "[role='button']:has-text('Choose'), "
    "[role='button']:has-text('Ch\u1ecdn')"
)
LEGACY_CHOOSE_BUTTON_SELECTOR = "button:has-text('Choose'), button:has-text('Ch\u1ecdn')"
TRAVELOKA_OPTION_ACTIVATION_SCRIPT = """
node => {
  const base = {bubbles: true, cancelable: true, composed: true, view: window};
  const pointer = (type, buttons) => {
    if (typeof PointerEvent === 'function') {
      return new PointerEvent(type, Object.assign({}, base, {
        button: 0,
        buttons,
        pointerType: 'mouse',
        isPrimary: true,
      }));
    }
    return new MouseEvent(type, Object.assign({}, base, {button: 0, buttons}));
  };
  node.dispatchEvent(pointer('pointerdown', 1));
  node.dispatchEvent(new MouseEvent(
    'mousedown',
    Object.assign({}, base, {button: 0, buttons: 1})
  ));
  node.dispatchEvent(pointer('pointerup', 0));
  node.dispatchEvent(new MouseEvent(
    'mouseup',
    Object.assign({}, base, {button: 0, buttons: 0})
  ));
  node.dispatchEvent(new MouseEvent(
    'click',
    Object.assign({}, base, {button: 0, buttons: 0})
  ));
}
"""
_FINAL_TOTAL_SELECTED_TIER = "selected_total"
_FINAL_TOTAL_SUMMARY_TIER = "summary"
_FINAL_TOTAL_GLOBAL_LABEL_TIER = "global_label"

_FINAL_TOTAL_SELECTED_SELECTORS: tuple[tuple[str, bool], ...] = (
    ("[data-testid*='selected'][data-testid*='total']", False),
    ("[data-testid*='final'][data-testid*='total']", True),
    ("[data-testid*='checkout'][data-testid*='total']", True),
    ("[aria-label*='selected' i][aria-label*='total' i]", False),
    ("[aria-label*='final' i][aria-label*='total' i]", True),
    ("[aria-label*='checkout' i][aria-label*='total' i]", True),
    ("text=/selected\\s+(?:final\\s+)?total/i", False),
    ("text=/final\\s+total/i", False),
    ("text=/checkout\\s+total/i", False),
)
_FINAL_TOTAL_SUMMARY_SELECTORS: tuple[str, ...] = (
    "#flight-search-result",
    "[data-testid='bundle-summary-tray']",
    "[data-testid*='bundle-summary']",
    "[data-testid*='selected'][data-testid*='summary']",
    "[data-testid*='summary'][data-testid*='tray']",
    "[aria-label*='selected' i][aria-label*='summary' i]",
    "[aria-label*='summary' i][aria-label*='tray' i]",
)
_FINAL_TOTAL_GLOBAL_LABEL_SELECTOR = "[data-testid='label_fl_inventory_price']"
_USD_PRICE_AFTER_MARKER_RE = re.compile(
    r"(?:USD|US\$|\$)\s*(\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_USD_PRICE_BEFORE_MARKER_RE = re.compile(
    r"(?<!:)(\d[\d,]*(?:\.\d+)?)\s*(?:USD\b|US\$)",
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
_EXPLICIT_TOTAL_PRICE_RE = re.compile(
    r"(?<!\baddon\s)(?<!\baddons\s)(?<!\badd-on\s)(?<!\badd-ons\s)"
    r"\btotal\b\s*((?:USD|US\$|\$|VND|\u20ab)\s*\d[\d,.]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_EXPLICIT_SUMMARY_PRICE_RE = re.compile(
    r"(?:(?<!\baddon\s)(?<!\baddons\s)(?<!\badd-on\s)(?<!\badd-ons\s)"
    r"\btotal\b|\bround-trip\s+price\b)"
    r"\s*((?:USD|US\$|\$|VND|\u20ab)\s*\d[\d,.]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_EXPLICIT_ROUND_TRIP_PRICE_RE = re.compile(
    r"\bround-trip\s+price\b\s*((?:USD|US\$|\$|VND|\u20ab)\s*\d[\d,.]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_EXPLICIT_SELECTED_TOTAL_PRICE_RE = re.compile(
    r"\b(?:final\s+total|checkout\s+total|selected\s+final\s+total|selected\s+total)\b"
    r"\s*((?:USD|US\$|\$|VND|\u20ab)\s*\d[\d,.]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_SCOPED_TOTAL_PRICE_ONLY_RE = re.compile(
    r"^\s*(?:(?:USD|US\$|\$|VND|\u20ab)\s*\d[\d,.]*(?:\.\d+)?"
    r"|\d[\d,.]*(?:\.\d+)?\s*(?:USD|US\$|VND|\u20ab))"
    r"(?:\s*/?\s*(?:pax|passenger|person))?\s*$",
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
        self._phase_recorder = TravelokaPhaseRecorder(clock=monotonic)

    @property
    def phase_timings(self) -> tuple[TravelokaPhaseTiming, ...]:
        return self._phase_recorder.records

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
                with self._phase_recorder.phase("browser_launch"):
                    browser = self._launch_browser(
                        headless=True,
                        timeout=_remaining_timeout_ms(deadline),
                    )
            except Exception as exc:
                if _is_timeout_exception(exc):
                    raise _timeout_error(type(exc).__name__) from None
                raise _browser_unavailable_error(type(exc).__name__) from None

            with self._phase_recorder.phase("context_page_setup"):
                _remaining_timeout_ms(deadline)
                context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
                _remaining_timeout_ms(deadline)
                page = context.new_page()  # type: ignore[attr-defined]
                _remaining_timeout_ms(deadline)
                page.on("response", state.handle_response)  # type: ignore[attr-defined]
            with self._phase_recorder.phase("initial_navigation"):
                _remaining_timeout_ms(deadline)
                page.goto(  # type: ignore[attr-defined]
                    build_full_search_url(request, base_url=self._base_url),
                    wait_until="domcontentloaded",
                    timeout=_remaining_timeout_ms(deadline),
                )

            try:
                with self._phase_recorder.phase("outbound_capture_wait"):
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
            with self._phase_recorder.phase("cleanup"):
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
                with self._phase_recorder.phase("browser_launch"):
                    browser = self._launch_browser(
                        headless=True,
                        timeout=_remaining_timeout_ms(deadline),
                    )
            except Exception as exc:
                if _is_timeout_exception(exc):
                    raise _timeout_error(type(exc).__name__) from None
                raise _browser_unavailable_error(type(exc).__name__) from None

            with self._phase_recorder.phase("context_page_setup"):
                _remaining_timeout_ms(deadline)
                context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
                _remaining_timeout_ms(deadline)
                page = context.new_page()  # type: ignore[attr-defined]
                _remaining_timeout_ms(deadline)
                page.on("response", state.handle_response)  # type: ignore[attr-defined]
            with self._phase_recorder.phase("initial_navigation"):
                _remaining_timeout_ms(deadline)
                page.goto(  # type: ignore[attr-defined]
                    build_full_search_url(request, base_url=self._base_url),
                    wait_until="domcontentloaded",
                    timeout=_remaining_timeout_ms(deadline),
                )

            try:
                with self._phase_recorder.phase("outbound_capture_wait"):
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
            with self._phase_recorder.phase("outbound_visible_option_discovery"):
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
            with self._phase_recorder.phase("outbound_binding"):
                outbound_key = _bind_visible_option_to_payload(
                    outbound_option,
                    outbound_capture.payload,
                )
                if outbound_key is None:
                    return _partial_round_trip_result(
                        outbound_capture,
                        "selected_outbound_binding_unavailable",
                    )

            with self._phase_recorder.phase("outbound_click_transition"):
                before_outbound_selection_url = str(getattr(page, "url", ""))
                before_outbound_selection_body = _read_body_text(
                    page,
                    timeout_ms=250,
                    deadline=deadline,
                )
                state.reset()
                _click_visible_option(
                    outbound_option,
                    timeout_ms=_remaining_timeout_ms(deadline),
                )
                if not _wait_for_outbound_selection_transition(
                    state,
                    page,
                    outbound_key,
                    deadline,
                    outbound_payload=outbound_capture.payload,
                    before_url=before_outbound_selection_url,
                    before_body_text=before_outbound_selection_body,
                    poll_interval_seconds=self._poll_interval_seconds,
                ):
                    return _partial_round_trip_result(
                        outbound_capture,
                        "outbound_selection_transition_unavailable",
                    )
            try:
                with self._phase_recorder.phase("return_capture_wait"):
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
            with self._phase_recorder.phase("return_visible_option_discovery"):
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
            with self._phase_recorder.phase("return_binding"):
                return_key = _bind_visible_option_to_payload(
                    return_option,
                    return_capture.payload,
                )
                if return_key is None:
                    return _partial_round_trip_result(
                        outbound_capture,
                        "selected_return_binding_unavailable",
                    )

            with self._phase_recorder.phase("return_click_transition"):
                return_click_timeout_ms = _remaining_timeout_ms(
                    deadline,
                    raise_on_expired=False,
                )
                if return_click_timeout_ms <= 0:
                    return _partial_round_trip_result(
                        outbound_capture,
                        "final_round_trip_total_unavailable",
                    )
                before_final_total_texts = _final_total_texts(page, deadline=deadline)
                before_return_selection_marker_texts = _return_selection_marker_texts(
                    page,
                    deadline=deadline,
                )
                before_return_selection_body = _read_body_text(
                    page,
                    timeout_ms=250,
                    deadline=deadline,
                )
                _click_visible_option(
                    return_option,
                    timeout_ms=return_click_timeout_ms,
                )
                if not _wait_for_return_selection_transition(
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                    before_marker_texts=before_return_selection_marker_texts,
                    before_body_text=before_return_selection_body,
                ):
                    return _partial_round_trip_result(
                        outbound_capture,
                        "final_round_trip_total_unavailable",
                    )
            with self._phase_recorder.phase("final_total_read"):
                final_total = _wait_for_final_total(
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                    before_texts=before_final_total_texts,
                )
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
            with self._phase_recorder.phase("cleanup"):
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


def _parse_explicit_price(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[Decimal, str] | None:
    normalized = " ".join(text.replace("\xa0", " ").split())
    match = pattern.search(normalized)
    if match is None:
        return None
    try:
        return _parse_visible_price(match.group(1))
    except Exception:
        return None


def _parse_explicit_prices(
    text: str,
    pattern: re.Pattern[str],
) -> list[tuple[Decimal, str]]:
    normalized = " ".join(text.replace("\xa0", " ").split())
    prices: list[tuple[Decimal, str]] = []
    for match in pattern.finditer(normalized):
        try:
            prices.append(_parse_visible_price(match.group(1)))
        except Exception:
            continue
    return prices


def _parse_selected_total_price(
    text: str,
    *,
    allow_price_only: bool,
) -> tuple[Decimal, str] | None:
    explicit_total = _parse_explicit_price(text, _EXPLICIT_SELECTED_TOTAL_PRICE_RE)
    if explicit_total is not None:
        return explicit_total

    if not allow_price_only:
        return None
    normalized = " ".join(text.replace("\xa0", " ").split())
    if _SCOPED_TOTAL_PRICE_ONLY_RE.fullmatch(normalized) is None:
        return None
    try:
        return _parse_visible_price(normalized)
    except Exception:
        return None


def _parse_summary_total_price(text: str) -> tuple[Decimal, str] | None:
    round_trip_price = _parse_explicit_price(text, _EXPLICIT_ROUND_TRIP_PRICE_RE)
    if round_trip_price is not None:
        return round_trip_price
    return _parse_explicit_price(text, _EXPLICIT_SUMMARY_PRICE_RE)


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
    inventory_options = _visible_options_from_inventory_cards(
        page,
        timeout_ms=timeout_ms,
        deadline=deadline,
    )
    if inventory_options:
        return inventory_options

    return _visible_options_from_legacy_buttons(
        page,
        timeout_ms=timeout_ms,
        deadline=deadline,
    )


def _visible_options_from_inventory_cards(
    page: object,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> list[TravelokaVisibleOption]:
    try:
        cards = page.locator(INVENTORY_CARD_SELECTOR)  # type: ignore[attr-defined]
        count = cards.count()
    except Exception:
        return []

    options: list[TravelokaVisibleOption] = []
    for index in range(count):
        try:
            card_locator = cards.nth(index)
        except Exception:
            continue

        key = _stable_key_from_locator(
            card_locator,
            timeout_ms=timeout_ms,
            deadline=deadline,
        )
        try:
            text_timeout_ms = _dom_operation_timeout_ms(
                timeout_ms=timeout_ms,
                deadline=deadline,
            )
            if text_timeout_ms is None:
                break
            text = card_locator.inner_text(timeout=text_timeout_ms)
        except Exception:
            continue

        button_locator = _selection_action_from_card(card_locator)
        if button_locator is None:
            continue

        parsed = _visible_option_from_text(text, button_locator, key=key)
        if parsed is not None:
            options.append(parsed)
    return options


def _visible_options_from_legacy_buttons(
    page: object,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> list[TravelokaVisibleOption]:
    try:
        cards = page.locator(LEGACY_CHOOSE_BUTTON_SELECTOR)  # type: ignore[attr-defined]
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


def _selection_action_from_card(card_locator: object) -> object | None:
    try:
        actions = card_locator.locator(INVENTORY_CARD_BUTTON_SELECTOR)
        if actions.count() <= 0:
            return None
        return _first_locator(actions)
    except Exception:
        return None


def _first_locator(locator_collection: object) -> object:
    first = getattr(locator_collection, "first", None)
    if first is not None:
        return first() if callable(first) else first
    return locator_collection.nth(0)  # type: ignore[attr-defined]


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
        key = _stable_key_from_attribute(value)
        if key is not None:
            return key
    return None


def _stable_key_from_attribute(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "flight-inventory-card-button":
        return None
    if value.startswith(INVENTORY_CARD_TEST_ID_PREFIX):
        key = value.removeprefix(INVENTORY_CARD_TEST_ID_PREFIX).strip()
        return key or None
    return value


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


@dataclass
class _FinalTotalSelectorCache:
    tier: str | None = None
    selector: str | None = None

    def record(self, *, tier: str, selector: str) -> None:
        self.tier = tier
        self.selector = selector


def _ordered_final_total_selector_items(
    *,
    tier: str,
    selectors: tuple[tuple[str, bool], ...],
    selector_cache: _FinalTotalSelectorCache | None,
) -> tuple[tuple[str, bool], ...]:
    if selector_cache is None or selector_cache.tier != tier:
        return selectors
    cached = selector_cache.selector
    if cached is None:
        return selectors
    cached_items = tuple(item for item in selectors if item[0] == cached)
    if not cached_items:
        return selectors
    remaining = tuple(item for item in selectors if item[0] != cached)
    return (*cached_items, *remaining)


def _ordered_final_total_selectors(
    *,
    tier: str,
    selectors: tuple[str, ...],
    selector_cache: _FinalTotalSelectorCache | None,
) -> tuple[str, ...]:
    if selector_cache is None or selector_cache.tier != tier:
        return selectors
    cached = selector_cache.selector
    if cached is None or cached not in selectors:
        return selectors
    return (cached, *(selector for selector in selectors if selector != cached))


def _read_final_total(
    page: object,
    *,
    timeout_ms: int = 1000,
    deadline: float | None = None,
    before_texts: Iterable[str] = (),
    selector_cache: _FinalTotalSelectorCache | None = None,
) -> tuple[Decimal, str] | None:
    timeout_ms = max(1, timeout_ms)
    before_text_keys = {_normalized_text_key(text) for text in before_texts}
    for selector, allow_price_only in _ordered_final_total_selector_items(
        tier=_FINAL_TOTAL_SELECTED_TIER,
        selectors=_FINAL_TOTAL_SELECTED_SELECTORS,
        selector_cache=selector_cache,
    ):
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=timeout_ms,
            deadline=deadline,
        ):
            if _normalized_text_key(text) in before_text_keys:
                continue
            parsed = _parse_selected_total_price(
                text,
                allow_price_only=allow_price_only,
            )
            if parsed is not None:
                if selector_cache is not None:
                    selector_cache.record(
                        tier=_FINAL_TOTAL_SELECTED_TIER,
                        selector=selector,
                    )
                return parsed

    all_summary_texts: list[tuple[str, str]] = []
    for selector in _ordered_final_total_selectors(
        tier=_FINAL_TOTAL_SUMMARY_TIER,
        selectors=_FINAL_TOTAL_SUMMARY_SELECTORS,
        selector_cache=selector_cache,
    ):
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=timeout_ms,
            deadline=deadline,
        ):
            all_summary_texts.append((selector, text))
    all_summary_texts = [
        (selector, text)
        for selector, text in all_summary_texts
        if _normalized_text_key(text) not in before_text_keys
    ]
    for selector, text in all_summary_texts:
        parsed = _parse_explicit_price(text, _EXPLICIT_ROUND_TRIP_PRICE_RE)
        if parsed is not None:
            if selector_cache is not None:
                selector_cache.record(
                    tier=_FINAL_TOTAL_SUMMARY_TIER,
                    selector=selector,
                )
            return parsed
    for selector, text in all_summary_texts:
        parsed = _parse_explicit_price(text, _EXPLICIT_SUMMARY_PRICE_RE)
        if parsed is not None:
            if selector_cache is not None:
                selector_cache.record(
                    tier=_FINAL_TOTAL_SUMMARY_TIER,
                    selector=selector,
                )
            return parsed

    label_totals: list[tuple[Decimal, str]] = []
    for text in _locator_texts(
        page,
        _FINAL_TOTAL_GLOBAL_LABEL_SELECTOR,
        timeout_ms=timeout_ms,
        deadline=deadline,
    ):
        if _normalized_text_key(text) in before_text_keys:
            continue
        label_totals.extend(_parse_explicit_prices(text, _EXPLICIT_TOTAL_PRICE_RE))
    if len(label_totals) == 1:
        if selector_cache is not None:
            selector_cache.record(
                tier=_FINAL_TOTAL_GLOBAL_LABEL_TIER,
                selector=_FINAL_TOTAL_GLOBAL_LABEL_SELECTOR,
            )
        return label_totals[0]
    return None


def _final_total_texts(
    page: object,
    *,
    deadline: float | None = None,
) -> tuple[str, ...]:
    texts: list[str] = []
    for selector, _allow_price_only in _FINAL_TOTAL_SELECTED_SELECTORS:
        texts.extend(_locator_texts(page, selector, timeout_ms=250, deadline=deadline))
    for selector in _FINAL_TOTAL_SUMMARY_SELECTORS:
        texts.extend(_locator_texts(page, selector, timeout_ms=250, deadline=deadline))
    texts.extend(
        _locator_texts(
            page,
            _FINAL_TOTAL_GLOBAL_LABEL_SELECTOR,
            timeout_ms=250,
            deadline=deadline,
        )
    )
    return tuple(texts)


def _locator_texts(
    page: object,
    selector: str,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> list[str]:
    try:
        locators = page.locator(selector)  # type: ignore[attr-defined]
    except Exception:
        return []

    local_budget_ms = max(1, timeout_ms)

    def next_timeout_ms() -> int | None:
        if local_budget_ms <= 0:
            return None
        if deadline is None:
            return local_budget_ms
        remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            return None
        return max(1, min(local_budget_ms, remaining_ms))

    def read_text(locator: object) -> str | None:
        nonlocal local_budget_ms
        text_timeout_ms = next_timeout_ms()
        if text_timeout_ms is None:
            return None
        started_at = monotonic()
        try:
            text = locator.inner_text(timeout=text_timeout_ms)
        except Exception:
            return None
        finally:
            elapsed_ms = int((monotonic() - started_at) * 1000)
            local_budget_ms = max(0, local_budget_ms - elapsed_ms)
        return text if isinstance(text, str) else None

    first = getattr(locators, "first", None)
    if callable(first):
        try:
            first_locator = first()
        except Exception:
            return []
    else:
        first_locator = locators

    texts: list[str] = []
    first_text = read_text(first_locator)
    if first_text is not None:
        texts.append(first_text)

    count = getattr(locators, "count", None)
    if not callable(count):
        return texts

    try:
        locator_count = count()
    except Exception:
        return texts

    for index in range(1, locator_count):
        try:
            locator = locators.nth(index)
        except Exception:
            continue
        text = read_text(locator)
        if text is None and next_timeout_ms() is None:
            break
        if text is not None:
            texts.append(text)
    return texts


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
    return max(1, min(timeout_ms, remaining_ms))


def _click_visible_option(
    option: TravelokaVisibleOption,
    *,
    timeout_ms: int = VISIBLE_OPTION_CLICK_TIMEOUT_MS,
) -> None:
    click_timeout_ms = max(1, min(timeout_ms, VISIBLE_OPTION_CLICK_TIMEOUT_MS))
    scroll = getattr(option.locator, "scroll_into_view_if_needed", None)
    if scroll is not None:
        try:
            scroll(timeout=click_timeout_ms)
        except Exception:
            pass

    evaluate = getattr(option.locator, "evaluate", None)
    if evaluate is not None:
        try:
            evaluate(TRAVELOKA_OPTION_ACTIVATION_SCRIPT, timeout=click_timeout_ms)
        except TypeError:
            evaluate(TRAVELOKA_OPTION_ACTIVATION_SCRIPT)
        return

    option.locator.click(timeout=click_timeout_ms)  # type: ignore[attr-defined]


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
    return _wait_for_conservative_capture_result(
        state,
        page,
        deadline,
        poll_interval_seconds=poll_interval_seconds,
    )


def _wait_for_conservative_capture_result(
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

    return _capture_result_after_wait(state)


def _capture_result_after_wait(state: _CaptureState) -> TravelokaCaptureResult:
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


def _wait_for_return_selection_transition(
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
    before_marker_texts: Iterable[str] = (),
    before_body_text: str = "",
) -> bool:
    before_marker_keys = {
        _return_selection_marker_key(text)
        for text in before_marker_texts
        if _return_selection_marker_text(text)
    }
    transition_deadline = min(
        deadline,
        monotonic() + (SELECTION_TRANSITION_TIMEOUT_MS / 1000),
    )
    while monotonic() < transition_deadline:
        if _return_selection_transitioned(
            page,
            before_marker_keys=before_marker_keys,
            before_body_text=before_body_text,
            deadline=transition_deadline,
        ):
            return True
        remaining_ms = _remaining_timeout_ms(
            transition_deadline,
            raise_on_expired=False,
        )
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        if wait_ms <= 0:
            break
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]
    return _return_selection_transitioned(
        page,
        before_marker_keys=before_marker_keys,
        before_body_text=before_body_text,
        deadline=transition_deadline,
    )


def _wait_for_final_total(
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
    before_texts: Iterable[str] = (),
) -> tuple[Decimal, str] | None:
    selector_cache = _FinalTotalSelectorCache()
    while monotonic() < deadline:
        remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            break
        final_total = _read_final_total(
            page,
            timeout_ms=min(FINAL_TOTAL_READ_TIMEOUT_MS, remaining_ms),
            deadline=deadline,
            before_texts=before_texts,
            selector_cache=selector_cache,
        )
        if final_total is not None:
            return final_total
        remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        if wait_ms <= 0:
            break
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]
    return None


def _return_selection_marker_texts(
    page: object,
    *,
    deadline: float | None = None,
) -> tuple[str, ...]:
    marker_texts: list[str] = []
    for selector in (
        "[data-testid='flight-summary-container-1_selected']",
        "[data-testid='bundle-summary-tray']",
        "[data-testid='flight-summary-tray-routes-v2']",
    ):
        for text in _locator_texts(
            page,
            selector,
            timeout_ms=250,
            deadline=deadline,
        ):
            if _return_selection_marker_text(text):
                marker_texts.append(text)
    return tuple(marker_texts)


def _return_selection_transitioned(
    page: object,
    *,
    before_marker_keys: set[str] | None = None,
    before_body_text: str = "",
    deadline: float | None = None,
) -> bool:
    before_marker_keys = before_marker_keys or set()
    for text in _return_selection_marker_texts(page, deadline=deadline):
        if _return_selection_marker_key(text) not in before_marker_keys:
            return True
    body_text = _read_body_text(
        page,
        timeout_ms=250,
        deadline=deadline,
    )
    return (
        "Change return flight" not in before_body_text
        and "Change return flight" in body_text
    )


def _return_selection_marker_text(text: str) -> bool:
    return "Return" in text or "Change return flight" in text


def _return_selection_marker_key(text: str) -> str:
    return _normalized_text_key(text)


def _normalized_text_key(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _wait_for_outbound_selection_transition(
    state: _CaptureState,
    page: object,
    selected_key: str | None,
    deadline: float,
    *,
    outbound_payload: dict[str, object],
    before_url: str,
    before_body_text: str,
    poll_interval_seconds: float,
) -> bool:
    transition_deadline = min(
        deadline,
        monotonic() + (SELECTION_TRANSITION_TIMEOUT_MS / 1000),
    )
    while monotonic() < transition_deadline:
        if _capture_looks_like_new_inventory(
            state.best_result,
            outbound_payload,
        ):
            return True
        if _outbound_selection_transitioned(
            page,
            selected_key,
            before_url=before_url,
            before_body_text=before_body_text,
            deadline=transition_deadline,
        ):
            return True
        remaining_ms = _remaining_timeout_ms(
            transition_deadline,
            raise_on_expired=False,
        )
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]
    return _capture_looks_like_new_inventory(
        state.best_result,
        outbound_payload,
    ) or _outbound_selection_transitioned(
        page,
        selected_key,
        before_url=before_url,
        before_body_text=before_body_text,
        deadline=transition_deadline,
    )


def _capture_looks_like_new_inventory(
    capture: TravelokaCaptureResult | None,
    previous_payload: dict[str, object],
) -> bool:
    if capture is None:
        return False
    current_ids = _explicit_payload_item_ids(capture.payload)
    previous_ids = _explicit_payload_item_ids(previous_payload)
    return bool(current_ids and previous_ids and not current_ids.issubset(previous_ids))


def _outbound_selection_transitioned(
    page: object,
    selected_key: str | None,
    *,
    before_url: str,
    before_body_text: str,
    deadline: float | None = None,
) -> bool:
    page_url = str(getattr(page, "url", ""))
    if (
        page_url != before_url
        and not _selected_url_fragment(before_url, selected_key)
        and _selected_url_fragment(page_url, selected_key)
    ):
        return True
    body_text = _read_body_text(
        page,
        timeout_ms=250,
        deadline=deadline,
    )
    return (
        "Change departure flight" not in before_body_text
        and "Change departure flight" in body_text
    )


def _selected_url_fragment(url: str, selected_key: str | None) -> bool:
    if not selected_key:
        return False
    return urlparse(url).fragment == f"SC{selected_key}"


def _read_body_text(
    page: object,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> str:
    if deadline is None:
        text_timeout_ms: int | None = max(1, timeout_ms)
    else:
        remaining_ms = _remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            text_timeout_ms = None
        else:
            text_timeout_ms = max(1, min(timeout_ms, remaining_ms))
    if text_timeout_ms is None:
        return ""
    try:
        return page.locator("body").inner_text(timeout=text_timeout_ms)  # type: ignore[attr-defined]
    except Exception:
        return ""


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
