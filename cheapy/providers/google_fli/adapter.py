"""Adapter for upstream fli Google Flights search."""

from __future__ import annotations

from typing import Any

from cheapy.models import ErrorCode
from cheapy.providers.base import ProviderExactOneWayRequest


class GoogleFliProviderError(Exception):
    """Structured provider-local error safe to map into Contract V1."""

    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        error_code: ErrorCode,
        retryable: bool,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
        self.error_code = error_code
        self.retryable = retryable
        self.exception_type = exception_type


class GoogleFliAdapter:
    """Sync adapter around upstream fli search."""

    configured_currency: str | None = None

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> list[object]:
        try:
            search = _search_class()()
            filters = build_search_filters(request)
            results = search.search(filters)
        except GoogleFliProviderError:
            raise
        except TimeoutError:
            raise
        except Exception as exc:
            raise GoogleFliProviderError(
                failure_type="transport_error",
                message_en="Google Fli transport failed.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=True,
                exception_type=type(exc).__name__,
            ) from exc

        if results is None:
            return []
        if isinstance(results, list):
            return results
        return list(results)


def build_search_filters(request: ProviderExactOneWayRequest) -> Any:
    """Build upstream fli search filters for exact one-way search."""
    try:
        from fli.models import (
            Airport,
            FlightSearchFilters,
            FlightSegment,
            PassengerInfo,
            SeatType,
            SortBy,
            TripType,
        )
    except Exception as exc:
        raise GoogleFliProviderError(
            failure_type="dependency_unavailable",
            message_en="Google Fli dependency is unavailable.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
            exception_type=type(exc).__name__,
        ) from exc

    origin = _airport(Airport, request.origin)
    destination = _airport(Airport, request.destination)
    return FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(
            adults=request.passengers.adults,
            children=request.passengers.children,
            infants_in_seat=request.passengers.infants_in_seat,
            infants_on_lap=request.passengers.infants_on_lap,
        ),
        flight_segments=[
            FlightSegment(
                departure_airport=[[origin, 0]],
                arrival_airport=[[destination, 0]],
                travel_date=request.departure_date,
            )
        ],
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
    )


def _airport(airport_enum: Any, iata: str) -> Any:
    try:
        return airport_enum[iata]
    except KeyError as exc:
        raise GoogleFliProviderError(
            failure_type="unsupported_airport_by_upstream",
            message_en="Google Fli does not support the requested airport.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        ) from exc


def _search_class() -> Any:
    try:
        from fli.search import SearchFlights
    except Exception as exc:
        raise GoogleFliProviderError(
            failure_type="dependency_unavailable",
            message_en="Google Fli dependency is unavailable.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
            exception_type=type(exc).__name__,
        ) from exc
    return SearchFlights
