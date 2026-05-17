"""Browser session lifecycle for the Traveloka research provider."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import urls as traveloka_urls
from cheapy.providers.traveloka.browser_helpers import close_quietly, remaining_timeout_ms
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
BrowserLauncher = Callable[..., object]


@dataclass(frozen=True)
class TravelokaBrowserSession:
    page: object
    state: traveloka_capture.CaptureState
    deadline: float


@contextmanager
def open_browser_session(
    request: ProviderRequest,
    *,
    base_url: str,
    timeout_seconds: float,
    launch_browser: BrowserLauncher,
    phase_recorder: TravelokaPhaseRecorder,
) -> Iterator[TravelokaBrowserSession]:
    browser: object | None = None
    context: object | None = None
    state = traveloka_capture.CaptureState()
    deadline = monotonic() + timeout_seconds
    try:
        try:
            with phase_recorder.phase("browser_launch"):
                browser = launch_browser(
                    headless=True,
                    timeout=remaining_timeout_ms(deadline),
                )
        except Exception as exc:
            if traveloka_errors.is_timeout_exception(exc):
                raise traveloka_errors.timeout_error(type(exc).__name__) from None
            raise traveloka_errors.browser_unavailable_error(
                type(exc).__name__
            ) from None

        with phase_recorder.phase("context_page_setup"):
            remaining_timeout_ms(deadline)
            context = browser.new_context(locale="en-US")  # type: ignore[attr-defined]
            remaining_timeout_ms(deadline)
            page = context.new_page()  # type: ignore[attr-defined]
            remaining_timeout_ms(deadline)
            page.on("response", state.handle_response)  # type: ignore[attr-defined]

        with phase_recorder.phase("initial_navigation"):
            remaining_timeout_ms(deadline)
            page.goto(  # type: ignore[attr-defined]
                traveloka_urls.build_full_search_url(request, base_url=base_url),
                wait_until="domcontentloaded",
                timeout=remaining_timeout_ms(deadline),
            )

        yield TravelokaBrowserSession(page=page, state=state, deadline=deadline)
    finally:
        with phase_recorder.phase("cleanup"):
            close_quietly(context)
            close_quietly(browser)
