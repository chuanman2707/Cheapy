"""Google Fli live provider."""

from __future__ import annotations

import asyncio
from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.google_fli.adapter import (
    GoogleFliAdapter,
    GoogleFliProviderError,
)
from cheapy.providers.google_fli.normalizer import (
    CAPABILITY,
    PROVIDER_NAME,
    normalize_flights,
)


EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"


class GoogleFliProvider:
    """Live provider backed by upstream fli."""

    name = PROVIDER_NAME
    capabilities = (CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)

    def __init__(
        self,
        *,
        adapter: object | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._adapter = adapter if adapter is not None else GoogleFliAdapter()
        self._timeout_seconds = timeout_seconds

    def with_timeout_seconds(self, timeout_seconds: float) -> "GoogleFliProvider":
        return GoogleFliProvider(
            adapter=self._adapter,
            timeout_seconds=max(0.001, timeout_seconds),
        )

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return await self._search(
            request,
            capability=CAPABILITY,
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
        request: ProviderExactOneWayRequest | ProviderExactRoundTripRequest,
        *,
        capability: str,
        search_method_name: str,
    ) -> ProviderResult:
        started = perf_counter()
        try:
            search_method = getattr(self._adapter, search_method_name)
            flights = await asyncio.wait_for(
                asyncio.to_thread(search_method, request),
                timeout=self._timeout_seconds,
            )
            offers, errors = normalize_flights(
                flights,
                request,
                configured_currency=getattr(self._adapter, "configured_currency", None),
            )
            errors = _errors_with_capability(errors, capability)
        except TimeoutError:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    message_en="Google Fli provider timed out.",
                    failure_type="timeout",
                    retryable=True,
                    capability=capability,
                ),
            )
        except GoogleFliProviderError as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=exc.error_code,
                    message_en=exc.message_en,
                    failure_type=exc.failure_type,
                    retryable=exc.retryable,
                    capability=capability,
                    exception_type=exc.exception_type,
                ),
            )
        except Exception as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_FAILED,
                    message_en="Google Fli provider raised an unexpected exception.",
                    failure_type="unexpected_error",
                    retryable=False,
                    capability=capability,
                    exception_type=type(exc).__name__,
                ),
            )

        if errors and offers:
            status = ProviderStatusCode.PARTIAL
        elif errors:
            status = ProviderStatusCode.FAILED
        else:
            status = ProviderStatusCode.SUCCESS

        return ProviderResult(
            provider_name=self.name,
            capability=capability,
            status=status,
            offers=offers,
            warnings=[],
            errors=errors,
            duration_ms=_duration_ms(started),
            retryable=any(error.retryable for error in errors),
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


def create_provider() -> GoogleFliProvider:
    return GoogleFliProvider()


def _provider_error(
    *,
    code: ErrorCode,
    message_en: str,
    failure_type: str,
    retryable: bool,
    capability: str,
    exception_type: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": capability,
        "failure_type": failure_type,
    }
    if exception_type is not None:
        details["exception_type"] = exception_type
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )


def _errors_with_capability(errors: list[ErrorV1], capability: str) -> list[ErrorV1]:
    remapped_errors = []
    for error in errors:
        details = dict(error.details)
        details["capability"] = capability
        remapped_errors.append(error.model_copy(update={"details": details}))
    return remapped_errors


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
