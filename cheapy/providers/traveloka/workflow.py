"""High-level Traveloka browser workflows."""

from __future__ import annotations

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
from cheapy.providers.traveloka.browser_helpers import (
    read_body_text,
    remaining_timeout_ms,
)
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
    partial_round_trip_result,
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


def search_selected_round_trip(
    request: ProviderExactRoundTripRequest,
    *,
    base_url: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    launch_browser: BrowserLauncher,
    phase_recorder: TravelokaPhaseRecorder,
) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
    with open_browser_session(
        request,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        launch_browser=launch_browser,
        phase_recorder=phase_recorder,
    ) as session:
        try:
            with phase_recorder.phase("outbound_capture_wait"):
                outbound_capture = traveloka_capture.wait_for_capture(
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
        if outbound_capture.timed_out:
            return outbound_capture

        outbound_selection_timeout_ms = remaining_timeout_ms(
            session.deadline,
            raise_on_expired=False,
        )
        if outbound_selection_timeout_ms <= 0:
            return partial_round_trip_result(
                outbound_capture,
                "outbound_selection_unavailable",
            )
        with phase_recorder.phase("outbound_visible_option_discovery"):
            outbound_option = traveloka_inventory.cheapest_visible_option(
                traveloka_inventory.visible_options_from_page(
                    session.page,
                    deadline=session.deadline,
                )
            )
            if outbound_option is None:
                return partial_round_trip_result(
                    outbound_capture,
                    "outbound_selection_unavailable",
                )
        with phase_recorder.phase("outbound_binding"):
            outbound_key = traveloka_inventory.bind_visible_option_to_payload(
                outbound_option,
                outbound_capture.payload,
            )
            if outbound_key is None:
                return partial_round_trip_result(
                    outbound_capture,
                    "selected_outbound_binding_unavailable",
                )

        with phase_recorder.phase("outbound_click_transition"):
            before_outbound_selection_url = str(getattr(session.page, "url", ""))
            before_outbound_selection_body = read_body_text(
                session.page,
                timeout_ms=250,
                deadline=session.deadline,
            )
            session.state.reset()
            traveloka_activation.click_visible_option(
                outbound_option,
                timeout_ms=remaining_timeout_ms(session.deadline),
            )
            if not traveloka_selection.wait_for_outbound_selection_transition(
                session.state,
                session.page,
                outbound_key,
                session.deadline,
                outbound_payload=outbound_capture.payload,
                before_url=before_outbound_selection_url,
                before_body_text=before_outbound_selection_body,
                poll_interval_seconds=poll_interval_seconds,
            ):
                return partial_round_trip_result(
                    outbound_capture,
                    "outbound_selection_transition_unavailable",
                )
        try:
            with phase_recorder.phase("return_capture_wait"):
                return_capture = traveloka_capture.wait_for_capture(
                    session.state,
                    session.page,
                    session.deadline,
                    poll_interval_seconds=poll_interval_seconds,
                )
                if return_capture.timed_out:
                    return partial_round_trip_result(
                        outbound_capture,
                        "return_capture_timeout",
                    )
        except traveloka_errors.TravelokaProviderError as exc:
            if exc.failure_type == "timeout":
                return partial_round_trip_result(
                    outbound_capture,
                    "return_capture_timeout",
                )
            raise

        return_selection_timeout_ms = remaining_timeout_ms(
            session.deadline,
            raise_on_expired=False,
        )
        if return_selection_timeout_ms <= 0:
            return partial_round_trip_result(
                outbound_capture,
                "return_selection_unavailable",
            )
        with phase_recorder.phase("return_visible_option_discovery"):
            return_option = traveloka_inventory.cheapest_visible_option(
                traveloka_inventory.visible_options_from_page(
                    session.page,
                    deadline=session.deadline,
                )
            )
            if return_option is None:
                return partial_round_trip_result(
                    outbound_capture,
                    "return_selection_unavailable",
                )
        with phase_recorder.phase("return_binding"):
            return_key = traveloka_inventory.bind_visible_option_to_payload(
                return_option,
                return_capture.payload,
            )
            if return_key is None:
                return partial_round_trip_result(
                    outbound_capture,
                    "selected_return_binding_unavailable",
                )

        with phase_recorder.phase("return_click_transition"):
            return_click_timeout_ms = remaining_timeout_ms(
                session.deadline,
                raise_on_expired=False,
            )
            if return_click_timeout_ms <= 0:
                return partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )
            before_final_total_texts = traveloka_totals.final_total_texts(
                session.page,
                deadline=session.deadline,
            )
            before_return_selection_marker_texts = (
                traveloka_selection.return_selection_marker_texts(
                    session.page,
                    deadline=session.deadline,
                )
            )
            before_return_selection_body = read_body_text(
                session.page,
                timeout_ms=250,
                deadline=session.deadline,
            )
            traveloka_activation.click_visible_option(
                return_option,
                timeout_ms=return_click_timeout_ms,
            )
            if not traveloka_selection.wait_for_return_selection_transition(
                session.page,
                session.deadline,
                poll_interval_seconds=poll_interval_seconds,
                before_marker_texts=before_return_selection_marker_texts,
                before_body_text=before_return_selection_body,
            ):
                return partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )
        with phase_recorder.phase("final_total_read"):
            final_total = traveloka_totals.wait_for_final_total(
                session.page,
                session.deadline,
                poll_interval_seconds=poll_interval_seconds,
                before_texts=before_final_total_texts,
            )
            if final_total is None:
                return partial_round_trip_result(
                    outbound_capture,
                    "final_round_trip_total_unavailable",
                )

        final_amount, final_currency = final_total
        return TravelokaSelectedRoundTripResult(
            outbound_payload=outbound_capture.payload,
            return_payload=return_capture.payload,
            selected_outbound_key=outbound_key,
            selected_return_key=return_key,
            final_total_amount=final_amount,
            final_total_currency=final_currency,
            source_paths=(outbound_capture.source_path, return_capture.source_path),
            timed_out=False,
        )
