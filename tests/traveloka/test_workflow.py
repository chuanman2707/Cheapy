from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
from cheapy.providers.traveloka import workflow as traveloka_workflow
from cheapy.providers.traveloka.capture import CaptureState
from cheapy.providers.traveloka.inventory import TravelokaVisibleOption
from cheapy.providers.traveloka.results import TravelokaCaptureResult
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


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


class ClickableLocator:
    def __init__(self) -> None:
        self.clicked = False

    def evaluate(self, script: str, **kwargs: object) -> None:
        self.clicked = True

    def click(self, **kwargs: object) -> None:
        self.clicked = True


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


def test_search_selected_round_trip_builds_selected_result(monkeypatch) -> None:
    page = FakePage()
    state = CaptureState()
    outbound = TravelokaCaptureResult(
        payload={"data": {"searchResults": [{"id": "out-1"}]}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    returning = TravelokaCaptureResult(
        payload={"data": {"searchResults": [{"id": "ret-1"}]}},
        source_path="/api/v2/flight/search/poll",
        search_completed=True,
    )
    captures = [outbound, returning]
    options = [
        TravelokaVisibleOption(
            key="out-1",
            airline_name="VJ",
            departure_time_text=None,
            arrival_time_text=None,
            route_text=None,
            price_amount=Decimal("100"),
            currency="USD",
            locator=ClickableLocator(),
        ),
        TravelokaVisibleOption(
            key="ret-1",
            airline_name="VJ",
            departure_time_text=None,
            arrival_time_text=None,
            route_text=None,
            price_amount=Decimal("200"),
            currency="USD",
            locator=ClickableLocator(),
        ),
    ]

    @contextmanager
    def fake_open_session(*args: object, **kwargs: object):
        yield FakeSession(page=page, state=state, deadline=1_000_000_000.0)

    def fake_wait_for_capture(
        *args: object,
        **kwargs: object,
    ) -> TravelokaCaptureResult:
        return captures.pop(0)

    def fake_visible_options_from_page(
        *args: object,
        **kwargs: object,
    ) -> list[TravelokaVisibleOption]:
        return [options.pop(0)]

    monkeypatch.setattr(traveloka_workflow, "open_browser_session", fake_open_session)
    monkeypatch.setattr(
        traveloka_workflow,
        "traveloka_inventory",
        traveloka_inventory,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_workflow,
        "traveloka_selection",
        traveloka_selection,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_workflow,
        "traveloka_totals",
        traveloka_totals,
        raising=False,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_capture,
        "wait_for_capture",
        fake_wait_for_capture,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_inventory,
        "visible_options_from_page",
        fake_visible_options_from_page,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_selection,
        "wait_for_outbound_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_selection,
        "wait_for_return_selection_transition",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_totals,
        "final_total_texts",
        lambda *args, **kwargs: (),
    )
    monkeypatch.setattr(
        traveloka_workflow.traveloka_totals,
        "wait_for_final_total",
        lambda *args, **kwargs: (Decimal("321.09"), "USD"),
    )

    result = traveloka_workflow.search_selected_round_trip(
        _round_trip_request(),
        base_url="https://www.traveloka.com",
        timeout_seconds=5.0,
        poll_interval_seconds=0.25,
        launch_browser=lambda **kwargs: object(),
        phase_recorder=TravelokaPhaseRecorder(),
    )

    assert result.selected_outbound_key == "out-1"
    assert result.selected_return_key == "ret-1"
    assert result.final_total_amount == Decimal("321.09")
    assert result.final_total_currency == "USD"
