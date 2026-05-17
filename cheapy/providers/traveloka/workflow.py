"""High-level Traveloka browser workflows."""

from __future__ import annotations

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
from cheapy.providers.traveloka.session import BrowserLauncher, open_browser_session
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def search_exact_one_way(
    request: ProviderExactOneWayRequest,
    *,
    base_url: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    launch_browser: BrowserLauncher,
    phase_recorder: TravelokaPhaseRecorder,
) -> TravelokaCaptureResult:
    with open_browser_session(
        request,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        launch_browser=launch_browser,
        phase_recorder=phase_recorder,
    ) as session:
        try:
            with phase_recorder.phase("outbound_capture_wait"):
                return traveloka_capture.wait_for_capture(
                    session.state,
                    session.page,
                    session.deadline,
                    poll_interval_seconds=poll_interval_seconds,
                )
        except traveloka_errors.TravelokaProviderError as exc:
            if exc.failure_type == "timeout":
                traveloka_errors.raise_blocked_if_terminal_page(
                    session.page.content()  # type: ignore[attr-defined]
                )
            raise
