from __future__ import annotations

import asyncio
import json
import time

import pytest

from cheapy.browser_bootstrap import (
    BrowserBootstrapErrorContext,
    BrowserBootstrapSession,
    BrowserBootstrapUnavailable,
)
from cheapy.models import ErrorCode, PassengersV1, ProviderStatusCode
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner.adapter import (
    SkyscannerConfig,
    SkyscannerItineraryCandidate,
    SkyscannerLegCandidate,
    SkyscannerProviderError,
)
from cheapy.providers.skyscanner.provider import SkyscannerProvider, create_provider
from cheapy.providers.skyscanner import provider as skyscanner_provider
from cheapy.providers.skyscanner.session import (
    SkyscannerSessionError,
    SkyscannerSessionManager,
)


SENSITIVE_TOKENS = (
    "/transport_deeplink/",
    "transport_deeplink",
    "__Secure-anon_token",
    "secret-cookie",
    "cookie",
    "header",
    "headers",
    "request_body",
    "requestbody",
    "raw_payload",
    "raw",
    "challenge",
    "sessionId",
    "session",
)


class FakeAdapter:
    configured_currency = "SGD"

    def __init__(self, result: list[SkyscannerItineraryCandidate] | Exception) -> None:
        self.result = result
        self.one_way_calls = 0
        self.round_trip_calls = 0

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> list[SkyscannerItineraryCandidate]:
        self.one_way_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> list[SkyscannerItineraryCandidate]:
        self.round_trip_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class SleepingAdapter:
    def __init__(self, sleep_seconds: float) -> None:
        self.sleep_seconds = sleep_seconds
        self.one_way_calls = 0

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> list[SkyscannerItineraryCandidate]:
        self.one_way_calls += 1
        time.sleep(self.sleep_seconds)
        return [_candidate()]


class FakeSessionManager:
    def __init__(
        self,
        responses: list[tuple[SkyscannerConfig, str] | Exception],
    ) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def config_for_call(
        self,
        env: object,
        *,
        timeout_seconds: float,
        deadline_monotonic: float,
        force_refresh: bool = False,
    ) -> tuple[SkyscannerConfig, str]:
        self.calls.append(
            {
                "env": env,
                "timeout_seconds": timeout_seconds,
                "deadline_monotonic": deadline_monotonic,
                "force_refresh": force_refresh,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _leg(
    origin: str = "SIN",
    destination: str = "SGN",
    departure_time: str = "2026-06-11T09:15:00",
    arrival_time: str = "2026-06-11T10:45:00",
    airline_code: str = "VJ",
    flight_number: str = "VJ814",
    duration_minutes: int = 90,
) -> SkyscannerLegCandidate:
    return SkyscannerLegCandidate(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=airline_code,
        flight_number=flight_number,
        duration_minutes=duration_minutes,
    )


def _candidate(
    *legs: SkyscannerLegCandidate,
    item_id: str = "itinerary-1",
) -> SkyscannerItineraryCandidate:
    candidate_legs = tuple(legs) if legs else (_leg(),)
    return SkyscannerItineraryCandidate(
        item_id=item_id,
        price_amount=220.96,
        currency="SGD",
        legs=candidate_legs,
        total_duration_minutes=sum(leg.duration_minutes for leg in candidate_legs),
        stops=0,
    )


def _broken_candidate(item_id: str = "broken") -> SkyscannerItineraryCandidate:
    return SkyscannerItineraryCandidate(
        item_id=item_id,
        price_amount=220.96,
        currency="SGD",
        legs=(),
        total_duration_minutes=0,
        stops=0,
    )


def _request(**overrides: object) -> ProviderExactOneWayRequest:
    data = {
        "origin": "SIN",
        "destination": "SGN",
        "departure_date": "2026-06-11",
    }
    data.update(overrides)
    return ProviderExactOneWayRequest(**data)


def _round_trip_request(**overrides: object) -> ProviderExactRoundTripRequest:
    data = {
        "origin": "SIN",
        "destination": "SGN",
        "departure_date": "2026-06-11",
        "return_date": "2026-06-18",
    }
    data.update(overrides)
    return ProviderExactRoundTripRequest(**data)


def _config(cookie: str = "secret-cookie") -> SkyscannerConfig:
    return SkyscannerConfig(
        base_url="https://www.skyscanner.com.sg",
        market="SG",
        locale="en-GB",
        currency="SGD",
        cookie=cookie,
        timeout_seconds=7.0,
        user_agent="SecretUA",
    )


def assert_no_sensitive_tokens(value: object) -> None:
    text = json.dumps(value, sort_keys=True, default=str).lower()
    for token in SENSITIVE_TOKENS:
        assert token.lower() not in text


def test_provider_returns_success_result() -> None:
    adapter = FakeAdapter([_candidate()])
    provider = SkyscannerProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.provider_name == "skyscanner"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert len(result.offers) == 1
    assert result.offers[0].provider == "skyscanner"
    assert result.offers[0].public_search_url is None
    assert result.duration_ms >= 0
    assert_no_sensitive_tokens(result.model_dump(mode="json"))


def test_provider_success_result_hashes_sensitive_item_id() -> None:
    provider = SkyscannerProvider(
        adapter=FakeAdapter(
            [
                _candidate(
                    item_id="/transport_deeplink/secret-cookie?sessionId=challenge"
                )
            ]
        ),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert len(result.offers) == 1
    assert "opaque-" in result.offers[0].offer_id
    assert_no_sensitive_tokens(result.model_dump(mode="json"))


def test_provider_rejects_children_without_adapter_call() -> None:
    adapter = FakeAdapter([_candidate()])
    provider = SkyscannerProvider(adapter=adapter, timeout_seconds=1)
    request = _request(passengers=PassengersV1(adults=1, children=1))

    result = asyncio.run(provider.search_exact_one_way(request))

    assert adapter.one_way_calls == 0
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.retryable is False
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "unsupported_passengers",
    }


def test_provider_maps_missing_cookie_error_to_failed_result() -> None:
    adapter = FakeAdapter(
        SkyscannerProviderError(
            failure_type="missing_cookie",
            message_en="Skyscanner cookie is not configured.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    )
    provider = SkyscannerProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "missing_cookie",
    }


def test_provider_does_not_write_stdout_or_stderr(capsys) -> None:
    provider = SkyscannerProvider(adapter=FakeAdapter([_candidate()]), timeout_seconds=1)

    asyncio.run(provider.search_exact_one_way(_request()))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_create_provider_does_not_require_cookie(monkeypatch) -> None:
    monkeypatch.delenv("CHEAPY_SKYSCANNER_COOKIE", raising=False)

    provider = create_provider()

    assert provider.name == "skyscanner"
    assert provider.capabilities == ("exact_one_way", "exact_round_trip")


def test_default_provider_builds_adapter_with_attempt_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    config = _config()
    session_manager = FakeSessionManager([(config, "bootstrap")])

    def fake_from_config(config: object, **kwargs: object) -> FakeAdapter:
        captured["config"] = config
        captured.update(kwargs)
        return FakeAdapter([_candidate()])

    monkeypatch.setattr(skyscanner_provider, "monotonic", lambda: 50.0)
    monkeypatch.setattr(
        "cheapy.providers.skyscanner.provider.SkyscannerAdapter.from_config",
        fake_from_config,
    )
    provider = SkyscannerProvider(
        env={},
        timeout_seconds=2.5,
        session_manager=session_manager,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert session_manager.calls == [
        {
            "env": {},
            "timeout_seconds": 2.5,
            "deadline_monotonic": 52.5,
            "force_refresh": False,
        }
    ]
    assert captured["config"] is config


def test_default_provider_passes_env_cookie_through_session_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    config = _config(cookie="env-cookie=secret-cookie")
    session_manager = FakeSessionManager([(config, "env")])

    def fake_from_config(config: object, **kwargs: object) -> FakeAdapter:
        captured["config"] = config
        return FakeAdapter([_candidate()])

    monkeypatch.setattr(
        "cheapy.providers.skyscanner.provider.SkyscannerAdapter.from_config",
        fake_from_config,
    )
    provider = SkyscannerProvider(
        env={"CHEAPY_SKYSCANNER_COOKIE": "env-cookie=secret-cookie"},
        timeout_seconds=1.0,
        session_manager=session_manager,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert session_manager.calls[0]["env"] == {
        "CHEAPY_SKYSCANNER_COOKIE": "env-cookie=secret-cookie"
    }
    assert captured["config"] is config


def test_with_timeout_seconds_preserves_same_session_manager() -> None:
    session_manager = FakeSessionManager([(_config(), "bootstrap")])
    provider = SkyscannerProvider(
        env={},
        timeout_seconds=1.0,
        session_manager=session_manager,
    )

    clone = provider.with_timeout_seconds(2.0)

    assert clone._session_manager is session_manager
    assert clone._timeout_seconds == 2.0


@pytest.mark.parametrize(
    ("failure_type", "error_code", "retryable"),
    [
        ("blocked", ErrorCode.PROVIDER_BLOCKED, False),
        ("rate_limited", ErrorCode.PROVIDER_RATE_LIMITED, True),
        ("no_usable_results", ErrorCode.PROVIDER_FAILED, False),
    ],
)
def test_cached_failure_force_refreshes_once_and_retries(
    monkeypatch: pytest.MonkeyPatch,
    failure_type: str,
    error_code: ErrorCode,
    retryable: bool,
) -> None:
    first_config = _config(cookie="cached-cookie=secret-cookie")
    second_config = _config(cookie="fresh-cookie=secret-cookie")
    session_manager = FakeSessionManager(
        [(first_config, "cache"), (second_config, "bootstrap")]
    )
    first_adapter = FakeAdapter(
        SkyscannerProviderError(
            failure_type=failure_type,
            message_en="Skyscanner cached session failed.",
            error_code=error_code,
            retryable=retryable,
        )
    )
    second_adapter = FakeAdapter([_candidate()])
    adapters = [first_adapter, second_adapter]
    from_config_calls: list[SkyscannerConfig] = []

    def fake_from_config(config: SkyscannerConfig, **kwargs: object) -> FakeAdapter:
        from_config_calls.append(config)
        return adapters.pop(0)

    monkeypatch.setattr(
        "cheapy.providers.skyscanner.provider.SkyscannerAdapter.from_config",
        fake_from_config,
    )
    provider = SkyscannerProvider(
        env={},
        timeout_seconds=1.0,
        session_manager=session_manager,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert [call["force_refresh"] for call in session_manager.calls] == [False, True]
    assert len(session_manager.calls) == 2
    assert from_config_calls == [first_config, second_config]
    assert first_adapter.one_way_calls == 1
    assert second_adapter.one_way_calls == 1


def test_provider_round_trip_uses_round_trip_adapter_and_capability() -> None:
    outbound = _leg()
    inbound = _leg(
        origin="SGN",
        destination="SIN",
        departure_time="2026-06-18T12:00:00",
        arrival_time="2026-06-18T15:30:00",
        flight_number="VJ815",
        duration_minutes=210,
    )
    adapter = FakeAdapter([_candidate(outbound, inbound)])
    provider = SkyscannerProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.one_way_calls == 0
    assert adapter.round_trip_calls == 1
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.offers[0].actual_return_date == "2026-06-18"


def test_provider_rejects_infants_without_adapter_call() -> None:
    adapter = FakeAdapter([_candidate()])
    provider = SkyscannerProvider(adapter=adapter, timeout_seconds=1)

    lap_result = asyncio.run(
        provider.search_exact_one_way(
            _request(passengers=PassengersV1(adults=1, infants_on_lap=1))
        )
    )
    seat_result = asyncio.run(
        provider.search_exact_one_way(
            _request(passengers=PassengersV1(adults=1, infants_in_seat=1))
        )
    )

    assert adapter.one_way_calls == 0
    assert lap_result.status == ProviderStatusCode.FAILED
    assert seat_result.status == ProviderStatusCode.FAILED
    assert lap_result.errors[0].details["failure_type"] == "unsupported_passengers"
    assert seat_result.errors[0].details["failure_type"] == "unsupported_passengers"


def test_provider_error_preserves_only_safe_adapter_details() -> None:
    adapter = FakeAdapter(
        SkyscannerProviderError(
            failure_type="blocked",
            message_en="Skyscanner blocked the request.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=403,
            exception_type="HTTPError",
        )
    )
    provider = SkyscannerProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "blocked",
        "http_status_code": 403,
        "exception_type": "HTTPError",
    }
    assert_no_sensitive_tokens(result.model_dump(mode="json"))


def test_provider_timeout_maps_to_retryable_timeout() -> None:
    adapter = SleepingAdapter(sleep_seconds=0.05)
    provider = SkyscannerProvider(adapter=adapter, timeout_seconds=0.001)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.status == ProviderStatusCode.FAILED
    assert result.retryable is True
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].retryable is True
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "timeout",
    }


def test_provider_preserves_and_sanitizes_normalizer_errors() -> None:
    provider = SkyscannerProvider(
        adapter=FakeAdapter([_broken_candidate(item_id="/transport_deeplink/secret")]),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    assert result.errors[0].details["failure_type"] == "parse_error"
    assert result.errors[0].details["capability"] == "exact_one_way"
    assert_no_sensitive_tokens(result.model_dump(mode="json"))


def test_bootstrap_session_manager_error_maps_to_safe_failed_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        raise BrowserBootstrapUnavailable(
            message_en="failed with secret-cookie header raw_payload sessionid",
            context=BrowserBootstrapErrorContext(
                failure_type="browser_bootstrap_failed",
                phase="setup",
                exception_type="RuntimeError",
            ),
        )

    session_manager = SkyscannerSessionManager(
        bootstrap_cookies=fake_bootstrap,
        monotonic=lambda: 0.0,
    )
    provider = SkyscannerProvider(
        env={},
        timeout_seconds=1,
        session_manager=session_manager,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))
    captured = capsys.readouterr()

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.retryable is True
    assert len(result.errors) == 1
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "browser_bootstrap_failed",
        "exception_type": "RuntimeError",
    }
    assert captured.out == ""
    assert captured.err == ""
    assert_no_sensitive_tokens(result.model_dump(mode="json"))
