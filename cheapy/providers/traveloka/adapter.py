"""Browser capture adapter for the Traveloka research provider."""

from __future__ import annotations

from time import monotonic
from typing import Callable

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import activation as traveloka_activation
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import inventory as traveloka_inventory
from cheapy.providers.traveloka import selection as traveloka_selection
from cheapy.providers.traveloka import totals as traveloka_totals
from cheapy.providers.traveloka import urls as traveloka_urls
from cheapy.providers.traveloka.browser_helpers import (
    close_quietly,
    read_body_text,
    remaining_timeout_ms,
)
from cheapy.providers.traveloka.errors import (
    TravelokaProviderError,
    browser_unavailable_error,
    is_timeout_exception,
    navigation_failed_error,
    raise_blocked_if_terminal_page,
    timeout_error,
)
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
    partial_round_trip_result,
)
from cheapy.providers.traveloka.timing import (
    TravelokaPhaseRecorder,
    TravelokaPhaseTiming,
)


DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_CURRENCY = "USD"
DEFAULT_LOCALE = "en-en"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
BrowserLauncher = Callable[..., object]


class TravelokaAdapter:
    """Sync browser adapter around Traveloka flight search capture surfaces."""

    configured_currency = DEFAULT_CURRENCY

    def __init__(
        self,
        *,
        base_url: str = traveloka_urls.DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = 0.25,
        launch_browser: BrowserLauncher | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0")
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._launch_browser = (
            launch_browser if launch_browser is not None else _default_launch_browser
        )
        self._phase_recorder = TravelokaPhaseRecorder(clock=monotonic)

    @property
    def phase_timings(self) -> tuple[TravelokaPhaseTiming, ...]:
        return self._phase_recorder.records

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> TravelokaCaptureResult:
        return self._search(request)

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        return self._search_selected_round_trip(request)

    def _search(self, request: ProviderRequest) -> TravelokaCaptureResult:
        browser: object | None = None
        context: object | None = None
        state = traveloka_capture.CaptureState()
        deadline = monotonic() + self._timeout_seconds
        try:
            try:
                with self._phase_recorder.phase("browser_launch"):
                    browser = self._launch_browser(
                        headless=True,
                        timeout=remaining_timeout_ms(deadline),
                    )
            except Exception as exc:
                if is_timeout_exception(exc):
                    raise timeout_error(type(exc).__name__) from None
                raise browser_unavailable_error(type(exc).__name__) from None

            with self._phase_recorder.phase("context_page_setup"):
                remaining_timeout_ms(deadline)
                context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page = context.new_page()  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page.on("response", state.handle_response)  # type: ignore[attr-defined]
            with self._phase_recorder.phase("initial_navigation"):
                remaining_timeout_ms(deadline)
                page.goto(  # type: ignore[attr-defined]
                    traveloka_urls.build_full_search_url(
                        request,
                        base_url=self._base_url,
                    ),
                    wait_until="domcontentloaded",
                    timeout=remaining_timeout_ms(deadline),
                )

            try:
                with self._phase_recorder.phase("outbound_capture_wait"):
                    return traveloka_capture.wait_for_capture(
                        state,
                        page,
                        deadline,
                        poll_interval_seconds=self._poll_interval_seconds,
                    )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    raise_blocked_if_terminal_page(page.content())  # type: ignore[attr-defined]
                raise
        except TravelokaProviderError:
            raise
        except Exception as exc:
            if is_timeout_exception(exc):
                raise timeout_error(type(exc).__name__) from None
            raise navigation_failed_error(type(exc).__name__) from None
        finally:
            with self._phase_recorder.phase("cleanup"):
                close_quietly(context)
                close_quietly(browser)

    def _search_selected_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        browser: object | None = None
        context: object | None = None
        state = traveloka_capture.CaptureState()
        deadline = monotonic() + self._timeout_seconds
        try:
            try:
                with self._phase_recorder.phase("browser_launch"):
                    browser = self._launch_browser(
                        headless=True,
                        timeout=remaining_timeout_ms(deadline),
                    )
            except Exception as exc:
                if is_timeout_exception(exc):
                    raise timeout_error(type(exc).__name__) from None
                raise browser_unavailable_error(type(exc).__name__) from None

            with self._phase_recorder.phase("context_page_setup"):
                remaining_timeout_ms(deadline)
                context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page = context.new_page()  # type: ignore[attr-defined]
                remaining_timeout_ms(deadline)
                page.on("response", state.handle_response)  # type: ignore[attr-defined]
            with self._phase_recorder.phase("initial_navigation"):
                remaining_timeout_ms(deadline)
                page.goto(  # type: ignore[attr-defined]
                    traveloka_urls.build_full_search_url(
                        request,
                        base_url=self._base_url,
                    ),
                    wait_until="domcontentloaded",
                    timeout=remaining_timeout_ms(deadline),
                )

            try:
                with self._phase_recorder.phase("outbound_capture_wait"):
                    outbound_capture = traveloka_capture.wait_for_capture(
                        state,
                        page,
                        deadline,
                        poll_interval_seconds=self._poll_interval_seconds,
                    )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    raise_blocked_if_terminal_page(page.content())  # type: ignore[attr-defined]
                raise
            if outbound_capture.timed_out:
                return outbound_capture

            outbound_selection_timeout_ms = remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if outbound_selection_timeout_ms <= 0:
                return partial_round_trip_result(
                    outbound_capture,
                    "outbound_selection_unavailable",
                )
            with self._phase_recorder.phase("outbound_visible_option_discovery"):
                outbound_option = traveloka_inventory.cheapest_visible_option(
                    traveloka_inventory.visible_options_from_page(
                        page,
                        deadline=deadline,
                    )
                )
                if outbound_option is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "outbound_selection_unavailable",
                    )
            with self._phase_recorder.phase("outbound_binding"):
                outbound_key = traveloka_inventory.bind_visible_option_to_payload(
                    outbound_option,
                    outbound_capture.payload,
                )
                if outbound_key is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "selected_outbound_binding_unavailable",
                    )

            with self._phase_recorder.phase("outbound_click_transition"):
                before_outbound_selection_url = str(getattr(page, "url", ""))
                before_outbound_selection_body = read_body_text(
                    page,
                    timeout_ms=250,
                    deadline=deadline,
                )
                state.reset()
                traveloka_activation.click_visible_option(
                    outbound_option,
                    timeout_ms=remaining_timeout_ms(deadline),
                )
                if not traveloka_selection.wait_for_outbound_selection_transition(
                    state,
                    page,
                    outbound_key,
                    deadline,
                    outbound_payload=outbound_capture.payload,
                    before_url=before_outbound_selection_url,
                    before_body_text=before_outbound_selection_body,
                    poll_interval_seconds=self._poll_interval_seconds,
                ):
                    return partial_round_trip_result(
                        outbound_capture,
                        "outbound_selection_transition_unavailable",
                    )
            try:
                with self._phase_recorder.phase("return_capture_wait"):
                    return_capture = traveloka_capture.wait_for_capture(
                        state,
                        page,
                        deadline,
                        poll_interval_seconds=self._poll_interval_seconds,
                    )
            except TravelokaProviderError as exc:
                if exc.failure_type == "timeout":
                    return partial_round_trip_result(
                        outbound_capture,
                        "return_capture_timeout",
                    )
                raise
            if return_capture.timed_out:
                return partial_round_trip_result(
                    outbound_capture,
                    "return_capture_timeout",
                )

            return_selection_timeout_ms = remaining_timeout_ms(
                deadline,
                raise_on_expired=False,
            )
            if return_selection_timeout_ms <= 0:
                return partial_round_trip_result(
                    outbound_capture,
                    "return_selection_unavailable",
                )
            with self._phase_recorder.phase("return_visible_option_discovery"):
                return_option = traveloka_inventory.cheapest_visible_option(
                    traveloka_inventory.visible_options_from_page(
                        page,
                        deadline=deadline,
                    )
                )
                if return_option is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "return_selection_unavailable",
                    )
            with self._phase_recorder.phase("return_binding"):
                return_key = traveloka_inventory.bind_visible_option_to_payload(
                    return_option,
                    return_capture.payload,
                )
                if return_key is None:
                    return partial_round_trip_result(
                        outbound_capture,
                        "selected_return_binding_unavailable",
                    )

            with self._phase_recorder.phase("return_click_transition"):
                return_click_timeout_ms = remaining_timeout_ms(
                    deadline,
                    raise_on_expired=False,
                )
                if return_click_timeout_ms <= 0:
                    return partial_round_trip_result(
                        outbound_capture,
                        "final_round_trip_total_unavailable",
                    )
                before_final_total_texts = traveloka_totals.final_total_texts(
                    page,
                    deadline=deadline,
                )
                before_return_selection_marker_texts = (
                    traveloka_selection.return_selection_marker_texts(
                        page,
                        deadline=deadline,
                    )
                )
                before_return_selection_body = read_body_text(
                    page,
                    timeout_ms=250,
                    deadline=deadline,
                )
                traveloka_activation.click_visible_option(
                    return_option,
                    timeout_ms=return_click_timeout_ms,
                )
                if not traveloka_selection.wait_for_return_selection_transition(
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
                    before_marker_texts=before_return_selection_marker_texts,
                    before_body_text=before_return_selection_body,
                ):
                    return partial_round_trip_result(
                        outbound_capture,
                        "final_round_trip_total_unavailable",
                    )
            with self._phase_recorder.phase("final_total_read"):
                final_total = traveloka_totals.wait_for_final_total(
                    page,
                    deadline,
                    poll_interval_seconds=self._poll_interval_seconds,
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
        except TravelokaProviderError:
            raise
        except Exception as exc:
            if is_timeout_exception(exc):
                raise timeout_error(type(exc).__name__) from None
            raise navigation_failed_error(type(exc).__name__) from None
        finally:
            with self._phase_recorder.phase("cleanup"):
                close_quietly(context)
                close_quietly(browser)

def _default_launch_browser(**kwargs: object) -> object:
    from cloakbrowser import launch

    return launch(**kwargs)
