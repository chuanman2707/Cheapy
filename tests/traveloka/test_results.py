from __future__ import annotations

from decimal import Decimal

from cheapy.providers.traveloka import results as traveloka_results
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)


def test_capture_result_carries_completion_and_timeout_state() -> None:
    result = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
        timed_out=True,
    )

    assert result.payload == {"data": {"searchResults": []}}
    assert result.source_path == "/api/v2/flight/search/initial"
    assert result.search_completed is False
    assert result.timed_out is True


def test_capture_result_carries_safe_partial_failure_metadata() -> None:
    result = TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
        timed_out=False,
        partial_failure_type="final_round_trip_total_unavailable",
    )

    assert result.partial_failure_type == "final_round_trip_total_unavailable"
    assert "http" not in result.partial_failure_type


def test_selected_round_trip_result_carries_final_total_and_safe_paths() -> None:
    result = TravelokaSelectedRoundTripResult(
        outbound_payload={"data": {"searchResults": [{"id": "out-1"}]}},
        return_payload={"data": {"searchResults": [{"id": "ret-1"}]}},
        selected_outbound_key="out-1",
        selected_return_key="ret-1",
        final_total_amount=Decimal("321.09"),
        final_total_currency="USD",
        source_paths=(
            "/api/v2/flight/search/initial",
            "/api/v2/flight/search/poll",
        ),
    )

    assert result.final_total_amount == Decimal("321.09")
    assert result.final_total_currency == "USD"
    assert result.selected_outbound_key == "out-1"
    assert result.selected_return_key == "ret-1"
    assert all(path.startswith("/api/") for path in result.source_paths)
    assert all("?" not in path for path in result.source_paths)


def test_traveloka_result_contracts_live_in_results_module() -> None:
    capture = traveloka_results.TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
    )
    partial = traveloka_results.partial_round_trip_result(
        capture,
        "return_capture_timeout",
    )
    selected = traveloka_results.TravelokaSelectedRoundTripResult(
        outbound_payload={"data": {"searchResults": [{"id": "out-1"}]}},
        return_payload={"data": {"searchResults": [{"id": "ret-1"}]}},
        selected_outbound_key="out-1",
        selected_return_key="ret-1",
        final_total_amount=Decimal("123.45"),
        final_total_currency="USD",
        source_paths=("/api/v2/flight/search/initial", "/api/v2/flight/search/poll"),
    )

    assert partial.partial_failure_type == "return_capture_timeout"
    assert partial.payload == capture.payload
    assert selected.final_total_currency == "USD"
