from __future__ import annotations

import asyncio
from time import sleep
from typing import Any

import pytest

from cheapy.models import ErrorCode, ProviderStatusCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import provider as traveloka_provider
from cheapy.providers.traveloka.adapter import (
    TravelokaCaptureResult,
    TravelokaProviderError,
)
from cheapy.providers.traveloka.provider import TravelokaProvider


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
    timed_out: bool = False,
) -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload=payload,
        source_path=source_path,
        search_completed=not timed_out,
        timed_out=timed_out,
    )


class FakeAdapter:
    configured_currency = "USD"

    def __init__(self, result: TravelokaCaptureResult | Exception) -> None:
        self.result = result
        self.one_way_calls = 0
        self.round_trip_calls = 0

    def search_exact_one_way(
        self, request: ProviderExactOneWayRequest
    ) -> TravelokaCaptureResult:
        self.one_way_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(
        self, request: ProviderExactRoundTripRequest
    ) -> TravelokaCaptureResult:
        self.round_trip_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_traveloka_provider_builds_default_adapter_with_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_timeout: list[float] = []

    class FakeDefaultAdapter:
        def __init__(self, *, timeout_seconds: float) -> None:
            captured_timeout.append(timeout_seconds)

    monkeypatch.setattr(traveloka_provider, "TravelokaAdapter", FakeDefaultAdapter)

    TravelokaProvider(timeout_seconds=12.5)

    assert captured_timeout == [12.5]


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
        "source_path": "/api/v2/flight/search/poll",
    }


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
        "source_path": "/api/v2/flight/search/initial",
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
