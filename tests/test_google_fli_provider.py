from __future__ import annotations

import asyncio
import builtins
from datetime import datetime
from types import SimpleNamespace

import pytest

from cheapy.models import ErrorCode, ProviderStatusCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.google_fli.adapter import (
    GoogleFliAdapter,
    GoogleFliProviderError,
    build_search_filters,
)
from cheapy.providers.google_fli.provider import GoogleFliProvider


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-11",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-11",
        return_date="2026-06-18",
    )


def _leg() -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(value="VJ"),
        flight_number="VJ801",
        departure_airport=SimpleNamespace(value="SGN"),
        arrival_airport=SimpleNamespace(value="BKK"),
        departure_datetime=datetime(2026, 6, 11, 9, 15),
        arrival_datetime=datetime(2026, 6, 11, 10, 45),
        duration=90,
    )


def _flight(currency: str | None = "USD") -> SimpleNamespace:
    return SimpleNamespace(
        price=88.5,
        currency=currency,
        duration=90,
        stops=0,
        legs=[_leg()],
    )


class FakeAdapter:
    configured_currency = None

    def __init__(
        self,
        result: list[object] | Exception,
        *,
        configured_currency: str | None = None,
    ) -> None:
        self.result = result
        self.configured_currency = configured_currency
        self.seen_request: ProviderExactOneWayRequest | None = None
        self.seen_round_trip_request: ProviderExactRoundTripRequest | None = None

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> list[object]:
        self.seen_request = request
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> list[object]:
        self.seen_round_trip_request = request
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_build_search_filters_maps_contract_request_to_fli_filters() -> None:
    filters = build_search_filters(_request())

    assert filters.trip_type.name == "ONE_WAY"
    assert filters.seat_type.name == "ECONOMY"
    assert filters.sort_by.name == "CHEAPEST"
    assert filters.passenger_info.adults == 1
    assert filters.passenger_info.children == 0
    assert filters.passenger_info.infants_in_seat == 0
    assert filters.passenger_info.infants_on_lap == 0
    assert filters.flight_segments[0].departure_airport[0][0].name == "SGN"
    assert filters.flight_segments[0].arrival_airport[0][0].name == "BKK"
    assert filters.flight_segments[0].travel_date == "2026-06-11"
    assert filters.show_all_results is False


def test_build_search_filters_maps_round_trip_request_to_fli_filters() -> None:
    filters = build_search_filters(_round_trip_request())

    assert filters.trip_type.name == "ROUND_TRIP"
    assert len(filters.flight_segments) == 2
    assert filters.flight_segments[0].departure_airport[0][0].name == "SGN"
    assert filters.flight_segments[0].arrival_airport[0][0].name == "BKK"
    assert filters.flight_segments[0].travel_date == "2026-06-11"
    assert filters.flight_segments[1].departure_airport[0][0].name == "BKK"
    assert filters.flight_segments[1].arrival_airport[0][0].name == "SGN"
    assert filters.flight_segments[1].travel_date == "2026-06-18"
    assert filters.show_all_results is False


def test_build_search_filters_maps_unsupported_airport_to_structured_error() -> None:
    request = ProviderExactOneWayRequest(
        origin="ZZZ",
        destination="BKK",
        departure_date="2026-06-11",
    )

    with pytest.raises(GoogleFliProviderError) as exc_info:
        build_search_filters(request)

    assert exc_info.value.failure_type == "unsupported_airport_by_upstream"
    assert exc_info.value.retryable is False


def test_build_search_filters_maps_dependency_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "fli.models":
            raise ImportError("secret import details")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(GoogleFliProviderError) as exc_info:
        build_search_filters(_request())

    assert exc_info.value.failure_type == "dependency_unavailable"
    assert exc_info.value.retryable is False
    assert exc_info.value.exception_type == "ImportError"


def test_google_fli_adapter_treats_none_upstream_results_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSearchFlights:
        def search(self, filters: object) -> None:
            return None

    monkeypatch.setattr(
        "cheapy.providers.google_fli.adapter._search_class",
        lambda: FakeSearchFlights,
    )

    assert GoogleFliAdapter().search_exact_one_way(_request()) == []


def test_google_fli_provider_returns_success_result() -> None:
    adapter = FakeAdapter([_flight()])
    provider = GoogleFliProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.seen_request == _request()
    assert result.provider_name == "google_fli"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert [offer.provider for offer in result.offers] == ["google_fli"]
    assert result.duration_ms >= 0


def test_google_fli_provider_returns_round_trip_success_result() -> None:
    adapter = FakeAdapter([_flight()])
    provider = GoogleFliProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.seen_round_trip_request == _round_trip_request()
    assert result.provider_name == "google_fli"
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.SUCCESS


def test_google_fli_provider_treats_empty_results_as_success() -> None:
    provider = GoogleFliProvider(adapter=FakeAdapter([]), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert result.offers == []
    assert result.errors == []


def test_google_fli_provider_passes_adapter_configured_currency() -> None:
    provider = GoogleFliProvider(
        adapter=FakeAdapter([_flight(currency=None)], configured_currency="VND"),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert result.offers[0].currency == "VND"


def test_google_fli_provider_fails_when_currency_is_unavailable() -> None:
    provider = GoogleFliProvider(
        adapter=FakeAdapter([_flight(currency=None)]),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details["failure_type"] == "currency_unavailable"


def test_google_fli_provider_returns_partial_for_offers_with_errors() -> None:
    malformed_flight = SimpleNamespace(
        price=88.5,
        currency="USD",
        duration=90,
        stops=0,
        legs=[],
    )
    provider = GoogleFliProvider(
        adapter=FakeAdapter([malformed_flight, _flight()]),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert result.errors[0].details["failure_type"] == "parse_error"


def test_google_fli_provider_round_trip_normalizer_errors_use_round_trip_capability() -> None:
    malformed_flight = SimpleNamespace(
        price=88.5,
        currency="USD",
        duration=90,
        stops=0,
        legs=[],
    )
    provider = GoogleFliProvider(
        adapter=FakeAdapter([malformed_flight]),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].details["failure_type"] == "parse_error"
    assert result.errors[0].details["capability"] == "exact_round_trip"


def test_google_fli_provider_maps_timeout() -> None:
    provider = GoogleFliProvider(
        adapter=FakeAdapter(TimeoutError("secret timeout details")),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].details["failure_type"] == "timeout"
    assert "secret timeout details" not in result.model_dump_json()


def test_google_fli_provider_maps_unexpected_errors_without_leaking_details() -> None:
    provider = GoogleFliProvider(
        adapter=FakeAdapter(RuntimeError("secret unexpected details")),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details["failure_type"] == "unexpected_error"
    assert result.errors[0].retryable is False
    assert "secret unexpected details" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("error", "code", "failure_type", "retryable"),
    [
        (
            GoogleFliProviderError(
                failure_type="dependency_unavailable",
                message_en="Google Fli dependency is unavailable.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=False,
            ),
            ErrorCode.PROVIDER_FAILED,
            "dependency_unavailable",
            False,
        ),
        (
            GoogleFliProviderError(
                failure_type="transport_error",
                message_en="Google Fli transport failed.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=True,
            ),
            ErrorCode.PROVIDER_FAILED,
            "transport_error",
            True,
        ),
    ],
)
def test_google_fli_provider_maps_structured_adapter_errors(
    error: GoogleFliProviderError,
    code: ErrorCode,
    failure_type: str,
    retryable: bool,
) -> None:
    provider = GoogleFliProvider(adapter=FakeAdapter(error), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].code == code
    assert result.errors[0].details["failure_type"] == failure_type
    assert result.errors[0].retryable is retryable
