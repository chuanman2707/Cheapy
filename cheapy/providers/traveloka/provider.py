"""Traveloka live research provider."""

from __future__ import annotations

import asyncio
import multiprocessing
from multiprocessing.queues import Queue
from queue import Empty
from time import perf_counter
from typing import Any

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightOfferV1,
    ProviderStatusCode,
    Severity,
)
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.traveloka.adapter import TravelokaAdapter
from cheapy.providers.traveloka.errors import TravelokaProviderError
from cheapy.providers.traveloka.normalization import (
    normalize_payload,
    normalize_selected_round_trip,
)
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)


PROVIDER_NAME = "traveloka"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
DEFAULT_TIMEOUT_SECONDS = 45.0
_DEFAULT_ADAPTER_CLEANUP_GRACE_SECONDS = 0.05
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
SAFE_PARTIAL_FAILURE_TYPES = frozenset(
    {
        "blocked",
        "final_round_trip_total_unavailable",
        "outbound_selection_unavailable",
        "outbound_selection_transition_unavailable",
        "partial_failure",
        "rate_limited",
        "return_capture_timeout",
        "return_selection_unavailable",
        "selected_outbound_binding_unavailable",
        "selected_return_binding_unavailable",
        "timeout",
    }
)


class TravelokaProvider:
    """Live provider backed by a conservative Traveloka browser adapter."""

    name = PROVIDER_NAME
    capabilities = (EXACT_ONE_WAY_CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)

    def __init__(
        self,
        *,
        adapter: object | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._adapter = adapter
        self._timeout_seconds = timeout_seconds

    def with_timeout_seconds(self, timeout_seconds: float) -> "TravelokaProvider":
        bounded_timeout = max(0.001, timeout_seconds)
        adapter = self._adapter
        if isinstance(adapter, TravelokaAdapter):
            adapter = TravelokaAdapter(
                base_url=adapter._base_url,
                timeout_seconds=bounded_timeout,
                poll_interval_seconds=adapter._poll_interval_seconds,
                launch_browser=adapter._launch_browser,
            )
        return TravelokaProvider(
            adapter=adapter,
            timeout_seconds=bounded_timeout,
        )

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return await self._search(
            request,
            capability=EXACT_ONE_WAY_CAPABILITY,
            search_method_name="search_exact_one_way",
        )

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        return await self._search(
            request,
            capability=EXACT_ROUND_TRIP_CAPABILITY,
            search_method_name="search_exact_round_trip",
        )

    async def _search(
        self,
        request: ProviderRequest,
        *,
        capability: str,
        search_method_name: str,
    ) -> ProviderResult:
        started = perf_counter()
        try:
            if self._adapter is None:
                result = await asyncio.to_thread(
                    _run_default_adapter_search,
                    request,
                    capability=capability,
                    search_method_name=search_method_name,
                    timeout_seconds=self._timeout_seconds,
                )
                return result.model_copy(update={"duration_ms": _duration_ms(started)})
            search_method = getattr(self._adapter, search_method_name)
            capture = await asyncio.to_thread(search_method, request)
            return _provider_result_from_capture(capture, request, capability, started)
        except TravelokaProviderError as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=exc.error_code,
                    message_en=exc.message_en,
                    failure_type=exc.failure_type,
                    retryable=exc.retryable,
                    capability=capability,
                    http_status_code=exc.http_status_code,
                    exception_type=exc.exception_type,
                ),
            )
        except Exception as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_FAILED,
                    message_en="Traveloka provider raised an unexpected exception.",
                    failure_type="unexpected_error",
                    retryable=False,
                    capability=capability,
                    exception_type=type(exc).__name__,
                ),
            )

    def _failed_result(
        self,
        started: float,
        capability: str,
        error: ErrorV1,
    ) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            capability=capability,
            status=ProviderStatusCode.FAILED,
            offers=[],
            warnings=[],
            errors=[error],
            duration_ms=_duration_ms(started),
            retryable=error.retryable,
        )


def create_provider() -> TravelokaProvider:
    return TravelokaProvider()


def _run_default_adapter_search(
    request: ProviderRequest,
    *,
    capability: str,
    search_method_name: str,
    timeout_seconds: float,
) -> ProviderResult:
    if timeout_seconds <= 0:
        raise TimeoutError
    timeout = timeout_seconds
    deadline = perf_counter() + timeout
    cleanup_budget = min(_DEFAULT_ADAPTER_CLEANUP_GRACE_SECONDS, timeout * 0.25)
    run_budget = max(0.0, timeout - cleanup_budget)
    context = multiprocessing.get_context()
    result_queue: Queue[Any] = context.Queue(maxsize=1)
    process = context.Process(
        target=_default_adapter_search_worker,
        args=(result_queue, request, capability, search_method_name, timeout),
    )
    process.start()
    process.join(min(run_budget, _remaining_process_budget(deadline)))
    if process.is_alive():
        process.terminate()
        terminate_grace = cleanup_budget / 2
        process.join(min(terminate_grace, _remaining_process_budget(deadline)))
        if process.is_alive():
            process.kill()
            kill_grace = cleanup_budget - terminate_grace
            process.join(min(kill_grace, _remaining_process_budget(deadline)))
        raise TimeoutError
    try:
        remaining = _remaining_process_budget(deadline)
        if remaining <= 0:
            raise TimeoutError
        payload = result_queue.get(timeout=remaining)
    except Empty as exc:
        raise TravelokaProviderError(
            failure_type="transport_error",
            message_en="Traveloka child process did not return a result.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=type(exc).__name__,
        ) from None
    kind = payload.get("kind") if isinstance(payload, dict) else None
    if kind == "result":
        return ProviderResult.model_validate(payload.get("result"))
    if kind == "provider_error":
        error_code_value = payload.get("error_code") or ErrorCode.PROVIDER_FAILED.value
        raise TravelokaProviderError(
            failure_type=str(payload.get("failure_type", "transport_error")),
            message_en=str(payload.get("message_en", "Traveloka provider failed.")),
            error_code=ErrorCode(str(error_code_value)),
            retryable=bool(payload.get("retryable", False)),
            http_status_code=(
                int(payload["http_status_code"])
                if payload.get("http_status_code") is not None
                else None
            ),
            exception_type=(
                str(payload["exception_type"])
                if payload.get("exception_type") is not None
                else None
            ),
        )
    if kind == "unexpected_error":
        raise TravelokaProviderError(
            failure_type="unexpected_error",
            message_en="Traveloka provider raised an unexpected exception.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
            exception_type=(
                str(payload["exception_type"])
                if payload.get("exception_type") is not None
                else None
            ),
        )
    raise TravelokaProviderError(
        failure_type="transport_error",
        message_en="Traveloka child process returned an invalid result.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type="InvalidChildResult",
    )


def _default_adapter_search_worker(
    result_queue: Queue[Any],
    request: ProviderRequest,
    capability: str,
    search_method_name: str,
    timeout_seconds: float,
) -> None:
    started = perf_counter()
    try:
        adapter = TravelokaAdapter(timeout_seconds=timeout_seconds)
        search_method = getattr(adapter, search_method_name)
        capture = search_method(request)
        result = _provider_result_from_capture(capture, request, capability, started)
        result_queue.put({"kind": "result", "result": result.model_dump(mode="json")})
    except TravelokaProviderError as exc:
        result_queue.put(
            {
                "kind": "provider_error",
                "failure_type": exc.failure_type,
                "message_en": exc.message_en,
                "error_code": exc.error_code.value,
                "retryable": exc.retryable,
                "http_status_code": exc.http_status_code,
                "exception_type": exc.exception_type,
            }
        )
    except Exception as exc:
        result_queue.put(
            {
                "kind": "unexpected_error",
                "exception_type": type(exc).__name__,
            }
        )


def _remaining_process_budget(deadline: float) -> float:
    return max(0.0, deadline - perf_counter())


def _provider_result_from_capture(
    capture: object,
    request: ProviderRequest,
    capability: str,
    started: float,
) -> ProviderResult:
    offers, errors = _normalize_capture(capture, request)
    if isinstance(capture, TravelokaCaptureResult):
        partial_error = _capture_partial_error(capture, capability)
        if partial_error is not None:
            if (
                _safe_failure_type(capture.partial_failure_type)
                == "outbound_selection_transition_unavailable"
            ):
                errors = _without_return_details_unavailable(errors)
            errors.append(partial_error)
        if (
            not offers
            and not errors
            and not _is_explicit_successful_empty_capture(capture)
        ):
            errors.append(_no_usable_outbound_data_error(capability))
    elif isinstance(capture, TravelokaSelectedRoundTripResult) and capture.timed_out:
        errors.append(_partial_failure_error("timeout", capability))

    if errors and offers:
        status = ProviderStatusCode.PARTIAL
    elif errors:
        status = ProviderStatusCode.FAILED
    else:
        status = ProviderStatusCode.SUCCESS

    return ProviderResult(
        provider_name=PROVIDER_NAME,
        capability=capability,
        status=status,
        offers=offers,
        warnings=[],
        errors=errors,
        duration_ms=_duration_ms(started),
        retryable=any(error.retryable for error in errors),
    )


def _normalize_capture(
    capture: object,
    request: ProviderRequest,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    if isinstance(capture, TravelokaSelectedRoundTripResult) and isinstance(
        request,
        ProviderExactRoundTripRequest,
    ):
        return normalize_selected_round_trip(capture, request)
    if isinstance(capture, TravelokaSelectedRoundTripResult):
        return [], [_unsupported_response_error(EXACT_ONE_WAY_CAPABILITY)]
    return normalize_payload(_capture_payload(capture), request)


def _capture_partial_error(
    capture: TravelokaCaptureResult,
    capability: str,
) -> ErrorV1 | None:
    failure_type = capture.partial_failure_type
    if failure_type is None and capture.timed_out:
        failure_type = "timeout"
    safe_failure_type = _safe_failure_type(failure_type)
    if safe_failure_type is None:
        return None

    return _partial_failure_error(safe_failure_type, capability)


def _partial_failure_error(failure_type: str, capability: str) -> ErrorV1:
    code, message_en, retryable = _partial_failure_metadata(failure_type)
    return _provider_error(
        code=code,
        message_en=message_en,
        failure_type=failure_type,
        retryable=retryable,
        capability=capability,
    )


def _without_return_details_unavailable(errors: list[ErrorV1]) -> list[ErrorV1]:
    return [
        error
        for error in errors
        if error.details.get("failure_type") != "return_details_unavailable"
    ]


def _safe_failure_type(failure_type: str | None) -> str | None:
    if not failure_type:
        return None
    value = failure_type.strip().lower()
    if value in SAFE_PARTIAL_FAILURE_TYPES:
        return value
    return "partial_failure"


def _partial_failure_metadata(
    failure_type: str,
) -> tuple[ErrorCode, str, bool]:
    if failure_type in {"timeout", "return_capture_timeout"}:
        return (
            ErrorCode.PROVIDER_TIMEOUT,
            "Traveloka search timed out after returning partial fares.",
            True,
        )
    if failure_type == "rate_limited":
        return (
            ErrorCode.PROVIDER_RATE_LIMITED,
            "Traveloka rate limited the request after returning partial fares.",
            True,
        )
    if failure_type == "blocked":
        return (
            ErrorCode.PROVIDER_BLOCKED,
            "Traveloka returned an access challenge after returning partial fares.",
            False,
        )
    return (
        ErrorCode.PROVIDER_FAILED,
        "Traveloka returned partial fares with incomplete provider metadata.",
        False,
    )


def _is_explicit_successful_empty_capture(capture: TravelokaCaptureResult) -> bool:
    return capture.search_completed and not capture.timed_out


def _no_usable_outbound_data_error(capability: str) -> ErrorV1:
    return _provider_error(
        code=ErrorCode.PROVIDER_FAILED,
        message_en="Traveloka did not return usable outbound fare data.",
        failure_type="no_usable_outbound_data",
        retryable=False,
        capability=capability,
    )


def _unsupported_response_error(capability: str) -> ErrorV1:
    return _provider_error(
        code=ErrorCode.PROVIDER_FAILED,
        message_en="Traveloka returned an unsupported response.",
        failure_type="unsupported_response",
        retryable=False,
        capability=capability,
    )


def _provider_error(
    *,
    code: ErrorCode,
    message_en: str,
    failure_type: str,
    retryable: bool,
    capability: str,
    http_status_code: int | None = None,
    exception_type: str | None = None,
    source_path: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": capability,
        "failure_type": failure_type,
    }
    if http_status_code is not None:
        details["http_status_code"] = http_status_code
    if exception_type is not None:
        details["exception_type"] = exception_type
    if source_path is not None:
        details["source_path"] = source_path
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )


def _capture_payload(capture: object) -> dict[str, object]:
    if isinstance(capture, TravelokaCaptureResult):
        return capture.payload
    return capture  # type: ignore[return-value]


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
