from __future__ import annotations

import pytest

from cheapy.browser_bootstrap.types import (
    BrowserNetworkCapture,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
)
from cheapy.providers.traveloka import replay
from cheapy.providers.traveloka.errors import TravelokaProviderError


def _payload(item_id: str = "capture") -> dict[str, object]:
    return {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": item_id}],
        }
    }


def _exchange(
    *,
    sequence: int,
    path: str = "/api/v2/flight/search/poll",
    request_url: str | None = None,
    response_payload: object | None = None,
    post_data: str | None = '{"searchId":"secret"}',
    headers: dict[str, str] | None = None,
) -> CapturedExchange:
    url = request_url or f"https://www.traveloka.com{path}?secret={sequence}"
    request = CapturedRequest(
        url=url,
        method="POST",
        sequence=sequence,
        headers=headers
        if headers is not None
        else {
            "accept": "application/json",
            "accept-language": "en-US",
            "content-type": "application/json",
            "origin": "https://www.traveloka.com",
            "referer": "https://www.traveloka.com/en-vn/flight/fullsearch",
            "cookie": "secret-cookie",
            "authorization": "Bearer secret",
            "sec-ch-ua": "fingerprint",
            "proxy-authorization": "proxy-secret",
            "x-forwarded-for": "127.0.0.1",
            "x-bad": "bad\r\nvalue",
        },
        post_data=post_data,
    )
    response = CapturedResponse(
        url=url,
        status_code=200,
        payload=response_payload if response_payload is not None else _payload(),
        sequence=sequence,
    )
    return CapturedExchange(
        sequence=sequence,
        captured_monotonic=100.0 + sequence,
        request=request,
        response=response,
    )


def _capture(*exchanges: CapturedExchange) -> BrowserNetworkCapture:
    if not exchanges:
        exchanges = (_exchange(sequence=1),)
    return BrowserNetworkCapture(
        cookie_header="datadome=secret-cookie",
        user_agent="Mozilla/5.0 secret",
        exchanges=tuple(exchanges),
        created_monotonic=100.0,
    )


class FakeReplayClient:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[replay.TravelokaReplayRequest] = []

    def post(
        self,
        request: replay.TravelokaReplayRequest,
        *,
        timeout: float,
    ) -> replay.TravelokaReplayResponse:
        self.calls.append(request)
        return replay.TravelokaReplayResponse(
            status_code=self.status_code,
            payload=self.payload,
        )


def test_select_replay_request_prefers_latest_poll_and_redacts_repr() -> None:
    selected = replay.select_replay_request(
        _capture(
            _exchange(
                sequence=4,
                path="/api/v2/flight/search/initial",
                response_payload=_payload("initial"),
            ),
            _exchange(sequence=2, response_payload=_payload("older-poll")),
            _exchange(sequence=5, response_payload=_payload("newer-poll")),
        )
    )

    assert selected.method == "POST"
    assert selected.path_and_query == "/api/v2/flight/search/poll?secret=5"
    assert selected.headers == {
        "accept": "application/json",
        "accept-language": "en-US",
        "content-type": "application/json",
        "origin": "https://www.traveloka.com",
        "referer": "https://www.traveloka.com/en-vn/flight/fullsearch",
    }
    assert selected.body == '{"searchId":"secret"}'
    assert selected.cookie_header == "datadome=secret-cookie"
    assert selected.user_agent == "Mozilla/5.0 secret"
    assert "secret" not in repr(selected)
    assert "Mozilla/5.0 secret" not in repr(selected)


def test_select_replay_request_rejects_non_exact_traveloka_url() -> None:
    with pytest.raises(TravelokaProviderError) as exc_info:
        replay.select_replay_request(
            _capture(
                _exchange(
                    sequence=1,
                    request_url="https://evil.traveloka.com/api/v2/flight/search/poll",
                )
            )
        )

    assert exc_info.value.failure_type == "network_capture_unavailable"
    assert "evil.traveloka.com" not in str(exc_info.value)


def test_select_replay_request_validates_selected_poll_instead_of_earlier_initial() -> None:
    with pytest.raises(TravelokaProviderError) as exc_info:
        replay.select_replay_request(
            _capture(
                _exchange(
                    sequence=10,
                    path="/api/v2/flight/search/initial",
                ),
                _exchange(
                    sequence=1,
                    request_url="https://www.traveloka.com./api/v2/flight/search/poll",
                ),
            )
        )

    assert exc_info.value.failure_type == "network_capture_unavailable"
    assert "traveloka.com" not in str(exc_info.value)


def test_select_replay_request_drops_unsafe_referer_and_crlf_values() -> None:
    selected = replay.select_replay_request(
        _capture(
            _exchange(
                sequence=1,
                headers={
                    "accept": "application/json\nbad",
                    "accept-language": "en-US\r\n",
                    "content-type": "application/json",
                    "origin": "\nhttps://www.traveloka.com",
                    "referer": "https://evil.example/fullsearch",
                },
            )
        )
    )

    assert selected.headers == {"content-type": "application/json"}


def test_select_replay_request_drops_unsafe_raw_header_names() -> None:
    selected = replay.select_replay_request(
        _capture(
            _exchange(
                sequence=1,
                headers={
                    "accept\n": "application/json",
                    "bad header": "x",
                    "content-type": "application/json",
                },
            )
        )
    )

    assert selected.headers == {"content-type": "application/json"}


def test_replay_success_returns_replay_payload() -> None:
    payload = _payload("replay")
    client = FakeReplayClient(payload)

    result = replay.replay_or_fallback(
        _capture(_exchange(sequence=1, response_payload=_payload("capture"))),
        client=client,
        timeout_seconds=3.0,
    )

    assert result.payload == payload
    assert result.source == "replay"
    assert len(client.calls) == 1


def test_replay_safe_failure_falls_back_to_same_exchange_payload() -> None:
    capture_payload = _payload("selected-capture")
    client = FakeReplayClient({"error": "blocked"}, status_code=403)

    result = replay.replay_or_fallback(
        _capture(
            _exchange(
                sequence=9,
                path="/api/v2/flight/search/initial",
                response_payload=_payload("wrong-exchange"),
            ),
            _exchange(sequence=2, response_payload=_payload("older-poll")),
            _exchange(sequence=10, response_payload=capture_payload),
        ),
        client=client,
        timeout_seconds=3.0,
    )

    assert result.payload == capture_payload
    assert result.source == "browser_capture"


def test_replay_and_capture_failure_raises_safe_error() -> None:
    client = FakeReplayClient({"error": "blocked"}, status_code=403)
    capture = _capture(
        _exchange(
            sequence=1,
            response_payload={"data": {"calendarPrices": []}},
        )
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        replay.replay_or_fallback(capture, client=client, timeout_seconds=3.0)

    assert exc_info.value.failure_type == "blocked"
    rendered = str(exc_info.value)
    assert "secret" not in rendered
    assert "datadome" not in rendered
    assert "traveloka.com/api" not in rendered


@pytest.mark.parametrize(
    ("status_code", "failure_type"),
    [
        (401, "blocked"),
        (403, "blocked"),
        (429, "rate_limited"),
        (500, "transport_error"),
    ],
)
def test_replay_status_failures_are_sanitized(
    status_code: int,
    failure_type: str,
) -> None:
    client = FakeReplayClient({"token": "secret"}, status_code=status_code)
    capture = _capture(_exchange(sequence=1, response_payload={"data": {}}))

    with pytest.raises(TravelokaProviderError) as exc_info:
        replay.replay_or_fallback(capture, client=client, timeout_seconds=3.0)

    assert exc_info.value.failure_type == failure_type
    assert "secret" not in str(exc_info.value)


def test_replay_non_dict_payload_maps_to_invalid_json() -> None:
    client = FakeReplayClient(["secret"], status_code=200)
    capture = _capture(_exchange(sequence=1, response_payload={"data": {}}))

    with pytest.raises(TravelokaProviderError) as exc_info:
        replay.replay_or_fallback(capture, client=client, timeout_seconds=3.0)

    assert exc_info.value.failure_type == "invalid_json"
    assert "secret" not in str(exc_info.value)
