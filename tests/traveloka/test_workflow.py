from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka import workflow as traveloka_workflow
from cheapy.providers.traveloka.capture import CaptureState
from cheapy.providers.traveloka.results import TravelokaCaptureResult
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


@dataclass(frozen=True)
class FakeSession:
    page: object
    state: CaptureState
    deadline: float


class FakePage:
    def __init__(self) -> None:
        self.content_calls = 0

    def content(self) -> str:
        self.content_calls += 1
        return "<html></html>"


def test_search_exact_one_way_waits_for_outbound_capture(monkeypatch) -> None:
    page = FakePage()
    state = CaptureState()
    expected = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    captured: dict[str, object] = {}

    @contextmanager
    def fake_open_session(*args: object, **kwargs: object):
        captured["open_args"] = args
        captured["open_kwargs"] = kwargs
        yield FakeSession(page=page, state=state, deadline=123.0)

    def fake_wait_for_capture(
        state_arg: CaptureState,
        page_arg: object,
        deadline_arg: float,
        *,
        poll_interval_seconds: float,
    ) -> TravelokaCaptureResult:
        captured["state"] = state_arg
        captured["page"] = page_arg
        captured["deadline"] = deadline_arg
        captured["poll_interval_seconds"] = poll_interval_seconds
        return expected

    monkeypatch.setattr(traveloka_workflow, "open_browser_session", fake_open_session)
    monkeypatch.setattr(
        traveloka_workflow.traveloka_capture,
        "wait_for_capture",
        fake_wait_for_capture,
    )

    result = traveloka_workflow.search_exact_one_way(
        _one_way_request(),
        base_url="https://www.traveloka.com",
        timeout_seconds=5.0,
        poll_interval_seconds=0.25,
        launch_browser=lambda **kwargs: object(),
        phase_recorder=TravelokaPhaseRecorder(),
    )

    assert result is expected
    assert captured["state"] is state
    assert captured["page"] is page
    assert captured["deadline"] == 123.0
    assert captured["poll_interval_seconds"] == 0.25
