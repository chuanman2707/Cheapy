"""Selection transition helpers for the Traveloka research provider."""

from __future__ import annotations

from collections.abc import Iterable
from time import monotonic
from urllib.parse import urlparse

from cheapy.providers.traveloka.browser_helpers import (
    locator_texts,
    read_body_text,
    remaining_timeout_ms,
)
from cheapy.providers.traveloka.capture import explicit_payload_item_ids
from cheapy.providers.traveloka.results import TravelokaCaptureResult
from cheapy.providers.traveloka.totals import normalized_text_key


SELECTION_TRANSITION_TIMEOUT_MS = 10_000


def wait_for_return_selection_transition(
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
        if return_selection_transitioned(
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
    return return_selection_transitioned(
        page,
        before_marker_keys=before_marker_keys,
        before_body_text=before_body_text,
        deadline=transition_deadline,
    )


def return_selection_marker_texts(
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


def return_selection_transitioned(
    page: object,
    *,
    before_marker_keys: set[str] | None = None,
    before_body_text: str = "",
    deadline: float | None = None,
) -> bool:
    before_marker_keys = before_marker_keys or set()
    for text in return_selection_marker_texts(page, deadline=deadline):
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
    return normalized_text_key(text)


def wait_for_outbound_selection_transition(
    state: object,
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
            getattr(state, "best_result", None),
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
        getattr(state, "best_result", None),
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
    current_ids = explicit_payload_item_ids(capture.payload)
    previous_ids = explicit_payload_item_ids(previous_payload)
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
