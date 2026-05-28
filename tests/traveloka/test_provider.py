from __future__ import annotations

import asyncio
from decimal import Decimal
import os
import sys
from time import sleep
from typing import Any

import pytest

from cheapy.models import ErrorCode, ProviderStatusCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.traveloka import provider as traveloka_provider
from cheapy.providers.traveloka.errors import TravelokaProviderError
from cheapy.providers.traveloka.provider import TravelokaProvider
from cheapy.providers.traveloka.results import (
    TravelokaCaptureResult,
    TravelokaSelectedRoundTripResult,
)


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def _payload() -> dict[str, Any]:
    return {
        "data": {
            "itineraries": [
                {
                    "id": "tv-1",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [
                        {
                            "origin": "SGN",
                            "destination": "BKK",
                            "departureTime": "2026-07-10T09:00:00",
                            "arrivalTime": "2026-07-10T10:35:00",
                            "airlineCode": "VJ",
                            "flightNumber": "VJ801",
                            "durationMinutes": 95,
                        }
                    ],
                }
            ]
        }
    }


def _capture(
    payload: dict[str, Any],
    *,
    source_path: str = "/api/v2/flight/search/initial",
    search_completed: bool | None = None,
    timed_out: bool = False,
    partial_failure_type: str | None = None,
) -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload=payload,
        source_path=source_path,
        search_completed=not timed_out if search_completed is None else search_completed,
        timed_out=timed_out,
        partial_failure_type=partial_failure_type,
    )


class FakeAdapter:
    configured_currency = "USD"

    def __init__(
        self,
        result: TravelokaCaptureResult | TravelokaSelectedRoundTripResult | Exception,
    ) -> None:
        self.result = result
        self.one_way_calls = 0
        self.round_trip_calls = 0

    def search_exact_one_way(
        self, request: ProviderExactOneWayRequest
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        self.one_way_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(
        self, request: ProviderExactRoundTripRequest
    ) -> TravelokaCaptureResult | TravelokaSelectedRoundTripResult:
        self.round_trip_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _selected_round_trip_result(
    *,
    timed_out: bool = False,
) -> TravelokaSelectedRoundTripResult:
    return TravelokaSelectedRoundTripResult(
        outbound_payload={
            "data": {
                "itineraries": [
                    {
                        "id": "out-1",
                        "price": {"amount": 111.0, "currency": "USD"},
                        "durationMinutes": 95,
                        "stops": 0,
                        "segments": [
                            {
                                "origin": "SGN",
                                "destination": "BKK",
                                "departureTime": "2026-07-10T09:00:00",
                                "arrivalTime": "2026-07-10T10:35:00",
                                "airlineCode": "VJ",
                                "flightNumber": "VJ801",
                                "durationMinutes": 95,
                            }
                        ],
                    }
                ]
            }
        },
        return_payload={
            "data": {
                "itineraries": [
                    {
                        "id": "ret-1",
                        "price": {"amount": 222.0, "currency": "USD"},
                        "durationMinutes": 95,
                        "stops": 0,
                        "segments": [
                            {
                                "origin": "BKK",
                                "destination": "SGN",
                                "departureTime": "2026-07-17T11:00:00",
                                "arrivalTime": "2026-07-17T12:35:00",
                                "airlineCode": "VJ",
                                "flightNumber": "VJ802",
                                "durationMinutes": 95,
                            }
                        ],
                    }
                ]
            }
        },
        selected_outbound_key="out-1",
        selected_return_key="ret-1",
        final_total_amount=Decimal("321.09"),
        final_total_currency="USD",
        source_paths=(
            "/api/v2/flight/search/initial",
            "/api/v2/flight/search/poll",
        ),
        timed_out=timed_out,
    )


def test_traveloka_default_provider_delegates_to_process_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_constructed(*, timeout_seconds: float) -> object:
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
            provider_name="traveloka",
            capability=capability,
            status=ProviderStatusCode.SUCCESS,
            offers=[],
            warnings=[],
            errors=[],
            duration_ms=1,
            retryable=False,
        )

    monkeypatch.setattr(traveloka_provider, "TravelokaAdapter", fail_if_constructed)
    monkeypatch.setattr(
        traveloka_provider,
        "_run_default_adapter_search",
        fake_process_helper,
        raising=False,
    )
    provider = TravelokaProvider(timeout_seconds=12.5)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert calls == [
        {
            "request": _request(),
            "capability": "exact_one_way",
            "search_method_name": "search_exact_one_way",
            "timeout_seconds": 12.5,
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
            assert kwargs["target"] is traveloka_provider._default_adapter_search_worker
            return self.process

    process = FakeProcess()
    monkeypatch.setattr(
        traveloka_provider.multiprocessing,
        "get_context",
        lambda: FakeContext(process),
    )

    with pytest.raises(TimeoutError):
        traveloka_provider._run_default_adapter_search(
            _request(),
            capability="exact_one_way",
            search_method_name="search_exact_one_way",
            timeout_seconds=0.1,
        )

    assert process.terminated is True
    assert process.killed is True
    assert sum(timeout or 0 for timeout in process.join_timeouts) <= 0.1


def test_default_provider_process_timeout_maps_to_retryable_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(
        request: ProviderExactOneWayRequest,
        *,
        capability: str,
        search_method_name: str,
        timeout_seconds: float,
    ) -> ProviderResult:
        raise TimeoutError("secret timeout details")

    monkeypatch.setattr(
        traveloka_provider,
        "_run_default_adapter_search",
        raise_timeout,
    )
    provider = TravelokaProvider(timeout_seconds=0.1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.retryable is True
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].retryable is True
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "timeout",
    }
    assert "secret timeout details" not in result.model_dump_json()


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
        def __init__(self, *, timeout_seconds: float) -> None:
            self.timeout_seconds = timeout_seconds

        def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> TravelokaCaptureResult:
            print("child stdout secret")
            print("child stderr secret", file=sys.stderr)
            os.write(1, b"child fd stdout secret\n")
            os.write(2, b"child fd stderr secret\n")
            return _capture(_payload())

    monkeypatch.setattr(traveloka_provider, "TravelokaAdapter", NoisyAdapter)
    queue = FakeQueue()

    traveloka_provider._default_adapter_search_worker(
        queue,
        _request(),
        "exact_one_way",
        "search_exact_one_way",
        1.0,
    )

    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert queue.payloads[0]["kind"] == "result"


def test_traveloka_provider_returns_success_result() -> None:
    adapter = FakeAdapter(_capture(_payload()))
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.provider_name == "traveloka"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert [offer.provider for offer in result.offers] == ["traveloka"]


def test_traveloka_provider_dispatches_selected_round_trip_result() -> None:
    adapter = FakeAdapter(_selected_round_trip_result())
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.round_trip_calls == 1
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.price_amount == 321.09
    assert offer.currency == "USD"
    assert offer.comparable is True
    assert offer.actual_return_date == "2026-07-17"
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]


def test_traveloka_provider_returns_partial_when_selected_round_trip_times_out() -> None:
    adapter = FakeAdapter(_selected_round_trip_result(timed_out=True))
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert result.offers[0].comparable is True
    assert result.offers[0].price_amount == 321.09
    assert result.retryable is True
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].retryable is True
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_round_trip",
        "failure_type": "timeout",
    }


def test_traveloka_provider_fails_when_selected_round_trip_reaches_one_way() -> None:
    adapter = FakeAdapter(_selected_round_trip_result())
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.retryable is False
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "unsupported_response",
    }


def test_traveloka_provider_returns_partial_result_for_item_parse_error() -> None:
    payload = _payload()
    payload["data"]["itineraries"].append(
        {
            "id": "bad",
            "price": {"amount": 100.0, "currency": "USD"},
            "segments": [],
        }
    )
    provider = TravelokaProvider(adapter=FakeAdapter(_capture(payload)), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert len(result.errors) == 1
    assert result.errors[0].details["failure_type"] == "parse_error"


def test_traveloka_provider_does_not_retry_adapter_error() -> None:
    adapter = FakeAdapter(
        TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka blocked the request.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=403,
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.status == ProviderStatusCode.FAILED
    assert result.retryable is False
    assert result.errors[0].code == ErrorCode.PROVIDER_BLOCKED
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "blocked",
        "http_status_code": 403,
    }


def test_traveloka_provider_maps_unsupported_response_failure() -> None:
    adapter = FakeAdapter(
        TravelokaProviderError(
            failure_type="unsupported_response",
            message_en="Traveloka returned an unsupported response.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.round_trip_calls == 1
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.retryable is False
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_round_trip",
        "failure_type": "unsupported_response",
    }


def test_traveloka_provider_does_not_wrap_adapter_with_equal_duration_timeout() -> None:
    class SlowAdapter:
        configured_currency = "USD"

        def search_exact_one_way(
            self, request: ProviderExactOneWayRequest
        ) -> TravelokaCaptureResult:
            sleep(0.02)
            return _capture(_payload())

        def search_exact_round_trip(
            self, request: ProviderExactRoundTripRequest
        ) -> TravelokaCaptureResult:
            raise AssertionError("round-trip should not be called")

    provider = TravelokaProvider(adapter=SlowAdapter(), timeout_seconds=0.01)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert len(result.offers) == 1
    assert result.errors == []


def test_traveloka_provider_returns_partial_when_capture_times_out_after_offers() -> None:
    adapter = FakeAdapter(
        _capture(
            _payload(),
            source_path="/api/v2/flight/search/poll",
            timed_out=True,
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert result.retryable is True
    assert result.errors[-1].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[-1].retryable is True
    assert result.errors[-1].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "timeout",
    }


def test_traveloka_provider_appends_safe_partial_failure_type() -> None:
    secret_path = "/api/v2/flight/search/poll?token=sk_live_secret"
    adapter = FakeAdapter(
        _capture(
            _payload(),
            source_path=secret_path,
            partial_failure_type="timeout",
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert result.offers[0].comparable is False
    assert result.errors[-1].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[-1].retryable is True
    assert result.errors[-1].details == {
        "provider": "traveloka",
        "capability": "exact_round_trip",
        "failure_type": "timeout",
    }
    assert "sk_live_secret" not in result.errors[-1].model_dump_json()


def test_traveloka_provider_maps_return_capture_timeout_to_timeout_error() -> None:
    adapter = FakeAdapter(
        _capture(
            _payload(),
            partial_failure_type="return_capture_timeout",
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert result.retryable is True
    assert result.errors[-1].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[-1].retryable is True
    assert result.errors[-1].details == {
        "provider": "traveloka",
        "capability": "exact_round_trip",
        "failure_type": "return_capture_timeout",
    }


def test_traveloka_provider_preserves_outbound_transition_failure_type() -> None:
    adapter = FakeAdapter(
        _capture(
            _payload(),
            partial_failure_type="outbound_selection_transition_unavailable",
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].retryable is False
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_round_trip",
        "failure_type": "outbound_selection_transition_unavailable",
    }


def test_traveloka_provider_maps_unknown_partial_failure_type_to_generic() -> None:
    secret = "sk_live_secret"
    adapter = FakeAdapter(
        _capture(
            _payload(),
            partial_failure_type=secret,
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert result.errors[-1].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[-1].retryable is False
    assert result.errors[-1].details == {
        "provider": "traveloka",
        "capability": "exact_round_trip",
        "failure_type": "partial_failure",
    }
    assert secret not in result.errors[-1].model_dump_json()


def test_traveloka_provider_returns_failed_when_empty_capture_times_out() -> None:
    adapter = FakeAdapter(
        _capture(
            {"data": {"searchResults": []}},
            source_path="/api/v2/flight/search/initial",
            timed_out=True,
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.retryable is True
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].retryable is True
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "timeout",
    }


def test_traveloka_provider_fails_when_empty_capture_is_not_successful() -> None:
    adapter = FakeAdapter(
        _capture(
            {"data": {"searchResults": []}},
            search_completed=False,
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.retryable is False
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "no_usable_outbound_data",
    }


def test_traveloka_provider_routes_round_trip_to_adapter() -> None:
    adapter = FakeAdapter(
        _capture(
            {
                "data": {
                    "itineraries": [
                        {
                            "id": "tv-rt-1",
                            "price": {"amount": 176.0, "currency": "USD"},
                            "durationMinutes": 190,
                            "stops": 0,
                            "segments": [
                                {
                                    "origin": "SGN",
                                    "destination": "BKK",
                                    "departureTime": "2026-07-10T09:00:00",
                                    "arrivalTime": "2026-07-10T10:35:00",
                                    "airlineCode": "VJ",
                                    "flightNumber": "VJ801",
                                    "durationMinutes": 95,
                                },
                                {
                                    "origin": "BKK",
                                    "destination": "SGN",
                                    "departureTime": "2026-07-17T11:00:00",
                                    "arrivalTime": "2026-07-17T12:35:00",
                                    "airlineCode": "VJ",
                                    "flightNumber": "VJ802",
                                    "durationMinutes": 95,
                                },
                            ],
                        }
                    ]
                }
            }
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.round_trip_calls == 1
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.comparable is False
    assert offer.actual_return_date is None
    assert offer.return_offset_days is None
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [("SGN", "BKK")]
    assert len(result.errors) == 1
    assert result.errors[0].details["failure_type"] == "return_details_unavailable"
