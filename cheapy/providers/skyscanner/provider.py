"""Skyscanner live provider."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
import os
import re
from time import perf_counter

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    ProviderStatusCode,
    Severity,
)
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.skyscanner import browserless, errors, search
from cheapy.providers.skyscanner.client import CurlClient, JsonHttpClient, SkyscannerHttpError


PROVIDER_NAME = "skyscanner"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 75.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 20.0
DEFAULT_BASE_URL = "https://www.skyscanner.com.sg"
DEFAULT_MARKET = "SG"
DEFAULT_LOCALE = "en-GB"
DEFAULT_CURRENCY = "SGD"
INITIAL_NO_USABLE_RESULTS_ATTEMPTS = 3
FINAL_NO_USABLE_RESULTS_ATTEMPTS = 1
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


class SkyscannerProvider:
    """Live provider backed by Skyscanner web-unified-search."""

    name = PROVIDER_NAME
    capabilities = (EXACT_ONE_WAY_CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)

    def __init__(
        self,
        *,
        adapter: object | None = None,
        env: Mapping[str, str] = os.environ,
        timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    ) -> None:
        self._env = env
        self._timeout_seconds = timeout_seconds
        self._adapter = (
            adapter
            if adapter is not None
            else SkyscannerAdapter(env=env, timeout_seconds=DEFAULT_HTTP_TIMEOUT_SECONDS)
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
        if not _has_browserless_token(self._env):
            return ProviderResult(
                provider_name=self.name,
                capability=capability,
                status=ProviderStatusCode.SKIPPED,
                offers=[],
                warnings=[],
                errors=[],
                duration_ms=_duration_ms(started),
                retryable=False,
            )

        passenger_error = _unsupported_passenger_error(request, capability)
        if passenger_error is not None:
            return self._failed_result(started, capability, passenger_error)

        try:
            search_method = getattr(self._adapter, search_method_name)
            itineraries = await asyncio.wait_for(
                asyncio.to_thread(search_method, request),
                timeout=self._timeout_seconds,
            )
            offers, errors_for_items = _normalize_itineraries(
                itineraries,
                request,
                capability=capability,
            )
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
        except errors.SkyscannerProviderError as exc:
            return self._failed_result(
                started,
                capability,
                _error_from_provider_error(exc, capability),
            )
        except SkyscannerHttpError as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=_code_for_failure_type(exc.failure_type),
                    message_en=_message_for_failure_type(exc.failure_type),
                    failure_type=_safe_failure_type(exc.failure_type),
                    retryable=True,
                    capability=capability,
                    http_status_code=exc.http_status_code,
                    exception_type=exc.exception_type,
                ),
            )
        except search.SearchError as exc:
            return self._failed_result(
                started,
                capability,
                _error_from_search_error(exc, capability),
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

        if errors_for_items and offers:
            status = ProviderStatusCode.PARTIAL
        elif errors_for_items:
            status = ProviderStatusCode.FAILED
        else:
            status = ProviderStatusCode.SUCCESS

        return ProviderResult(
            provider_name=self.name,
            capability=capability,
            status=status,
            offers=offers,
            warnings=[],
            errors=errors_for_items,
            duration_ms=_duration_ms(started),
            retryable=any(error.retryable for error in errors_for_items),
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


class SkyscannerAdapter:
    """Synchronous adapter for Browserless bootstrap plus Skyscanner search."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] = os.environ,
        http_client: JsonHttpClient | None = None,
        browserless_client: browserless.BrowserlessClient | None = None,
        bootstrap_session_fn: Callable[..., browserless.BrowserlessSession]
        = browserless.bootstrap_session,
        get_entity_fn: Callable[..., search.EntityResult] = search.get_entity,
        fetch_itineraries_fn: Callable[..., list[search.SkyscannerItinerary]]
        = search.fetch_itineraries,
        base_url: str = DEFAULT_BASE_URL,
        market: str = DEFAULT_MARKET,
        locale: str = DEFAULT_LOCALE,
        currency: str = DEFAULT_CURRENCY,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._env = env
        self._http_client = http_client if http_client is not None else CurlClient()
        self._browserless_client = browserless_client
        self._bootstrap_session_fn = bootstrap_session_fn
        self._get_entity_fn = get_entity_fn
        self._fetch_itineraries_fn = fetch_itineraries_fn
        self._base_url = base_url
        self._market = market
        self._locale = locale
        self._currency = currency
        self._timeout_seconds = timeout_seconds

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> list[search.SkyscannerItinerary]:
        return self._search(
            request,
            departure_date=request.departure_date,
            return_date=None,
        )

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> list[search.SkyscannerItinerary]:
        return self._search(
            request,
            departure_date=request.departure_date,
            return_date=request.return_date,
        )

    def _search(
        self,
        request: ProviderRequest,
        *,
        departure_date: str,
        return_date: str | None,
    ) -> list[search.SkyscannerItinerary]:
        config = self._config_from_bootstrap()
        origin, destination = self._entities(request, config)
        try:
            return self._fetch(
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                return_date=return_date,
                config=config,
                adults=request.passengers.adults,
                attempts=INITIAL_NO_USABLE_RESULTS_ATTEMPTS,
            )
        except search.NoUsableResults:
            refreshed_config = self._config_from_bootstrap()
            refreshed_origin, refreshed_destination = self._entities(
                request, refreshed_config
            )
            try:
                return self._fetch(
                    origin=refreshed_origin,
                    destination=refreshed_destination,
                    departure_date=departure_date,
                    return_date=return_date,
                    config=refreshed_config,
                    adults=request.passengers.adults,
                    attempts=FINAL_NO_USABLE_RESULTS_ATTEMPTS,
                )
            except search.NoUsableResults:
                raise errors.no_usable_results_error(
                    search_attempts=(
                        INITIAL_NO_USABLE_RESULTS_ATTEMPTS
                        + FINAL_NO_USABLE_RESULTS_ATTEMPTS
                    ),
                    cookie_refresh_count=1,
                ) from None

    def _config_from_bootstrap(self) -> search.SkyscannerConfig:
        session = self._bootstrap_session_fn(
            env=self._env,
            client=self._browserless_client,
        )
        return search.SkyscannerConfig(
            base_url=self._base_url,
            market=self._market,
            locale=self._locale,
            currency=self._currency,
            cookie=session.cookie_header,
            user_agent=session.user_agent,
            timeout_seconds=self._timeout_seconds,
        )

    def _entities(
        self,
        request: ProviderRequest,
        config: search.SkyscannerConfig,
    ) -> tuple[search.EntityResult, search.EntityResult]:
        origin = self._get_entity_fn(
            request.origin,
            config=config,
            client=self._http_client,
            is_destination=False,
        )
        destination = self._get_entity_fn(
            request.destination,
            config=config,
            client=self._http_client,
            is_destination=True,
        )
        return origin, destination

    def _fetch(
        self,
        *,
        origin: search.EntityResult,
        destination: search.EntityResult,
        departure_date: str,
        return_date: str | None,
        config: search.SkyscannerConfig,
        adults: int,
        attempts: int,
    ) -> list[search.SkyscannerItinerary]:
        return self._fetch_itineraries_fn(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            config=config,
            client=self._http_client,
            adults=adults,
            no_usable_results_attempts=attempts,
        )


def create_provider() -> SkyscannerProvider:
    return SkyscannerProvider()


def _has_browserless_token(env: Mapping[str, str]) -> bool:
    return bool(env.get("BROWSERLESS_TOKEN", "").strip())


def _unsupported_passenger_error(
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
    return _error_from_provider_error(errors.unsupported_passengers_error(), capability)


def _normalize_itineraries(
    itineraries: list[search.SkyscannerItinerary],
    request: ProviderRequest,
    *,
    capability: str,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    offers: list[FlightOfferV1] = []
    item_errors: list[ErrorV1] = []
    for item_index, itinerary in enumerate(itineraries, start=1):
        try:
            offers.append(
                _normalize_itinerary(
                    itinerary,
                    request,
                    capability=capability,
                    item_index=item_index,
                    rank=len(offers) + 1,
                )
            )
        except _ItemNormalizationError as exc:
            item_errors.append(exc.error)
    return _rank_offers(offers), item_errors


class _ItemNormalizationError(Exception):
    def __init__(self, error: ErrorV1) -> None:
        super().__init__(error.message_en)
        self.error = error


@dataclass(frozen=True)
class _RouteShape:
    actual_origin: str
    actual_destination: str
    actual_departure_date: str
    actual_return_date: str | None


def _normalize_itinerary(
    itinerary: search.SkyscannerItinerary,
    request: ProviderRequest,
    *,
    capability: str,
    item_index: int,
    rank: int,
) -> FlightOfferV1:
    try:
        route = _route_shape(itinerary.segments, request)
        legs = [_normalize_segment(segment) for segment in itinerary.segments]
        _validate_exact_dates(route, request)
        departure_offset_days = _date_offset(
            route.actual_departure_date,
            request.requested_departure_date,
        )
        return_offset_days = _return_offset_days(route, request)
        return_suffix = (
            f":{request.return_date}"
            if isinstance(request, ProviderExactRoundTripRequest)
            else ""
        )
        return FlightOfferV1(
            offer_id=(
                f"{PROVIDER_NAME}:{request.origin}-{request.destination}:"
                f"{request.departure_date}{return_suffix}:{item_index}"
            ),
            price_amount=itinerary.price_amount,
            currency=itinerary.currency,
            comparable=True,
            rank_within_currency=rank,
            global_rank=rank,
            provider=PROVIDER_NAME,
            requested_origin=request.requested_origin,
            requested_destination=request.requested_destination,
            actual_origin=route.actual_origin,
            actual_destination=route.actual_destination,
            nearby_origin_distance_km=None,
            nearby_destination_distance_km=None,
            requested_departure_date=request.requested_departure_date,
            actual_departure_date=route.actual_departure_date,
            departure_offset_days=departure_offset_days,
            requested_return_date=(
                request.requested_return_date
                if isinstance(request, ProviderExactRoundTripRequest)
                else None
            ),
            actual_return_date=route.actual_return_date,
            return_offset_days=return_offset_days,
            legs=legs,
            total_duration_minutes=itinerary.total_duration_minutes,
            stops=itinerary.stops,
            flags=OfferFlagsV1(
                uses_flexible_departure_date=departure_offset_days != 0,
                uses_flexible_return_date=return_offset_days not in (None, 0),
                has_many_stops=itinerary.stops >= 2,
            ),
            fare_details_status="not_collected",
        )
    except _ItemNormalizationError:
        raise
    except Exception as exc:
        raise _ItemNormalizationError(
            _parse_error(item_index, capability, exc)
        ) from exc


def _route_shape(
    segments: tuple[search.SkyscannerSegment, ...],
    request: ProviderRequest,
) -> _RouteShape:
    if not segments:
        raise ValueError("itinerary has no segments")
    if isinstance(request, ProviderExactRoundTripRequest):
        outbound_end_index = _chain_end_index(
            segments,
            start=request.origin,
            end=request.destination,
            start_index=0,
        )
        if outbound_end_index is None:
            raise ValueError("round-trip outbound route does not match request")
        return_start_index = outbound_end_index + 1
        return_end_index = _chain_end_index(
            segments,
            start=request.destination,
            end=request.origin,
            start_index=return_start_index,
        )
        if return_end_index is None or return_end_index != len(segments) - 1:
            raise ValueError("round-trip return route does not match request")
        return _RouteShape(
            actual_origin=request.origin,
            actual_destination=request.destination,
            actual_departure_date=segments[0].departure_time[:10],
            actual_return_date=segments[return_start_index].departure_time[:10],
        )

    end_index = _chain_end_index(
        segments,
        start=request.origin,
        end=request.destination,
        start_index=0,
    )
    if end_index is None or end_index != len(segments) - 1:
        raise ValueError("one-way route does not match request")
    return _RouteShape(
        actual_origin=segments[0].origin,
        actual_destination=segments[-1].destination,
        actual_departure_date=segments[0].departure_time[:10],
        actual_return_date=None,
    )


def _chain_end_index(
    segments: tuple[search.SkyscannerSegment, ...],
    *,
    start: str,
    end: str,
    start_index: int,
) -> int | None:
    if start_index >= len(segments) or segments[start_index].origin != start:
        return None
    current_destination = segments[start_index].destination
    if current_destination == end:
        return start_index
    for index in range(start_index + 1, len(segments)):
        segment = segments[index]
        if segment.origin != current_destination:
            return None
        current_destination = segment.destination
        if current_destination == end:
            return index
    return None


def _normalize_segment(segment: search.SkyscannerSegment) -> FlightLegV1:
    return FlightLegV1(
        origin=segment.origin,
        destination=segment.destination,
        departure_time=segment.departure_time,
        arrival_time=segment.arrival_time,
        airline_code=segment.airline_code,
        flight_number=segment.flight_number,
        duration_minutes=segment.duration_minutes,
    )


def _return_offset_days(
    route: _RouteShape,
    request: ProviderRequest,
) -> int | None:
    if not isinstance(request, ProviderExactRoundTripRequest):
        return None
    if route.actual_return_date is None:
        raise ValueError("round-trip result has no return date")
    return _date_offset(route.actual_return_date, request.requested_return_date)


def _validate_exact_dates(route: _RouteShape, request: ProviderRequest) -> None:
    if route.actual_departure_date != request.departure_date:
        raise ValueError("outbound departure date does not match exact request")
    if isinstance(request, ProviderExactRoundTripRequest):
        if route.actual_return_date != request.return_date:
            raise ValueError("return departure date does not match exact request")


def _date_offset(actual: str, requested: str) -> int:
    return (date.fromisoformat(actual) - date.fromisoformat(requested)).days


def _rank_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers}
    if len(currencies) <= 1:
        return [
            offer.model_copy(
                update={
                    "comparable": True,
                    "rank_within_currency": rank,
                    "global_rank": rank,
                }
            )
            for rank, offer in enumerate(offers, start=1)
        ]

    currency_ranks: dict[str, int] = {}
    ranked: list[FlightOfferV1] = []
    for offer in offers:
        rank = currency_ranks.get(offer.currency, 0) + 1
        currency_ranks[offer.currency] = rank
        ranked.append(
            offer.model_copy(
                update={
                    "comparable": False,
                    "rank_within_currency": rank,
                    "global_rank": None,
                }
            )
        )
    return ranked


def _error_from_provider_error(
    exc: errors.SkyscannerProviderError,
    capability: str,
) -> ErrorV1:
    return _provider_error(
        code=exc.error_code,
        message_en=_message_for_failure_type(exc.failure_type),
        failure_type=_safe_failure_type(exc.failure_type),
        retryable=exc.retryable,
        capability=capability,
        http_status_code=exc.http_status_code,
        exception_type=exc.exception_type,
        search_attempts=exc.search_attempts,
        cookie_refresh_count=exc.cookie_refresh_count,
    )


def _error_from_search_error(exc: search.SearchError, capability: str) -> ErrorV1:
    failure_type = _safe_failure_type(exc.failure_type)
    return _provider_error(
        code=_code_for_failure_type(failure_type),
        message_en=_message_for_failure_type(failure_type),
        failure_type=failure_type,
        retryable=_retryable_for_failure_type(failure_type),
        capability=capability,
    )


def _parse_error(index: int, capability: str, exc: Exception) -> ErrorV1:
    return _provider_error(
        code=ErrorCode.PROVIDER_FAILED,
        message_en="Skyscanner result could not be normalized.",
        failure_type="parse_error",
        retryable=False,
        capability=capability,
        item_index=index,
        exception_type=type(exc).__name__,
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
    search_attempts: int | None = None,
    cookie_refresh_count: int | None = None,
    item_index: int | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": capability,
        "failure_type": failure_type,
    }
    if item_index is not None:
        details["item_index"] = item_index
    if http_status_code is not None:
        details["http_status_code"] = http_status_code
    safe_exception_type = _safe_exception_type(exception_type)
    if safe_exception_type is not None:
        details["exception_type"] = safe_exception_type
    if search_attempts is not None:
        details["search_attempts"] = search_attempts
    if cookie_refresh_count is not None:
        details["cookie_refresh_count"] = cookie_refresh_count
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )


def _safe_failure_type(value: str | None) -> str:
    if value is None:
        return "provider_failed"
    normalized = value.strip().lower()
    if re.fullmatch(r"[a-z0-9_]{1,64}", normalized):
        return normalized
    return "provider_failed"


def _safe_exception_type(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", stripped):
        return stripped
    return "Exception"


def _code_for_failure_type(failure_type: str) -> ErrorCode:
    if failure_type == "blocked":
        return ErrorCode.PROVIDER_BLOCKED
    if failure_type == "rate_limited":
        return ErrorCode.PROVIDER_RATE_LIMITED
    if failure_type in {"search_incomplete", "timeout"}:
        return ErrorCode.PROVIDER_TIMEOUT
    return ErrorCode.PROVIDER_FAILED


def _retryable_for_failure_type(failure_type: str) -> bool:
    return failure_type in {
        "autosuggest_http_error",
        "autosuggest_transport_error",
        "browserless_bootstrap_failed",
        "browserless_cookie_unavailable",
        "no_usable_results",
        "rate_limited",
        "search_http_error",
        "search_incomplete",
        "search_transport_error",
        "timeout",
        "transport_error",
    }


def _message_for_failure_type(failure_type: str) -> str:
    messages = {
        "blocked": "Skyscanner returned an access challenge.",
        "rate_limited": "Skyscanner rate limited the request.",
        "search_incomplete": "Skyscanner search did not complete.",
        "timeout": "Skyscanner provider timed out.",
        "browserless_bootstrap_failed": "Skyscanner Browserless bootstrap failed.",
        "browserless_cookie_unavailable": (
            "Skyscanner Browserless bootstrap did not return cookies."
        ),
        "entity_not_found": (
            "Skyscanner did not return an entity for the requested airport."
        ),
        "entity_ambiguous": (
            "Skyscanner returned multiple matching airport entities."
        ),
        "no_usable_results": "Skyscanner did not return usable fare results.",
        "unsupported_passengers": (
            "Skyscanner provider currently supports adult passengers only."
        ),
        "transport_error": "Skyscanner transport failed.",
    }
    return messages.get(failure_type, "Skyscanner provider failed.")


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
