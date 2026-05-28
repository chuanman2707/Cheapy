"""Skyscanner live provider."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import os
from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.skyscanner.adapter import (
    SkyscannerAdapter,
    SkyscannerProviderError,
)
from cheapy.providers.skyscanner.normalizer import (
    EXACT_ONE_WAY_CAPABILITY,
    EXACT_ROUND_TRIP_CAPABILITY,
    PROVIDER_NAME,
    normalize_candidates,
)


DEFAULT_TIMEOUT_SECONDS = 30.0
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
SENSITIVE_DETAIL_TOKENS = (
    "/transport_deeplink/",
    "transport_deeplink",
    "__secure-anon_token",
    "secret-cookie",
    "cookie",
    "header",
    "request_body",
    "requestbody",
    "raw_payload",
    "raw",
    "challenge",
    "sessionid",
    "session",
)


class SkyscannerProvider:
    """Live provider backed by the Skyscanner HTTP adapter."""

    name = PROVIDER_NAME
    capabilities = (EXACT_ONE_WAY_CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)

    def __init__(
        self,
        *,
        adapter: object | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._adapter = adapter
        self._timeout_seconds = timeout_seconds
        self._env = dict(os.environ if env is None else env)

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
        passenger_error = _unsupported_passengers_error(request, capability)
        if passenger_error is not None:
            return self._failed_result(started, capability, passenger_error)

        try:
            candidates = await asyncio.wait_for(
                asyncio.to_thread(
                    self._search_sync,
                    request,
                    search_method_name=search_method_name,
                ),
                timeout=self._timeout_seconds,
            )
            offers, errors = normalize_candidates(candidates, request)
            errors = _errors_with_capability(errors, capability)
        except TimeoutError:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    message_en="Skyscanner provider timed out.",
                    failure_type="timeout",
                    retryable=True,
                    capability=capability,
                ),
            )
        except SkyscannerProviderError as exc:
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
                    message_en="Skyscanner provider raised an unexpected exception.",
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

    def _adapter_for_call(self) -> object:
        if self._adapter is not None:
            return self._adapter
        return SkyscannerAdapter.from_env(self._env)

    def _search_sync(
        self,
        request: ProviderRequest,
        *,
        search_method_name: str,
    ) -> list[object]:
        adapter = self._adapter_for_call()
        search_method = getattr(adapter, search_method_name)
        return search_method(request)

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


def create_provider() -> SkyscannerProvider:
    return SkyscannerProvider()


def _provider_error(
    *,
    code: ErrorCode,
    message_en: str,
    failure_type: str,
    retryable: bool,
    capability: str,
    http_status_code: int | None = None,
    exception_type: str | None = None,
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
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )


def _unsupported_passengers_error(
    request: ProviderRequest,
    capability: str,
) -> ErrorV1 | None:
    passengers = request.passengers
    if (
        passengers.children == 0
        and passengers.infants_on_lap == 0
        and passengers.infants_in_seat == 0
    ):
        return None
    return _provider_error(
        code=ErrorCode.PROVIDER_FAILED,
        message_en="Skyscanner provider supports adult passengers only.",
        failure_type="unsupported_passengers",
        retryable=False,
        capability=capability,
    )


def _errors_with_capability(errors: list[ErrorV1], capability: str) -> list[ErrorV1]:
    remapped_errors = []
    for error in errors:
        details = _sanitized_details(error.details)
        details["capability"] = capability
        remapped_errors.append(error.model_copy(update={"details": details}))
    return remapped_errors


def _sanitized_details(details: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key, value in details.items():
        if _is_sensitive_text(key) or _is_sensitive_value(value):
            continue
        sanitized[key] = value
    sanitized["provider"] = PROVIDER_NAME
    return sanitized


def _is_sensitive_value(value: object) -> bool:
    if isinstance(value, str):
        return _is_sensitive_text(value)
    if isinstance(value, Mapping):
        return any(
            _is_sensitive_text(str(key)) or _is_sensitive_value(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_is_sensitive_value(item) for item in value)
    return False


def _is_sensitive_text(value: str) -> bool:
    text = value.lower()
    return any(token in text for token in SENSITIVE_DETAIL_TOKENS)


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
