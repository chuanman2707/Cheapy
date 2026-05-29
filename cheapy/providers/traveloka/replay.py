"""HTTP replay helpers for same-call Traveloka browser harvests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from cheapy.browser_bootstrap.types import BrowserNetworkCapture, CapturedExchange
from cheapy.models import ErrorCode
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors


ALLOWED_REPLAY_HEADERS = frozenset(
    {"accept", "accept-language", "content-type", "origin", "referer"}
)
SEARCH_PATHS = frozenset(
    {traveloka_capture.INITIAL_SEARCH_PATH, traveloka_capture.POLL_SEARCH_PATH}
)
TRAVELOKA_REPLAY_HOST = "www.traveloka.com"
HEADER_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


@dataclass(frozen=True, slots=True)
class TravelokaReplayRequest:
    path_and_query: str = field(repr=False)
    method: str
    headers: Mapping[str, str] = field(repr=False)
    body: str = field(repr=False)
    cookie_header: str = field(repr=False)
    user_agent: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class TravelokaReplayResponse:
    status_code: int
    payload: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class TravelokaReplayResult:
    payload: dict[str, object] = field(repr=False)
    source: str
    source_path: str


class TravelokaReplayClient(Protocol):
    def post(
        self,
        request: TravelokaReplayRequest,
        *,
        timeout: float,
    ) -> TravelokaReplayResponse: ...


def select_replay_request(capture: BrowserNetworkCapture) -> TravelokaReplayRequest:
    """Build a sanitized replay request from the best captured exchange."""

    return _request_from_exchange(capture, _select_exchange(capture))


def replay_or_fallback(
    capture: BrowserNetworkCapture,
    *,
    client: TravelokaReplayClient,
    timeout_seconds: float,
) -> TravelokaReplayResult:
    """Replay a harvested request, falling back to its same captured response."""

    exchange = _select_exchange(capture)
    replay_error: traveloka_errors.TravelokaProviderError | None = None

    try:
        request = _request_from_exchange(capture, exchange)
        response = client.post(request, timeout=timeout_seconds)
        replay_payload = _payload_from_replay_response(response)
        return TravelokaReplayResult(
            payload=replay_payload,
            source="replay",
            source_path=urlsplit(exchange.request.url).path,
        )
    except traveloka_errors.TravelokaProviderError as exc:
        replay_error = exc
    except Exception as exc:
        if traveloka_errors.is_timeout_exception(exc):
            replay_error = traveloka_errors.timeout_error(type(exc).__name__)
        else:
            replay_error = traveloka_errors.transport_error()

    fallback_payload = _supported_payload_from_exchange(exchange)
    if fallback_payload is not None:
        return TravelokaReplayResult(
            payload=fallback_payload,
            source="browser_capture",
            source_path=urlsplit(exchange.request.url).path,
        )
    raise replay_error


def _request_from_exchange(
    capture: BrowserNetworkCapture,
    exchange: CapturedExchange,
) -> TravelokaReplayRequest:
    parsed = urlsplit(exchange.request.url)
    if not _is_valid_replay_url(parsed.scheme, parsed.netloc, parsed.path):
        raise _network_capture_unavailable()
    if exchange.request.method.upper() != "POST":
        raise _network_capture_unavailable()
    body = exchange.request.post_data
    if body is None:
        raise _network_capture_unavailable()

    return TravelokaReplayRequest(
        path_and_query=f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path,
        method="POST",
        headers=_safe_replay_headers(exchange.request.headers),
        body=body,
        cookie_header=capture.cookie_header,
        user_agent=capture.user_agent,
    )


def _select_exchange(capture: BrowserNetworkCapture) -> CapturedExchange:
    candidates = [
        exchange for exchange in capture.exchanges if _is_replay_candidate(exchange)
    ]
    if not candidates:
        raise _network_capture_unavailable()
    return max(
        candidates,
        key=lambda exchange: (
            _path_rank(urlsplit(exchange.request.url).path),
            exchange.sequence,
        ),
    )


def _is_replay_candidate(exchange: CapturedExchange) -> bool:
    parsed = urlsplit(exchange.request.url)
    return (
        _is_valid_replay_url(parsed.scheme, parsed.netloc, parsed.path)
        and exchange.request.method.upper() == "POST"
    )


def _is_valid_replay_url(scheme: str, netloc: str, path: str) -> bool:
    return (
        scheme == "https"
        and netloc.lower() == TRAVELOKA_REPLAY_HOST
        and path in SEARCH_PATHS
    )


def _path_rank(path: str) -> int:
    if path == traveloka_capture.POLL_SEARCH_PATH:
        return 2
    return 1


def _safe_replay_headers(headers: object) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        return {}

    safe: dict[str, str] = {}
    for key, value in headers.items():
        raw_name = str(key)
        if not raw_name or any(char not in HEADER_NAME_CHARS for char in raw_name):
            continue
        name = raw_name.lower()
        if name not in ALLOWED_REPLAY_HEADERS:
            continue

        raw_value = str(value)
        if "\r" in raw_value or "\n" in raw_value:
            continue
        text = raw_value.strip()
        if not text:
            continue
        if name == "referer" and not _is_safe_referer(text):
            continue
        safe[name] = text
    return safe


def _is_safe_referer(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == TRAVELOKA_REPLAY_HOST
        and value.startswith(f"https://{TRAVELOKA_REPLAY_HOST}/")
    )


def _payload_from_replay_response(
    response: TravelokaReplayResponse,
) -> dict[str, object]:
    status = response.status_code
    if status in {401, 403}:
        raise traveloka_errors.blocked_error(status)
    if status == 429:
        raise traveloka_errors.rate_limited_error(status)
    if status >= 500:
        raise traveloka_errors.transport_error(status)
    if status != 200:
        raise traveloka_errors.unsupported_response_error()

    payload = response.payload
    if not isinstance(payload, dict):
        raise traveloka_errors.invalid_json_error("InvalidReplayPayload")
    if not _is_supported_payload(payload):
        raise traveloka_errors.unsupported_response_error()
    return payload


def _supported_payload_from_exchange(
    exchange: CapturedExchange,
) -> dict[str, object] | None:
    response = exchange.response
    if response is None or response.status_code != 200:
        return None
    payload = response.payload
    if not isinstance(payload, dict) or not _is_supported_payload(payload):
        return None
    return payload


def _is_supported_payload(payload: dict[str, object]) -> bool:
    data = payload.get("data")
    return isinstance(data, dict) and isinstance(data.get("searchResults"), list)


def _network_capture_unavailable() -> traveloka_errors.TravelokaProviderError:
    return traveloka_errors.TravelokaProviderError(
        failure_type="network_capture_unavailable",
        message_en="Traveloka browser capture did not include a replayable request.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
    )
