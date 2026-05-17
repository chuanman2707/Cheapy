from __future__ import annotations

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
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
