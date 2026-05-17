"""Provider-local result contracts for the Traveloka research provider."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TravelokaCaptureResult:
    payload: dict[str, object]
    source_path: str
    search_completed: bool
    timed_out: bool = False
    partial_failure_type: str | None = None


@dataclass(frozen=True)
class TravelokaSelectedRoundTripResult:
    outbound_payload: dict[str, object]
    return_payload: dict[str, object]
    selected_outbound_key: str | None
    selected_return_key: str | None
    final_total_amount: Decimal
    final_total_currency: str
    source_paths: tuple[str, ...]
    timed_out: bool = False


def partial_round_trip_result(
    capture: TravelokaCaptureResult,
    failure_type: str,
) -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload=capture.payload,
        source_path=capture.source_path,
        search_completed=capture.search_completed,
        timed_out=capture.timed_out,
        partial_failure_type=failure_type,
    )
