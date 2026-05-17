"""Final selected total parsing helpers for the Traveloka research provider."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
import re

from cheapy.providers.traveloka.browser_helpers import locator_texts, remaining_timeout_ms
from cheapy.providers.traveloka.inventory import parse_visible_price


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


def _parse_explicit_price(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[Decimal, str] | None:
    normalized = " ".join(text.replace("\xa0", " ").split())
    match = pattern.search(normalized)
    if match is None:
        return None
    try:
        return parse_visible_price(match.group(1))
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
            prices.append(parse_visible_price(match.group(1)))
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
        return parse_visible_price(normalized)
    except Exception:
        return None


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


def read_final_total(
    page: object,
    *,
    timeout_ms: int = 1000,
    deadline: float | None = None,
    before_texts: Iterable[str] = (),
    selector_cache: _FinalTotalSelectorCache | None = None,
) -> tuple[Decimal, str] | None:
    timeout_ms = max(1, timeout_ms)
    before_text_keys = {normalized_text_key(text) for text in before_texts}
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
            if normalized_text_key(text) in before_text_keys:
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
        if normalized_text_key(text) not in before_text_keys
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
        if normalized_text_key(text) in before_text_keys:
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


def final_total_texts(
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


def wait_for_final_total(
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
        final_total = read_final_total(
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


def normalized_text_key(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())
