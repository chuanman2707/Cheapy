from __future__ import annotations

import json

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
        "cookie",
    ):
        assert token not in text


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


def test_http_403_maps_to_blocked_error() -> None:
    client = FakeClient([FakeResponse(status_code=403, payload={"error": "blocked"})])

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.get_entity_id("SIN", config=config(), client=client)

    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.http_status_code == 403
    assert_no_sensitive_tokens(exc_info.value.__dict__)


def test_round_trip_includes_return_leg_and_uses_passenger_count() -> None:
    client = FakeClient(
        [
            FakeResponse(payload=entity("SIN", "95673375")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=search_payload()),
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
    assert_no_sensitive_tokens(exc_info.value.__dict__)
