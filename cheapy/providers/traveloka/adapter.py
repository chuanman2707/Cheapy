"""Browser capture adapter for the Traveloka research provider."""

from __future__ import annotations

from typing import Callable

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import urls as traveloka_urls
from cheapy.providers.traveloka import workflow as traveloka_workflow
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)
from cheapy.providers.traveloka.timing import (
    TravelokaPhaseRecorder,
    TravelokaPhaseTiming,
)


DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_CURRENCY = "USD"
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
        self._phase_recorder = TravelokaPhaseRecorder()

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

    def _search(self, request: ProviderExactOneWayRequest) -> TravelokaCaptureResult:
        try:
            return traveloka_workflow.search_exact_one_way(
                request,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                poll_interval_seconds=self._poll_interval_seconds,
                launch_browser=self._launch_browser,
                phase_recorder=self._phase_recorder,
            )
        except traveloka_errors.TravelokaProviderError:
            raise
        except Exception as exc:
            if traveloka_errors.is_timeout_exception(exc):
                raise traveloka_errors.timeout_error(type(exc).__name__) from None
            raise traveloka_errors.navigation_failed_error(
                type(exc).__name__
            ) from None

    def _search_selected_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        try:
            return traveloka_workflow.search_selected_round_trip(
                request,
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                poll_interval_seconds=self._poll_interval_seconds,
                launch_browser=self._launch_browser,
                phase_recorder=self._phase_recorder,
            )
        except traveloka_errors.TravelokaProviderError:
            raise
        except Exception as exc:
            if traveloka_errors.is_timeout_exception(exc):
                raise traveloka_errors.timeout_error(type(exc).__name__) from None
            raise traveloka_errors.navigation_failed_error(
                type(exc).__name__
            ) from None


def _default_launch_browser(**kwargs: object) -> object:
    from cloakbrowser import launch

    return launch(**kwargs)
