from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from cheapy.models import ErrorCode, PassengersV1
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner import adapter


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: object = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeClient:
    def __init__(self, responses: list[FakeResponse] | Exception) -> None:
        self.responses = responses
        self.get_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.get_calls.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        if isinstance(self.responses, Exception):
            raise self.responses
        return self.responses.pop(0)

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.post_calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if isinstance(self.responses, Exception):
            raise self.responses
        return self.responses.pop(0)


def config() -> adapter.SkyscannerConfig:
    return adapter.SkyscannerConfig(
        base_url="https://www.skyscanner.com.sg",
        market="SG",
        locale="en-GB",
        currency="SGD",
        cookie="traveller_context=abc; __Secure-anon_token=secret",
        timeout_seconds=7.0,
    )


def config_with_deadline(deadline_monotonic: float) -> adapter.SkyscannerConfig:
    return adapter.SkyscannerConfig(
        base_url="https://www.skyscanner.com.sg",
        market="SG",
        locale="en-GB",
        currency="SGD",
        cookie="traveller_context=abc; __Secure-anon_token=secret",
        timeout_seconds=7.0,
        deadline_monotonic=deadline_monotonic,
    )


def entity(iata: str, entity_id: str) -> dict[str, object]:
    return {
        "places": [
            {
                "iataCode": iata,
                "entityId": entity_id,
                "name": iata,
                "type": "PLACE_TYPE_AIRPORT",
                "parentId": f"city-{iata}",
            }
        ]
    }


def test_curl_client_keeps_session_url_out_of_argv() -> None:
    calls: list[dict[str, object]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        config_path = Path(args[args.index("--config") + 1])
        calls.append({"args": args, "config": config_path.read_text()})
        return subprocess.CompletedProcess(args, 0, stdout='{"ok": true}\n200', stderr="")

    client = adapter.CurlClient(runner=fake_run)

    session_url = (
        "https://www.skyscanner.com.sg/g/radar/api/v2/web-unified-search/"
        "session%2Fid%3Dsecret"
    )

    response = client.get(
        session_url,
        params={},
        headers={"cookie": "session=secret-cookie"},
        timeout=5.0,
    )

    assert response.status_code == 200
    call = calls[0]
    args = call["args"]
    assert isinstance(args, list)
    argv_text = " ".join(args)
    assert "session%2Fid%3Dsecret" not in argv_text
    assert "session/id=secret" not in argv_text
    assert "secret-cookie" not in argv_text
    assert f'url = "{session_url}"' in call["config"]


def test_curl_client_bounds_subprocess_timeout_to_request_timeout() -> None:
    calls: list[dict[str, object]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "timeout": timeout})
        return subprocess.CompletedProcess(args, 0, stdout='{"ok": true}\n200', stderr="")

    response = adapter.CurlClient(runner=fake_run).get(
        "https://www.skyscanner.com.sg/test",
        params={},
        headers={},
        timeout=0.2,
    )

    assert response.status_code == 200
    args = calls[0]["args"]
    assert isinstance(args, list)
    max_time = float(args[args.index("--max-time") + 1])
    assert 0 < max_time <= 0.2
    assert calls[0]["timeout"] == 0.2


def search_payload() -> dict[str, object]:
    return {
        "context": {"status": "complete"},
        "itineraries": {
            "results": [
                {
                    "id": "itinerary-1",
                    "price": {"raw": 220.96},
                    "legs": [
                        {
                            "origin": {"displayCode": "SIN"},
                            "destination": {"displayCode": "SGN"},
                            "departure": "2026-06-11T09:15:00",
                            "arrival": "2026-06-11T10:45:00",
                            "durationInMinutes": 90,
                            "stopCount": 0,
                            "segments": [
                                {
                                    "origin": {"displayCode": "SIN"},
                                    "destination": {"displayCode": "SGN"},
                                    "departure": "2026-06-11T09:15:00",
                                    "arrival": "2026-06-11T10:45:00",
                                    "durationInMinutes": 90,
                                    "marketingCarrier": {
                                        "displayCode": "VJ",
                                        "name": "VietJet",
                                    },
                                    "flightNumber": "814",
                                }
                            ],
                        }
                    ],
                    "pricingOptions": [
                        {
                            "price": {"amount": 220.96},
                            "items": [{"url": "/transport_deeplink/secret"}],
                        }
                    ],
                }
            ]
        },
    }


def assert_no_sensitive_tokens(value: object) -> None:
    text = json.dumps(value, sort_keys=True, default=str)
    for token in (
        "/transport_deeplink/",
        "__Secure-anon_token=secret",
        "/transport_deeplink/secret",
        "challenge",
        "sessionId",
        "raw payload text",
    ):
        assert token not in text


def assert_error_is_sanitized(exc: adapter.SkyscannerProviderError) -> None:
    assert_no_sensitive_tokens(exc.__dict__)
    assert_no_sensitive_tokens(str(exc))


def test_config_repr_redacts_cookie() -> None:
    text = repr(config())
    assert "__Secure-anon_token" not in text
    assert "secret" not in text
    assert "cookie" not in text


def test_build_search_body_uses_requested_adult_count() -> None:
    origin = adapter.SkyscannerEntity(
        iata="SIN", entity_id="95673375", name="Singapore"
    )
    destination = adapter.SkyscannerEntity(
        iata="SGN", entity_id="95673379", name="Ho Chi Minh City"
    )

    body = adapter.build_search_body(
        origin=origin,
        destination=destination,
        departure_date="2026-06-11",
        return_date=None,
        adults=3,
    )

    assert body["adults"] == 3
    assert body["childAges"] == []
    assert len(body["legs"]) == 1


def test_build_search_body_preserves_adult_count_exactly() -> None:
    origin = adapter.SkyscannerEntity(
        iata="SIN", entity_id="95673375", name="Singapore"
    )
    destination = adapter.SkyscannerEntity(
        iata="SGN", entity_id="95673379", name="Ho Chi Minh City"
    )

    body = adapter.build_search_body(
        origin=origin,
        destination=destination,
        departure_date="2026-06-11",
        return_date=None,
        adults=0,
    )

    assert body["adults"] == 0


def test_fetch_itineraries_returns_minimal_candidates_without_deeplink() -> None:
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=search_payload()),
        ]
    )

    candidates = adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
        ProviderExactOneWayRequest(
            origin="SIN",
            destination="SGN",
            departure_date="2026-06-11",
        )
    )

    assert len(candidates) == 1
    assert candidates[0].price_amount == 220.96
    assert candidates[0].currency == "SGD"
    assert candidates[0].legs[0].airline_code == "VJ"
    assert candidates[0].legs[0].flight_number == "VJ814"
    assert_no_sensitive_tokens(candidates)


def test_attempt_deadline_bounds_repeated_http_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([100.0, 102.0, 105.0])
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(times))
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=search_payload()),
        ]
    )

    adapter.SkyscannerAdapter(
        config=config_with_deadline(106.0),
        client=client,
    ).search_exact_one_way(
        ProviderExactOneWayRequest(
            origin="SIN",
            destination="SGN",
            departure_date="2026-06-11",
        )
    )

    assert [call["timeout"] for call in client.get_calls] == [6.0, 4.0]
    assert [call["timeout"] for call in client.post_calls] == [1.0]


def test_expired_attempt_deadline_fails_before_client_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter.time, "monotonic", lambda: 100.1)
    client = FakeClient([])

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.get_entity_id(
            "SIN",
            config=config_with_deadline(100.0),
            client=client,
        )

    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.retryable is True
    assert client.get_calls == []
    assert client.post_calls == []


def test_attempt_deadline_bounds_poll_get_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([100.0, 101.0, 102.0, 103.0])
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(times))
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload={"context": {"status": "incomplete", "sessionId": "s1"}}),
            FakeResponse(payload=search_payload()),
        ]
    )

    adapter.SkyscannerAdapter(
        config=config_with_deadline(105.0),
        client=client,
    ).search_exact_one_way(
        ProviderExactOneWayRequest(
            origin="SIN",
            destination="SGN",
            departure_date="2026-06-11",
        )
    )

    assert [call["timeout"] for call in client.get_calls] == [5.0, 4.0, 2.0]
    assert [call["timeout"] for call in client.post_calls] == [3.0]


def test_attempt_deadline_bounds_poll_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([100.0, 100.0, 100.0, 100.0, 100.3, 100.6])
    sleep_calls: list[float] = []
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(adapter.time, "sleep", sleep_calls.append)
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload={"context": {"status": "incomplete", "sessionId": "s1"}}),
            FakeResponse(payload={"context": {"status": "incomplete", "sessionId": "s1"}}),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(
            config=config_with_deadline(100.5),
            client=client,
        ).search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="SIN",
                destination="SGN",
                departure_date="2026-06-11",
            )
        )

    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.failure_type == "timeout"
    assert sleep_calls == pytest.approx([0.2])
    assert len(client.get_calls) == 3


def test_search_referer_preserves_adult_count_exactly() -> None:
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=search_payload()),
        ]
    )

    adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
        ProviderExactOneWayRequest(
            origin="SIN",
            destination="SGN",
            departure_date="2026-06-11",
            passengers=PassengersV1(adults=2),
        )
    )

    assert "adultsv2=2" in client.post_calls[0]["headers"]["referer"]


def test_get_entity_id_accepts_top_level_autosuggest_list() -> None:
    client = FakeClient(
        [
            FakeResponse(
                payload=[
                    {
                        "iataCode": "SIN",
                        "entityId": "95673375",
                        "name": "Singapore",
                        "type": "PLACE_TYPE_AIRPORT",
                    }
                ]
            )
        ]
    )

    result = adapter.get_entity_id("SIN", config=config(), client=client)

    assert result.entity_id == "95673375"


def test_multisegment_leg_is_skipped_instead_of_misrepresented() -> None:
    payload = search_payload()
    itinerary = payload["itineraries"]["results"][0]
    leg = itinerary["legs"][0]
    leg["segments"].append(
        {
            "origin": {"displayCode": "BKK"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-06-11T12:15:00",
            "arrival": "2026-06-11T14:45:00",
            "durationInMinutes": 150,
            "marketingCarrier": {"displayCode": "VJ", "name": "VietJet"},
            "flightNumber": "816",
        }
    )
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=payload),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="SIN",
                destination="SGN",
                departure_date="2026-06-11",
            )
        )

    assert exc_info.value.failure_type == "no_usable_results"


def test_entity_ambiguous_error_does_not_leak_provider_fields() -> None:
    client = FakeClient(
        [
            FakeResponse(
                payload={
                    "places": [
                        {
                            "iataCode": "SIN",
                            "entityId": "/transport_deeplink/secret",
                            "name": "__Secure-anon_token=secret",
                            "type": "PLACE_TYPE_AIRPORT challenge",
                        },
                        {
                            "iataCode": "SIN",
                            "entityId": "sessionId",
                            "name": "raw payload text",
                            "type": "PLACE_TYPE_AIRPORT",
                        },
                    ]
                }
            )
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.get_entity_id("SIN", config=config(), client=client)

    assert exc_info.value.failure_type == "entity_ambiguous"
    assert_error_is_sanitized(exc_info.value)


def test_round_trip_one_leg_payload_is_skipped_as_no_usable_results() -> None:
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=search_payload()),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(config=config(), client=client).search_exact_round_trip(
            ProviderExactRoundTripRequest(
                origin="SIN",
                destination="SGN",
                departure_date="2026-06-11",
                return_date="2026-06-18",
            )
        )

    assert exc_info.value.failure_type == "no_usable_results"
    assert_error_is_sanitized(exc_info.value)


def test_one_way_wrong_route_payload_is_skipped_as_no_usable_results() -> None:
    payload = search_payload()
    leg = payload["itineraries"]["results"][0]["legs"][0]
    leg["origin"]["displayCode"] = "HAN"
    leg["segments"][0]["origin"]["displayCode"] = "HAN"
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=payload),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="SIN",
                destination="SGN",
                departure_date="2026-06-11",
            )
        )

    assert exc_info.value.failure_type == "no_usable_results"
    assert_error_is_sanitized(exc_info.value)


def test_segment_route_mismatch_is_skipped_as_no_usable_results() -> None:
    payload = search_payload()
    segment = payload["itineraries"]["results"][0]["legs"][0]["segments"][0]
    segment["origin"]["displayCode"] = "HAN"
    segment["departure"] = "2026-06-11T08:00:00"
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=payload),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="SIN",
                destination="SGN",
                departure_date="2026-06-11",
            )
        )

    assert exc_info.value.failure_type == "no_usable_results"
    assert_error_is_sanitized(exc_info.value)


def test_itinerary_without_transport_deeplink_is_no_usable_results() -> None:
    payload = search_payload()
    pricing_option = payload["itineraries"]["results"][0]["pricingOptions"][0]
    pricing_option["items"] = [{"url": "https://example.com/not-a-deeplink"}]
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=payload),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="SIN",
                destination="SGN",
                departure_date="2026-06-11",
            )
        )

    assert exc_info.value.failure_type == "no_usable_results"
    assert_error_is_sanitized(exc_info.value)


def test_http_403_maps_to_blocked_error() -> None:
    client = FakeClient([FakeResponse(status_code=403, payload={"error": "blocked"})])

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.get_entity_id("SIN", config=config(), client=client)

    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.http_status_code == 403
    assert_error_is_sanitized(exc_info.value)


def test_search_http_429_maps_to_rate_limited_error() -> None:
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(
                status_code=429,
                payload={"error": "/transport_deeplink/secret challenge sessionId"},
            ),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="SIN",
                destination="SGN",
                departure_date="2026-06-11",
            )
        )

    assert exc_info.value.error_code == ErrorCode.PROVIDER_RATE_LIMITED
    assert exc_info.value.failure_type == "rate_limited"
    assert exc_info.value.retryable is True
    assert exc_info.value.http_status_code == 429
    assert_error_is_sanitized(exc_info.value)


def test_round_trip_includes_return_leg_and_uses_passenger_count() -> None:
    payload = search_payload()
    outbound_leg = payload["itineraries"]["results"][0]["legs"][0]
    payload["itineraries"]["results"][0]["legs"].append(
        {
            **outbound_leg,
            "origin": {"displayCode": "SGN"},
            "destination": {"displayCode": "SIN"},
            "departure": "2026-06-18T09:15:00",
            "arrival": "2026-06-18T10:45:00",
            "segments": [
                {
                    **outbound_leg["segments"][0],
                    "origin": {"displayCode": "SGN"},
                    "destination": {"displayCode": "SIN"},
                    "departure": "2026-06-18T09:15:00",
                    "arrival": "2026-06-18T10:45:00",
                }
            ],
        }
    )
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=payload),
        ]
    )

    adapter.SkyscannerAdapter(config=config(), client=client).search_exact_round_trip(
        ProviderExactRoundTripRequest(
            origin="SIN",
            destination="SGN",
            departure_date="2026-06-11",
            return_date="2026-06-18",
        )
    )

    body = client.post_calls[0]["json"]
    assert isinstance(body, dict)
    assert body["adults"] == 1
    assert body["childAges"] == []
    assert len(body["legs"]) == 2
    assert body["legs"][1]["legOrigin"]["entityId"] == "95673379"
    assert body["legs"][1]["legDestination"]["entityId"] == "95673375"


def test_config_from_env_missing_cookie_is_sanitized() -> None:
    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.config_from_env({})

    assert exc_info.value.failure_type == "missing_cookie"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is False
    text = json.dumps(exc_info.value.__dict__, sort_keys=True, default=str)
    assert "__Secure-anon_token" not in text
    assert "secret" not in text
    assert "/transport_deeplink/" not in text


def test_malformed_json_maps_to_parse_error_without_payload() -> None:
    client = FakeClient([FakeResponse(json_error=ValueError("secret payload"))])

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.get_entity_id("SIN", config=config(), client=client)

    assert exc_info.value.failure_type == "parse_error"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.exception_type == "ValueError"
    assert_error_is_sanitized(exc_info.value)
