"""Inventory-card discovery helpers for the Traveloka research provider."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
import re

from cheapy.providers.traveloka.browser_helpers import dom_operation_timeout_ms
from cheapy.providers.traveloka.capture import explicit_payload_item_ids


INVENTORY_CARD_TEST_ID_PREFIX = "flight-inventory-card-container-"
INVENTORY_CARD_SELECTOR = (
    f"[data-testid^='{INVENTORY_CARD_TEST_ID_PREFIX}']"
)
INVENTORY_CARD_BUTTON_SELECTOR = (
    "[data-testid='flight-inventory-card-button'], "
    "[role='button']:has-text('Choose'), "
    "[role='button']:has-text('Ch\u1ecdn')"
)
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


def cheapest_visible_option(
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


def parse_visible_price(text: str) -> tuple[Decimal, str]:
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


def bind_visible_option_to_payload(
    option: TravelokaVisibleOption,
    payload: dict[str, object],
) -> str | None:
    if (
        option.key is not None
        and option.key in explicit_payload_item_ids(payload)
    ):
        return option.key
    return None


def visible_options_from_page(
    page: object,
    *,
    timeout_ms: int = 10_000,
    deadline: float | None = None,
) -> list[TravelokaVisibleOption]:
    timeout_ms = max(1, timeout_ms)
    return _visible_options_from_inventory_cards(
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
            text_timeout_ms = dom_operation_timeout_ms(
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
        amount, currency = parse_visible_price(price_line)
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
        attribute_timeout_ms = dom_operation_timeout_ms(
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
