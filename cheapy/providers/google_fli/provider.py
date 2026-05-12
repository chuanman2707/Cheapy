"""Google Fli live provider."""

from __future__ import annotations

import asyncio
from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers.google_fli.adapter import (
    GoogleFliAdapter,
    GoogleFliProviderError,
)
from cheapy.providers.google_fli.normalizer import (
    CAPABILITY,
    PROVIDER_NAME,
    normalize_flights,
)


class GoogleFliProvider:
    """Live provider backed by upstream fli."""

    name = PROVIDER_NAME
    capabilities = (CAPABILITY,)

    def __init__(
        self,
        *,
        adapter: object | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._adapter = adapter if adapter is not None else GoogleFliAdapter()
        self._timeout_seconds = timeout_seconds

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        started = perf_counter()
        try:
            flights = await asyncio.wait_for(
                asyncio.to_thread(self._adapter.search_exact_one_way, request),
                timeout=self._timeout_seconds,
            )
            offers, errors = normalize_flights(
                flights,
                request,
                configured_currency=getattr(self._adapter, "configured_currency", None),
            )
        except TimeoutError:
            return self._failed_result(
                started,
                _provider_error(
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    message_en="Google Fli provider timed out.",
                    failure_type="timeout",
                    retryable=True,
                ),
            )
        except GoogleFliProviderError as exc:
            return self._failed_result(
                started,
                _provider_error(
                    code=exc.error_code,
                    message_en=exc.message_en,
                    failure_type=exc.failure_type,
                    retryable=exc.retryable,
                    exception_type=exc.exception_type,
                ),
            )
        except Exception as exc:
            return self._failed_result(
                started,
                _provider_error(
                    code=ErrorCode.PROVIDER_FAILED,
                    message_en="Google Fli provider raised an unexpected exception.",
                    failure_type="unexpected_error",
                    retryable=False,
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
            capability=CAPABILITY,
            status=status,
            offers=offers,
            warnings=[],
            errors=errors,
            duration_ms=_duration_ms(started),
            retryable=any(error.retryable for error in errors),
        )

    def _failed_result(self, started: float, error: ErrorV1) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            capability=CAPABILITY,
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
    exception_type: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": CAPABILITY,
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


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
