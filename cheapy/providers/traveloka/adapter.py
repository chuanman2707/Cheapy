"""Browser capture adapter for the Traveloka research provider."""

from __future__ import annotations

from time import monotonic
from typing import Callable
from urllib.parse import urlparse

from cheapy.browser_bootstrap import capture_first_party_requests
from cheapy.browser_bootstrap.errors import BrowserBootstrapError
from cheapy.browser_bootstrap.types import CapturedRequest, CapturedResponse
from cheapy.browser_bootstrap import launch_browser as default_launch_browser
from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka import replay as traveloka_replay
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
NetworkCapture = Callable[..., object]


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
        capture_network: NetworkCapture | None = None,
        replay_client: traveloka_replay.TravelokaReplayClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0")
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._launch_browser = (
            launch_browser if launch_browser is not None else default_launch_browser
        )
        self._capture_network = capture_network
        self._replay_client = replay_client
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
            if self._capture_network is not None and self._replay_client is not None:
                return self._search_with_replay(request)
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

    def _search_with_replay(
        self,
        request: ProviderExactOneWayRequest,
    ) -> TravelokaCaptureResult:
        deadline = monotonic() + self._timeout_seconds
        capture_network = self._capture_network or capture_first_party_requests
        replay_client = self._replay_client
        if replay_client is None:
            raise traveloka_errors.transport_error()
        try:
            network_capture = capture_network(
                page_url=traveloka_urls.build_full_search_url(
                    request,
                    base_url=self._base_url,
                ),
                deadline_monotonic=deadline,
                request_predicate=_is_traveloka_search_request,
                response_predicate=_is_traveloka_search_response,
                launch_browser=self._launch_browser,
            )
            replay_result = traveloka_replay.replay_or_fallback(
                network_capture,
                client=replay_client,
                timeout_seconds=_remaining_seconds(deadline),
            )
            return TravelokaCaptureResult(
                payload=replay_result.payload,
                source_path=traveloka_capture.POLL_SEARCH_PATH,
                search_completed=traveloka_capture.search_completed(
                    replay_result.payload
                ),
                timed_out=False,
            )
        except BrowserBootstrapError as exc:
            raise _traveloka_error_from_bootstrap(exc) from None

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


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise traveloka_errors.timeout_error()
    return remaining


def _is_traveloka_search_request(request: CapturedRequest) -> bool:
    return (
        request.method.upper() == "POST"
        and traveloka_capture.is_traveloka_first_party_url(request.url)
        and urlparse(request.url).path in traveloka_capture.SUPPORTED_FARE_PATHS
    )


def _is_traveloka_search_response(response: CapturedResponse) -> bool:
    return (
        traveloka_capture.is_traveloka_first_party_url(response.url)
        and urlparse(response.url).path in traveloka_capture.SUPPORTED_FARE_PATHS
        and response.status_code < 500
    )


def _traveloka_error_from_bootstrap(
    exc: BrowserBootstrapError,
) -> traveloka_errors.TravelokaProviderError:
    context = exc.context
    failure_type = context.failure_type
    if failure_type == "timeout":
        return traveloka_errors.timeout_error(context.exception_type)
    if failure_type == "blocked":
        return traveloka_errors.blocked_error(context.http_status_code)
    if failure_type == "rate_limited":
        return traveloka_errors.rate_limited_error(context.http_status_code)
    if failure_type == "browser_cookie_unavailable":
        return traveloka_errors.TravelokaProviderError(
            failure_type="browser_cookie_unavailable",
            message_en="Traveloka browser bootstrap did not produce usable cookies.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=context.exception_type,
        )
    if failure_type == "network_capture_unavailable":
        return traveloka_errors.TravelokaProviderError(
            failure_type="network_capture_unavailable",
            message_en="Traveloka browser capture did not include a replayable request.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=context.exception_type,
        )
    return traveloka_errors.TravelokaProviderError(
        failure_type="browser_bootstrap_failed",
        message_en="Traveloka browser bootstrap failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=context.exception_type,
    )
