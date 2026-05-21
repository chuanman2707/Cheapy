"""Traveloka fare response capture helpers."""

from __future__ import annotations

from collections.abc import Mapping
from time import monotonic
from urllib.parse import urlparse

from cheapy.providers.traveloka.browser_helpers import remaining_timeout_ms
from cheapy.providers.traveloka.errors import (
    blocked_error,
    invalid_json_error,
    rate_limited_error,
    timeout_error,
    transport_error,
    unsupported_response_error,
)
from cheapy.providers.traveloka.results import TravelokaCaptureResult


INITIAL_SEARCH_PATH = "/api/v2/flight/search/initial"
POLL_SEARCH_PATH = "/api/v2/flight/search/poll"
SUPPORTED_FARE_PATHS = {INITIAL_SEARCH_PATH, POLL_SEARCH_PATH}


class CaptureState:
    def __init__(self) -> None:
        self.best_result: TravelokaCaptureResult | None = None
        self.completed = False

    def reset(self) -> None:
        self.best_result = None
        self.completed = False

    def handle_response(self, response: object) -> None:
        response_url = str(getattr(response, "url", ""))
        if not is_traveloka_first_party_url(response_url):
            return

        path = urlparse(response_url).path
        if path not in SUPPORTED_FARE_PATHS:
            return

        status = int(getattr(response, "status", 0))
        if status in {401, 403}:
            raise blocked_error(status)
        if status == 429:
            raise rate_limited_error(status)
        if status >= 500:
            raise transport_error(status)

        payload: object
        try:
            payload = response.json()  # type: ignore[attr-defined]
        except Exception as exc:
            raise invalid_json_error(type(exc).__name__) from None

        if not isinstance(payload, dict) or not _is_supported_fare_payload(payload):
            raise unsupported_response_error()

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


def _is_supported_fare_payload(payload: dict[str, object]) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    return isinstance(data.get("searchResults"), list)


def is_traveloka_first_party_url(url: str) -> bool:
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


def explicit_payload_item_ids(payload: object) -> set[str]:
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


def wait_for_capture(
    state: CaptureState,
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
    state: CaptureState,
    page: object,
    deadline: float,
    *,
    poll_interval_seconds: float,
) -> TravelokaCaptureResult:
    while not state.completed and monotonic() < deadline:
        remaining_ms = remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            break
        wait_ms = min(round(poll_interval_seconds * 1000), remaining_ms)
        page.wait_for_timeout(wait_ms)  # type: ignore[attr-defined]

    return _capture_result_after_wait(state)


def _capture_result_after_wait(state: CaptureState) -> TravelokaCaptureResult:
    if state.best_result is None:
        raise timeout_error()
    if state.completed:
        return state.best_result
    return TravelokaCaptureResult(
        payload=state.best_result.payload,
        source_path=state.best_result.source_path,
        search_completed=state.best_result.search_completed,
        timed_out=True,
        partial_failure_type=state.best_result.partial_failure_type,
    )
