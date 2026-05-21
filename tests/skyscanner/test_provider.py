from __future__ import annotations

import asyncio
from collections.abc import Mapping

from cheapy.models import ErrorCode, PassengersV1, ProviderStatusCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.skyscanner import errors, search
from cheapy.providers.skyscanner.browserless import BrowserlessSession
from cheapy.providers.skyscanner.provider import SkyscannerAdapter, SkyscannerProvider


def _env() -> dict[str, str]:
    return {"BROWSERLESS_TOKEN": "test-token"}


def _request(*, adults: int = 1) -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        passengers=PassengersV1(adults=adults),
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-11",
        return_date="2026-07-18",
        requested_departure_date="2026-07-10",
        requested_return_date="2026-07-17",
    )


def _segment(
    origin: str,
    destination: str,
    *,
    departure_time: str = "2026-07-10T09:00:00",
    arrival_time: str = "2026-07-10T10:35:00",
    flight_number: str = "VJ801",
) -> search.SkyscannerSegment:
    return search.SkyscannerSegment(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=flight_number[:2],
        flight_number=flight_number,
        duration_minutes=95,
    )


def _itinerary(
    segments: tuple[search.SkyscannerSegment, ...] | None = None,
) -> search.SkyscannerItinerary:
    route_segments = segments or (
        _segment("SGN", "BKK"),
    )
    return search.SkyscannerItinerary(
        price_amount=88.5,
        currency="USD",
        deeplink_url="https://www.skyscanner.com.sg/transport_deeplink/secret",
        segments=route_segments,
        total_duration_minutes=sum(segment.duration_minutes for segment in route_segments),
        stops=0,
    )


class FakeProviderAdapter:
    def __init__(self, result: list[search.SkyscannerItinerary] | Exception) -> None:
        self.result = result
        self.one_way_calls = 0
        self.round_trip_calls = 0

    def search_exact_one_way(
        self, request: ProviderExactOneWayRequest
    ) -> list[search.SkyscannerItinerary]:
        self.one_way_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(
        self, request: ProviderExactRoundTripRequest
    ) -> list[search.SkyscannerItinerary]:
        self.round_trip_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_missing_browserless_token_returns_skipped_without_adapter_call() -> None:
    adapter = FakeProviderAdapter([_itinerary()])
    provider = SkyscannerProvider(adapter=adapter, env={})

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.provider_name == "skyscanner"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SKIPPED
    assert result.offers == []
    assert result.errors == []
    assert adapter.one_way_calls == 0
    assert adapter.round_trip_calls == 0


def test_create_provider_returns_skyscanner_provider() -> None:
    from cheapy.providers.skyscanner.provider import create_provider

    provider = create_provider()

    assert isinstance(provider, SkyscannerProvider)
    assert provider.name == "skyscanner"
    assert provider.capabilities == ("exact_one_way", "exact_round_trip")


def test_one_way_success_normalizes_offer_fields_and_legs() -> None:
    provider = SkyscannerProvider(
        adapter=FakeProviderAdapter([_itinerary()]),
        env=_env(),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    offer = result.offers[0]
    assert offer.offer_id == "skyscanner:SGN-BKK:2026-07-10:1"
    assert offer.provider == "skyscanner"
    assert offer.price_amount == 88.5
    assert offer.currency == "USD"
    assert offer.comparable is True
    assert offer.rank_within_currency == 1
    assert offer.global_rank == 1
    assert offer.requested_departure_date == "2026-07-10"
    assert offer.actual_departure_date == "2026-07-10"
    assert offer.departure_offset_days == 0
    assert offer.requested_return_date is None
    assert offer.actual_return_date is None
    assert offer.return_offset_days is None
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert offer.flags.baggage_unknown is True
    assert offer.fare_details_status == "not_collected"
    assert [(leg.origin, leg.destination, leg.flight_number) for leg in offer.legs] == [
        ("SGN", "BKK", "VJ801")
    ]
    assert "transport_deeplink" not in result.model_dump_json()


def test_round_trip_success_sets_return_fields_and_offsets() -> None:
    itinerary = _itinerary(
        (
            _segment(
                "SGN",
                "BKK",
                departure_time="2026-07-11T09:00:00",
                arrival_time="2026-07-11T10:35:00",
                flight_number="VJ801",
            ),
            _segment(
                "BKK",
                "SGN",
                departure_time="2026-07-18T11:00:00",
                arrival_time="2026-07-18T12:35:00",
                flight_number="VJ802",
            ),
        )
    )
    provider = SkyscannerProvider(
        adapter=FakeProviderAdapter([itinerary]),
        env=_env(),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    offer = result.offers[0]
    assert offer.offer_id == "skyscanner:SGN-BKK:2026-07-11:2026-07-18:1"
    assert offer.requested_departure_date == "2026-07-10"
    assert offer.actual_departure_date == "2026-07-11"
    assert offer.departure_offset_days == 1
    assert offer.requested_return_date == "2026-07-17"
    assert offer.actual_return_date == "2026-07-18"
    assert offer.return_offset_days == 1
    assert offer.flags.uses_flexible_departure_date is True
    assert offer.flags.uses_flexible_return_date is True
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]


def test_one_way_result_with_wrong_departure_date_becomes_parse_error() -> None:
    itinerary = _itinerary(
        (
            _segment(
                "SGN",
                "BKK",
                departure_time="2026-07-11T09:00:00",
                arrival_time="2026-07-11T10:35:00",
            ),
        )
    )
    provider = SkyscannerProvider(
        adapter=FakeProviderAdapter([itinerary]),
        env=_env(),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors[0].details["failure_type"] == "parse_error"


def test_round_trip_result_with_wrong_return_date_becomes_parse_error() -> None:
    itinerary = _itinerary(
        (
            _segment(
                "SGN",
                "BKK",
                departure_time="2026-07-11T09:00:00",
                arrival_time="2026-07-11T10:35:00",
                flight_number="VJ801",
            ),
            _segment(
                "BKK",
                "SGN",
                departure_time="2026-07-19T11:00:00",
                arrival_time="2026-07-19T12:35:00",
                flight_number="VJ802",
            ),
        )
    )
    provider = SkyscannerProvider(
        adapter=FakeProviderAdapter([itinerary]),
        env=_env(),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors[0].details["failure_type"] == "parse_error"


def test_unsupported_children_or_infants_returns_controlled_failure() -> None:
    adapter = FakeProviderAdapter([_itinerary()])
    provider = SkyscannerProvider(adapter=adapter, env=_env(), timeout_seconds=1)
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        passengers=PassengersV1(adults=1, children=1, infants_on_lap=1),
    )

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.status == ProviderStatusCode.FAILED
    assert result.retryable is False
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "unsupported_passengers",
    }
    assert adapter.one_way_calls == 0


def test_no_usable_results_refreshes_cookie_once_then_succeeds() -> None:
    bootstrap_calls: list[Mapping[str, str]] = []
    fetch_attempts: list[int] = []

    def bootstrap_session(**kwargs: object) -> BrowserlessSession:
        env = kwargs["env"]
        assert isinstance(env, Mapping)
        bootstrap_calls.append(env)
        return BrowserlessSession(
            cookie_header=f"traveller_context=session-{len(bootstrap_calls)}",
            user_agent="Browserless-UA",
        )

    def get_entity(
        iata_code: str,
        *,
        config: search.SkyscannerConfig,
        client: object,
        is_destination: bool = False,
    ) -> search.EntityResult:
        return search.EntityResult(
            iata=iata_code,
            entity_id=f"{iata_code}-entity",
            name=iata_code,
            place_type="Airport",
        )

    def fetch_itineraries(**kwargs: object) -> list[search.SkyscannerItinerary]:
        attempts = kwargs["no_usable_results_attempts"]
        assert isinstance(attempts, int)
        fetch_attempts.append(attempts)
        if attempts == 3:
            raise search.NoUsableResults()
        return [_itinerary()]

    adapter = SkyscannerAdapter(
        env=_env(),
        http_client=object(),
        bootstrap_session_fn=bootstrap_session,
        get_entity_fn=get_entity,
        fetch_itineraries_fn=fetch_itineraries,
    )
    provider = SkyscannerProvider(adapter=adapter, env=_env(), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert fetch_attempts == [3, 1]
    assert len(bootstrap_calls) == 2


def test_no_usable_results_failure_after_refresh_exposes_safe_attempt_metadata() -> None:
    def bootstrap_session(**kwargs: object) -> BrowserlessSession:
        return BrowserlessSession(
            cookie_header="traveller_context=session",
            user_agent="Browserless-UA",
        )

    def get_entity(
        iata_code: str,
        *,
        config: search.SkyscannerConfig,
        client: object,
        is_destination: bool = False,
    ) -> search.EntityResult:
        return search.EntityResult(
            iata=iata_code,
            entity_id=f"{iata_code}-entity",
            name=iata_code,
            place_type="Airport",
        )

    def fetch_itineraries(**kwargs: object) -> list[search.SkyscannerItinerary]:
        raise search.NoUsableResults()

    adapter = SkyscannerAdapter(
        env=_env(),
        http_client=object(),
        bootstrap_session_fn=bootstrap_session,
        get_entity_fn=get_entity,
        fetch_itineraries_fn=fetch_itineraries,
    )
    provider = SkyscannerProvider(adapter=adapter, env=_env(), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.retryable is True
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "no_usable_results",
        "search_attempts": 4,
        "cookie_refresh_count": 1,
    }
    assert "traveller_context" not in result.model_dump_json()
    assert "test-token" not in result.model_dump_json()


def test_block_and_rate_limit_structured_errors_map_safely() -> None:
    blocked = SkyscannerProvider(
        adapter=FakeProviderAdapter(errors.blocked_error(http_status_code=403)),
        env=_env(),
        timeout_seconds=1,
    )
    rate_limited = SkyscannerProvider(
        adapter=FakeProviderAdapter(errors.rate_limited_error(http_status_code=429)),
        env=_env(),
        timeout_seconds=1,
    )

    blocked_result = asyncio.run(blocked.search_exact_one_way(_request()))
    rate_limited_result = asyncio.run(rate_limited.search_exact_one_way(_request()))

    assert blocked_result.status == ProviderStatusCode.FAILED
    assert blocked_result.retryable is False
    assert blocked_result.errors[0].code == ErrorCode.PROVIDER_BLOCKED
    assert blocked_result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "blocked",
        "http_status_code": 403,
    }
    assert rate_limited_result.status == ProviderStatusCode.FAILED
    assert rate_limited_result.retryable is True
    assert rate_limited_result.errors[0].code == ErrorCode.PROVIDER_RATE_LIMITED
    assert rate_limited_result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "rate_limited",
        "http_status_code": 429,
    }


def test_unexpected_exception_redacts_secret_text() -> None:
    provider = SkyscannerProvider(
        adapter=FakeProviderAdapter(RuntimeError("token=secret cookie=secret")),
        env=_env(),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "unexpected_error",
        "exception_type": "RuntimeError",
    }
    assert "token=secret" not in result.model_dump_json()
    assert "cookie=secret" not in result.model_dump_json()


def test_invalid_route_item_becomes_parse_error_without_payload_leak() -> None:
    itinerary = _itinerary(
        (
            _segment(
                "SGN",
                "SIN",
                flight_number="SECRET123",
            ),
        )
    )
    provider = SkyscannerProvider(
        adapter=FakeProviderAdapter([itinerary]),
        env=_env(),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "parse_error",
        "item_index": 1,
        "exception_type": "ValueError",
    }
    assert "SECRET123" not in result.model_dump_json()


def test_adult_passenger_count_is_passed_to_search_core() -> None:
    seen_adults: list[int] = []

    def bootstrap_session(**kwargs: object) -> BrowserlessSession:
        return BrowserlessSession(
            cookie_header="traveller_context=session",
            user_agent="Browserless-UA",
        )

    def get_entity(
        iata_code: str,
        *,
        config: search.SkyscannerConfig,
        client: object,
        is_destination: bool = False,
    ) -> search.EntityResult:
        return search.EntityResult(
            iata=iata_code,
            entity_id=f"{iata_code}-entity",
            name=iata_code,
            place_type="Airport",
        )

    def fetch_itineraries(**kwargs: object) -> list[search.SkyscannerItinerary]:
        adults = kwargs["adults"]
        assert isinstance(adults, int)
        seen_adults.append(adults)
        return [_itinerary()]

    adapter = SkyscannerAdapter(
        env=_env(),
        http_client=object(),
        bootstrap_session_fn=bootstrap_session,
        get_entity_fn=get_entity,
        fetch_itineraries_fn=fetch_itineraries,
    )
    provider = SkyscannerProvider(adapter=adapter, env=_env(), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request(adults=3)))

    assert result.status == ProviderStatusCode.SUCCESS
    assert seen_adults == [3]
