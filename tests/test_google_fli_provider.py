from __future__ import annotations

import asyncio
import builtins
from datetime import datetime
import os
import sys
from types import SimpleNamespace

import pytest

from cheapy.models import ErrorCode, ProviderStatusCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.google_fli.adapter import (
    GoogleFliAdapter,
    GoogleFliProviderError,
    build_search_filters,
)
from cheapy.providers.google_fli.provider import GoogleFliProvider
from cheapy.providers.google_fli import provider as google_provider


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


def _return_leg() -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(value="VJ"),
        flight_number="VJ802",
        departure_airport=SimpleNamespace(value="BKK"),
        arrival_airport=SimpleNamespace(value="SGN"),
        departure_datetime=datetime(2026, 6, 18, 11, 15),
        arrival_datetime=datetime(2026, 6, 18, 12, 45),
        duration=90,
    )


def _flight(
    *,
    price: float = 88.5,
    currency: str | None = "USD",
    duration: int = 90,
    stops: int = 0,
    legs: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        price=price,
        currency=currency,
        duration=duration,
        stops=stops,
        legs=legs if legs is not None else [_leg()],
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


def test_google_fli_default_provider_delegates_to_process_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_constructed() -> object:
        raise AssertionError("default live adapter must be isolated in child process")

    calls: list[dict[str, object]] = []

    def fake_process_helper(
        request: ProviderExactOneWayRequest,
        *,
        capability: str,
        search_method_name: str,
        timeout_seconds: float,
    ) -> ProviderResult:
        calls.append(
            {
                "request": request,
                "capability": capability,
                "search_method_name": search_method_name,
                "timeout_seconds": timeout_seconds,
            }
        )
        return ProviderResult(
            provider_name="google_fli",
            capability=capability,
            status=ProviderStatusCode.SUCCESS,
            offers=[],
            warnings=[],
            errors=[],
            duration_ms=1,
            retryable=False,
        )

    monkeypatch.setattr(
        "cheapy.providers.google_fli.provider.GoogleFliAdapter",
        fail_if_constructed,
    )
    monkeypatch.setattr(
        google_provider,
        "_run_default_adapter_search",
        fake_process_helper,
        raising=False,
    )
    provider = GoogleFliProvider(timeout_seconds=0.25)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert calls == [
        {
            "request": _request(),
            "capability": "exact_one_way",
            "search_method_name": "search_exact_one_way",
            "timeout_seconds": 0.25,
        }
    ]
    assert result.status == ProviderStatusCode.SUCCESS


def test_default_adapter_process_cleanup_stays_inside_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeQueue:
        pass

    class FakeProcess:
        def __init__(self) -> None:
            self.join_timeouts: list[float | None] = []
            self.terminated = False
            self.killed = False

        def start(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            self.join_timeouts.append(timeout)

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    class FakeContext:
        def __init__(self, process: FakeProcess) -> None:
            self.process = process

        def Queue(self, maxsize: int) -> FakeQueue:
            assert maxsize == 1
            return FakeQueue()

        def Process(self, **kwargs: object) -> FakeProcess:
            assert kwargs["target"] is google_provider._default_adapter_search_worker
            return self.process

    process = FakeProcess()
    monkeypatch.setattr(
        google_provider.multiprocessing,
        "get_context",
        lambda: FakeContext(process),
    )

    with pytest.raises(TimeoutError):
        google_provider._run_default_adapter_search(
            _request(),
            capability="exact_one_way",
            search_method_name="search_exact_one_way",
            timeout_seconds=0.1,
        )

    assert process.terminated is True
    assert process.killed is True
    assert sum(timeout or 0 for timeout in process.join_timeouts) <= 0.1


def test_default_adapter_worker_suppresses_child_stdio(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    class FakeQueue:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def put(self, payload: dict[str, object]) -> None:
            self.payloads.append(payload)

    class NoisyAdapter:
        configured_currency = "USD"

        def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> list[object]:
            print("child stdout secret")
            print("child stderr secret", file=sys.stderr)
            os.write(1, b"child fd stdout secret\n")
            os.write(2, b"child fd stderr secret\n")
            return []

    monkeypatch.setattr(google_provider, "GoogleFliAdapter", NoisyAdapter)
    queue = FakeQueue()

    google_provider._default_adapter_search_worker(
        queue,
        _request(),
        "exact_one_way",
        "search_exact_one_way",
    )

    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert queue.payloads[0]["kind"] == "result"


def test_google_fli_provider_returns_round_trip_success_result() -> None:
    adapter = FakeAdapter(
        [
            (
                _flight(),
                _flight(legs=[_return_leg()], price=250, currency="EUR"),
            )
        ]
    )
    provider = GoogleFliProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.seen_round_trip_request == _round_trip_request()
    assert result.provider_name == "google_fli"
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert result.offers[0].actual_return_date == "2026-06-18"


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
