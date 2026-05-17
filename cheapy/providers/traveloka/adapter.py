"""Browser capture adapter for the Traveloka research provider."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from typing import Callable
from urllib.parse import urlparse

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import urls as traveloka_urls
from cheapy.providers.traveloka.browser_helpers import (
    close_quietly,
    locator_texts,
    read_body_text,
    remaining_timeout_ms,
)
from cheapy.providers.traveloka.errors import (
    TravelokaProviderError,
    browser_unavailable_error,
    is_timeout_exception,
    navigation_failed_error,
    raise_blocked_if_terminal_page,
    timeout_error,
)
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
    partial_round_trip_result,
)
from cheapy.providers.traveloka.timing import (
    TravelokaPhaseRecorder,
    TravelokaPhaseTiming,
)


DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_CURRENCY = "USD"
DEFAULT_LOCALE = "en-en"
SELECTION_TRANSITION_TIMEOUT_MS = 10_000
FINAL_TOTAL_READ_TIMEOUT_MS = 250
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
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
BrowserLauncher = Callable[..., object]


class TravelokaAdapter:
    """Sync browser adapter around Traveloka flight search capture surfaces."""

    configured_currency = DEFAULT_CURRENCY

    def __init__(
        self,
        *,
        base_url: str = traveloka_urls.DEFAULT_BASE_URL,
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
        state = traveloka_capture.CaptureState()
        deadline = monotonic() + self._timeout_seconds
        try:
            try:
                with self._phase_recorder.phase("browser_launch"):
                    browser = self._launch_browser(
                        headless=True,
                        timeout=remaining_timeout_ms(deadline),
                    )
            except Exception as exc:
                if is_timeout_exception(exc):
                    raise timeout_error(type(exc).__name__) from None
                raise browser_unavailable_error(type(exc).__name__) from None

            with self._phase_recorder.phase("context_page_setup"):
                remaining_timeout_ms(deadline)
                context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page = context.new_page()  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page.on("response", state.handle_response)  # type: ignore[attr-defined]
            with self._phase_recorder.phase("initial_navigation"):
                remaining_timeout_ms(deadline)
                page.goto(  # type: ignore[attr-defined]
                    traveloka_urls.build_full_search_url(
                        request,
                        base_url=self._base_url,
                    ),
                    wait_until="domcontentloaded",
                    timeout=remaining_timeout_ms(deadline),
                )

            try:
                with self._phase_recorder.phase("outbound_capture_wait"):
                    return traveloka_capture.wait_for_capture(
                        state,
                        page,
                        deadline,
                        poll_interval_seconds=self._poll_interval_seconds,
                    )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    raise_blocked_if_terminal_page(page.content())  # type: ignore[attr-defined]
                raise
        except TravelokaProviderError:
            raise
        except Exception as exc:
            if is_timeout_exception(exc):
                raise timeout_error(type(exc).__name__) from None
            raise navigation_failed_error(type(exc).__name__) from None
        finally:
            with self._phase_recorder.phase("cleanup"):
                close_quietly(context)
                close_quietly(browser)

    def _search_selected_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        browser: object | None = None
        context: object | None = None
        state = traveloka_capture.CaptureState()
        deadline = monotonic() + self._timeout_seconds
        try:
            try:
                with self._phase_recorder.phase("browser_launch"):
                    browser = self._launch_browser(
                        headless=True,
                        timeout=remaining_timeout_ms(deadline),
                    )
            except Exception as exc:
                if is_timeout_exception(exc):
                    raise timeout_error(type(exc).__name__) from None
                raise browser_unavailable_error(type(exc).__name__) from None

            with self._phase_recorder.phase("context_page_setup"):
                remaining_timeout_ms(deadline)
                context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page = context.new_page()  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page.on("response", state.handle_response)  # type: ignore[attr-defined]
            with self._phase_recorder.phase("initial_navigation"):
                remaining_timeout_ms(deadline)
                page.goto(  # type: ignore[attr-defined]
                    traveloka_urls.build_full_search_url(
                        request,
                        base_url=self._base_url,
                    ),
                    wait_until="domcontentloaded",
                    timeout=remaining_timeout_ms(deadline),
                )

            try:
                with self._phase_recorder.phase("outbound_capture_wait"):
                    outbound_capture = traveloka_capture.wait_for_capture(
                        state,
                        page,
                        deadline,
                        poll_interval_seconds=self._poll_interval_seconds,
                    )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    raise_blocked_if_terminal_page(page.content())  # type: ignore[attr-defined]
                raise
            if outbound_capture.timed_out:
                return outbound_capture

            outbound_selection_timeout_ms = remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if outbound_selection_timeout_ms <= 0:
                return partial_round_trip_result(
                    outbound_capture,
                    "outbound_selection_unavailable",
                )
            with self._phase_recorder.phase("outbound_visible_option_discovery"):
                outbound_option = traveloka_inventory.cheapest_visible_option(
                    traveloka_inventory.visible_options_from_page(
                        page,
                        deadline=deadline,
                    )
                )
                if outbound_option is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "outbound_selection_unavailable",
                    )
            with self._phase_recorder.phase("outbound_binding"):
                outbound_key = traveloka_inventory.bind_visible_option_to_payload(
                    outbound_option,
                    outbound_capture.payload,
                )
                if outbound_key is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "selected_outbound_binding_unavailable",
                    )

            with self._phase_recorder.phase("outbound_click_transition"):
                before_outbound_selection_url = str(getattr(page, "url", ""))
                before_outbound_selection_body = read_body_text(
                    page,
                    timeout_ms=250,
                    deadline=deadline,
                )
                state.reset()
                traveloka_activation.click_visible_option(
                    outbound_option,
                    timeout_ms=remaining_timeout_ms(deadline),
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
                    return partial_round_trip_result(
                        outbound_capture,
                        "outbound_selection_transition_unavailable",
                    )
            try:
                with self._phase_recorder.phase("return_capture_wait"):
                    return_capture = traveloka_capture.wait_for_capture(
                        state,
                        page,
                        deadline,
                        poll_interval_seconds=self._poll_interval_seconds,
                    )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    return partial_round_trip_result(
                        outbound_capture,
                        "return_capture_timeout",
                    )
                raise
            if return_capture.timed_out:
                return partial_round_trip_result(
                    outbound_capture,
                    "return_capture_timeout",
                )

            return_selection_timeout_ms = remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if return_selection_timeout_ms <= 0:
                return partial_round_trip_result(
                    outbound_capture,
                    "return_selection_unavailable",
                )
            with self._phase_recorder.phase("return_visible_option_discovery"):
                return_option = traveloka_inventory.cheapest_visible_option(
                    traveloka_inventory.visible_options_from_page(
                        page,
                        deadline=deadline,
                    )
                )
                if return_option is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "return_selection_unavailable",
                    )
            with self._phase_recorder.phase("return_binding"):
                return_key = traveloka_inventory.bind_visible_option_to_payload(
                    return_option,
                    return_capture.payload,
                )
                if return_key is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "selected_return_binding_unavailable",
                    )

            with self._phase_recorder.phase("return_click_transition"):
                return_click_timeout_ms = remaining_timeout_ms(
                    deadline,
                    raise_on_expired=False,
                )
                if return_click_timeout_ms <= 0:
                    return partial_round_trip_result(
                        outbound_capture,
                        "final_round_trip_total_unavailable",
                    )
                before_final_total_texts = _final_total_texts(page, deadline=deadline)
                before_return_selection_marker_texts = _return_selection_marker_texts(
                    page,
                    deadline=deadline,
                )
                before_return_selection_body = read_body_text(
                    page,
                    timeout_ms=250,
                    deadline=deadline,
                )
                traveloka_activation.click_visible_option(
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
                    return partial_round_trip_result(
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
                return partial_round_trip_result(
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
            if is_timeout_exception(exc):
                raise timeout_error(type(exc).__name__) from None
            raise navigation_failed_error(type(exc).__name__) from None
        finally:
            with self._phase_recorder.phase("cleanup"):
                close_quietly(context)
                close_quietly(browser)


def _parse_explicit_price(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[Decimal, str] | None:
    normalized = " ".join(text.replace("\xa0", " ").split())
    match = pattern.search(normalized)
    if match is None:
        return None
    try:
        return traveloka_inventory.parse_visible_price(match.group(1))
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
            prices.append(traveloka_inventory.parse_visible_price(match.group(1)))
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
        return traveloka_inventory.parse_visible_price(normalized)
    except Exception:
        return None


def _parse_summary_total_price(text: str) -> tuple[Decimal, str] | None:
    round_trip_price = _parse_explicit_price(text, _EXPLICIT_ROUND_TRIP_PRICE_RE)
    if round_trip_price is not None:
        return round_trip_price
    return _parse_explicit_price(text, _EXPLICIT_SUMMARY_PRICE_RE)


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
        for text in locator_texts(
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
        for text in locator_texts(
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
    for text in locator_texts(
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
        texts.extend(locator_texts(page, selector, timeout_ms=250, deadline=deadline))
    for selector in _FINAL_TOTAL_SUMMARY_SELECTORS:
        texts.extend(locator_texts(page, selector, timeout_ms=250, deadline=deadline))
    texts.extend(
        locator_texts(
            page,
            _FINAL_TOTAL_GLOBAL_LABEL_SELECTOR,
            timeout_ms=250,
            deadline=deadline,
        )
    )
    return tuple(texts)


def _default_launch_browser(**kwargs: object) -> object:
    from cloakbrowser import launch

    return launch(**kwargs)


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
        remaining_ms = remaining_timeout_ms(
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
        remaining_ms = remaining_timeout_ms(deadline, raise_on_expired=False)
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
        remaining_ms = remaining_timeout_ms(deadline, raise_on_expired=False)
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
        for text in locator_texts(
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
    body_text = read_body_text(
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
    state: traveloka_capture.CaptureState,
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
        remaining_ms = remaining_timeout_ms(
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
    current_ids = traveloka_capture.explicit_payload_item_ids(capture.payload)
    previous_ids = traveloka_capture.explicit_payload_item_ids(previous_payload)
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
    body_text = read_body_text(
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
