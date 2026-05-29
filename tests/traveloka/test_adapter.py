from __future__ import annotations

from pathlib import Path

from cheapy.browser_bootstrap.types import (
    BrowserNetworkCapture,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
)
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import adapter as traveloka_adapter
from cheapy.providers.traveloka import replay as traveloka_replay
from cheapy.providers.traveloka import workflow as traveloka_workflow
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.results import TravelokaCaptureResult


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def _capture() -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )


def _payload(item_id: str) -> dict[str, object]:
    return {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": item_id}],
        }
    }


def _network_capture(payload: dict[str, object]) -> BrowserNetworkCapture:
    url = "https://www.traveloka.com/api/v2/flight/search/poll"
    request = CapturedRequest(
        url=url,
        method="POST",
        sequence=1,
        headers={"content-type": "application/json"},
        post_data='{"searchId":"secret"}',
    )
    response = CapturedResponse(url=url, status_code=200, payload=payload, sequence=1)
    return BrowserNetworkCapture(
        cookie_header="datadome=secret-cookie",
        user_agent="Mozilla/5.0 secret",
        exchanges=(
            CapturedExchange(
                sequence=1,
                captured_monotonic=100.0,
                request=request,
                response=response,
            ),
        ),
        created_monotonic=100.0,
    )


class ReplayClient:
    def __init__(self, response: traveloka_replay.TravelokaReplayResponse) -> None:
        self.response = response
        self.calls: list[traveloka_replay.TravelokaReplayRequest] = []
        self.timeouts: list[float] = []

    def post(
        self,
        request: traveloka_replay.TravelokaReplayRequest,
        *,
        timeout: float,
    ) -> traveloka_replay.TravelokaReplayResponse:
        self.calls.append(request)
        self.timeouts.append(timeout)
        return self.response


def test_adapter_delegates_one_way_to_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_search_exact_one_way(
        request: object,
        **kwargs: object,
    ) -> TravelokaCaptureResult:
        seen["request"] = request
        seen["kwargs"] = kwargs
        return _capture()

    monkeypatch.setattr(
        traveloka_workflow,
        "search_exact_one_way",
        fake_search_exact_one_way,
    )

    adapter = TravelokaAdapter(
        base_url="https://example.test",
        timeout_seconds=7,
        poll_interval_seconds=0.5,
        launch_browser=lambda **kwargs: object(),
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.search_completed is True
    assert seen["request"] == _one_way_request()
    assert seen["kwargs"]["base_url"] == "https://example.test"
    assert seen["kwargs"]["timeout_seconds"] == 7
    assert seen["kwargs"]["poll_interval_seconds"] == 0.5


def test_adapter_prefers_replay_payload_from_harvest() -> None:
    capture_payload = _payload("capture")
    replay_payload = _payload("replay")
    replay_client = ReplayClient(
        traveloka_replay.TravelokaReplayResponse(200, replay_payload)
    )
    capture_calls: list[dict[str, object]] = []

    def fake_capture_network(**kwargs: object) -> BrowserNetworkCapture:
        capture_calls.append(kwargs)
        return _network_capture(capture_payload)

    adapter = TravelokaAdapter(
        capture_network=fake_capture_network,
        replay_client=replay_client,
        timeout_seconds=5.0,
        launch_browser=lambda **kwargs: object(),
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == replay_payload
    assert result.source_path == "/api/v2/flight/search/poll"
    assert result.search_completed is True
    assert len(replay_client.calls) == 1
    assert capture_calls[0]["launch_browser"] is adapter._launch_browser
    assert str(capture_calls[0]["page_url"]).startswith(
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
    )


def test_adapter_falls_back_to_browser_capture_when_replay_is_blocked() -> None:
    capture_payload = _payload("capture")
    replay_client = ReplayClient(
        traveloka_replay.TravelokaReplayResponse(403, {"error": "blocked"})
    )
    adapter = TravelokaAdapter(
        capture_network=lambda **kwargs: _network_capture(capture_payload),
        replay_client=replay_client,
        timeout_seconds=5.0,
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == capture_payload
    assert result.source_path == "/api/v2/flight/search/poll"


def test_adapter_replay_timeout_uses_remaining_attempt_budget(monkeypatch) -> None:
    replay_payload = _payload("replay")
    replay_client = ReplayClient(
        traveloka_replay.TravelokaReplayResponse(200, replay_payload)
    )
    times = iter([100.0, 101.25])
    monkeypatch.setattr(traveloka_adapter, "monotonic", lambda: next(times))

    adapter = TravelokaAdapter(
        capture_network=lambda **kwargs: _network_capture(_payload("capture")),
        replay_client=replay_client,
        timeout_seconds=5.0,
    )

    adapter.search_exact_one_way(_one_way_request())

    assert replay_client.timeouts == [3.75]


def test_adapter_delegates_round_trip_to_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_search_selected_round_trip(
        request: object,
        **kwargs: object,
    ) -> TravelokaCaptureResult:
        seen["request"] = request
        seen["kwargs"] = kwargs
        return _capture()

    monkeypatch.setattr(
        traveloka_workflow,
        "search_selected_round_trip",
        fake_search_selected_round_trip,
    )

    adapter = TravelokaAdapter(timeout_seconds=7, launch_browser=lambda **kwargs: object())

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert result.search_completed is True
    assert seen["request"] == _round_trip_request()
    assert seen["kwargs"]["timeout_seconds"] == 7


def test_round_trip_with_replay_dependencies_keeps_browser_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_search_selected_round_trip(
        request: object,
        **kwargs: object,
    ) -> TravelokaCaptureResult:
        seen["request"] = request
        seen["kwargs"] = kwargs
        return _capture()

    monkeypatch.setattr(
        traveloka_workflow,
        "search_selected_round_trip",
        fake_search_selected_round_trip,
    )

    adapter = TravelokaAdapter(
        capture_network=lambda **kwargs: _network_capture(_payload("capture")),
        replay_client=ReplayClient(
            traveloka_replay.TravelokaReplayResponse(200, _payload("replay"))
        ),
        timeout_seconds=7,
        launch_browser=lambda **kwargs: object(),
    )

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert result.search_completed is True
    assert seen["request"] == _round_trip_request()


def test_traveloka_search_predicates_match_only_first_party_post_search_paths() -> None:
    poll_request = CapturedRequest(
        url="https://www.traveloka.com/api/v2/flight/search/poll",
        method="POST",
        sequence=1,
    )
    initial_request = CapturedRequest(
        url="https://traveloka.com/api/v2/flight/search/initial",
        method="POST",
        sequence=2,
    )
    get_request = CapturedRequest(
        url="https://www.traveloka.com/api/v2/flight/search/poll",
        method="GET",
        sequence=3,
    )
    non_first_party_request = CapturedRequest(
        url="https://evil.example/api/v2/flight/search/poll",
        method="POST",
        sequence=4,
    )
    other_path_request = CapturedRequest(
        url="https://www.traveloka.com/api/profile",
        method="POST",
        sequence=5,
    )

    assert traveloka_adapter._is_traveloka_search_request(poll_request) is True
    assert traveloka_adapter._is_traveloka_search_request(initial_request) is True
    assert traveloka_adapter._is_traveloka_search_request(get_request) is False
    assert (
        traveloka_adapter._is_traveloka_search_request(non_first_party_request)
        is False
    )
    assert traveloka_adapter._is_traveloka_search_request(other_path_request) is False
    assert (
        traveloka_adapter._is_traveloka_search_response(
            CapturedResponse(
                url=poll_request.url,
                status_code=200,
                payload={},
                sequence=1,
            )
        )
        is True
    )
    assert (
        traveloka_adapter._is_traveloka_search_response(
            CapturedResponse(
                url=poll_request.url,
                status_code=500,
                payload={},
                sequence=1,
            )
        )
        is False
    )


def test_adapter_phase_timings_exposes_recorder_without_response_mutation() -> None:
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: object())
    result = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )

    assert adapter.phase_timings == ()

    with adapter._phase_recorder.phase("context_page_setup"):
        pass

    assert adapter.phase_timings[0].phase == "context_page_setup"
    assert result == TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    assert not hasattr(result, "phase_timings")


def test_default_launch_browser_uses_shared_browser_bootstrap_launcher() -> None:
    assert TravelokaAdapter()._launch_browser is traveloka_adapter.default_launch_browser


def test_cloakbrowser_runtime_import_is_limited_to_shared_bootstrap() -> None:
    repo_root = Path(__file__).parents[2]
    matches: list[str] = []

    for path in (repo_root / "cheapy").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from cloakbrowser" in text or "import cloakbrowser" in text:
            matches.append(path.relative_to(repo_root).as_posix())

    assert matches == ["cheapy/browser_bootstrap/cloak.py"]
