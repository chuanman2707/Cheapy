from __future__ import annotations

from dataclasses import dataclass

import pytest

from cheapy.providers.skyscanner import search


@dataclass(frozen=True)
class FakeResponse:
    status_code: int = 200
    payload: object = None

    def json(self) -> object:
        return self.payload if self.payload is not None else {}


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> FakeResponse:
        self.calls.append(
            {"method": "GET", "url": url, "params": params, "headers": headers}
        )
        return self.responses.pop(0)

    def post_json(
        self,
        url: str,
        *,
        json_body: dict[str, object],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> FakeResponse:
        self.calls.append(
            {"method": "POST", "url": url, "json_body": json_body, "headers": headers}
        )
        return self.responses.pop(0)


def _config() -> search.SkyscannerConfig:
    return search.SkyscannerConfig(
        base_url="https://www.skyscanner.com.sg",
        market="SG",
        locale="en-GB",
        currency="SGD",
        cookie="traveller_context=abc; X-Gateway-Servedby=gw52.skyscanner.net",
        user_agent="Browserless-UA",
        timeout_seconds=20.0,
    )


def _entity(iata: str, entity_id: str) -> search.EntityResult:
    return search.EntityResult(
        iata=iata,
        entity_id=entity_id,
        name=iata,
        place_type="Airport",
        parent_entity_id="city-" + iata,
        place_of_stay_entity_id="city-" + iata,
    )


def _payload(
    *, amount: float = 220.96, url: str = "/transport_deeplink/cheap"
) -> dict[str, object]:
    return {
        "context": {"status": "complete"},
        "itineraries": {
            "results": [
                {
                    "price": {"raw": amount},
                    "pricingOptions": [
                        {"price": {"amount": amount}, "items": [{"url": url}]}
                    ],
                    "legs": [
                        {
                            "origin": {"displayCode": "SGN"},
                            "destination": {"displayCode": "BKK"},
                            "departure": "2026-07-10T09:00:00",
                            "arrival": "2026-07-10T10:30:00",
                            "durationInMinutes": 90,
                            "segments": [
                                {
                                    "origin": {"displayCode": "SGN"},
                                    "destination": {"displayCode": "BKK"},
                                    "departure": "2026-07-10T09:00:00",
                                    "arrival": "2026-07-10T10:30:00",
                                    "durationInMinutes": 90,
                                    "marketingCarrier": {"displayCode": "VJ"},
                                    "flightNumber": "801",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    }


def _first_result(payload: dict[str, object]) -> dict[str, object]:
    itineraries = payload["itineraries"]
    assert isinstance(itineraries, dict)
    results = itineraries["results"]
    assert isinstance(results, list)
    result = results[0]
    assert isinstance(result, dict)
    return result


def _first_segment(payload: dict[str, object]) -> dict[str, object]:
    result = _first_result(payload)
    legs = result["legs"]
    assert isinstance(legs, list)
    leg = legs[0]
    assert isinstance(leg, dict)
    segments = leg["segments"]
    assert isinstance(segments, list)
    segment = segments[0]
    assert isinstance(segment, dict)
    return segment


def test_get_entity_resolves_airport() -> None:
    client = FakeClient(
        [
            FakeResponse(
                payload={
                    "places": [
                        {
                            "iataCode": "SGN",
                            "entityId": "95673379",
                            "name": "Ho Chi Minh City",
                            "placeType": "Airport",
                            "parentId": "city-sgn",
                        }
                    ]
                }
            )
        ]
    )

    entity = search.get_entity("SGN", config=_config(), client=client, is_destination=True)

    assert entity.iata == "SGN"
    assert entity.entity_id == "95673379"
    assert entity.place_of_stay_entity_id == "city-sgn"


def test_build_search_body_maps_round_trip() -> None:
    body = search.build_search_body(
        origin=_entity("SGN", "origin-id"),
        destination=_entity("BKK", "destination-id"),
        departure_date="2026-07-10",
        return_date="2026-07-17",
        adults=1,
    )

    assert body["adults"] == 1
    assert len(body["legs"]) == 2
    assert body["legs"][0]["legOrigin"] == {"@type": "entity", "entityId": "origin-id"}
    assert body["legs"][1]["legOrigin"] == {
        "@type": "entity",
        "entityId": "destination-id",
    }


def test_search_itineraries_polls_and_keeps_initial_non_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search, "sleep_between_polls", lambda: None)
    initial = _payload(amount=220.96)
    initial["context"] = {"status": "incomplete", "sessionId": "session/id=secret"}
    empty_complete = {"context": {"status": "complete"}, "itineraries": {"results": []}}
    client = FakeClient([FakeResponse(payload=initial), FakeResponse(payload=empty_complete)])

    payload = search.fetch_search_payload(
        origin=_entity("SGN", "origin-id"),
        destination=_entity("BKK", "destination-id"),
        departure_date="2026-07-10",
        return_date=None,
        config=_config(),
        client=client,
    )

    assert payload == initial
    assert client.calls[1]["url"].endswith("/web-unified-search/session%2Fid%3Dsecret")


def test_fetch_search_payload_passes_adult_count_to_body() -> None:
    client = FakeClient([FakeResponse(payload=_payload())])

    search.fetch_search_payload(
        origin=_entity("SGN", "origin-id"),
        destination=_entity("BKK", "destination-id"),
        departure_date="2026-07-10",
        return_date=None,
        config=_config(),
        client=client,
        adults=3,
    )

    assert client.calls[0]["json_body"]["adults"] == 3


def test_extract_usable_itineraries_requires_same_origin_deeplink() -> None:
    payload = _payload(url="https://evil.example/transport_deeplink/steal")

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


@pytest.mark.parametrize(
    "url",
    [
        "//evil.example/transport_deeplink/steal",
        "javascript:alert(1)",
        "/not_transport_deeplink/steal",
        "https://[bad/transport_deeplink/x",
    ],
)
def test_extract_usable_itineraries_rejects_other_unsafe_deeplinks(url: str) -> None:
    payload = _payload(url=url)

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_skips_segment_missing_required_field() -> None:
    payload = _payload()
    results = payload["itineraries"]["results"]
    assert isinstance(results, list)
    segment = results[0]["legs"][0]["segments"][0]
    del segment["arrival"]

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_skips_empty_segments() -> None:
    payload = _payload()
    results = payload["itineraries"]["results"]
    assert isinstance(results, list)
    results[0]["legs"][0]["segments"] = []

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_skips_route_when_any_leg_has_no_segments() -> None:
    payload = _payload()
    result = _first_result(payload)
    result["legs"].append(
        {
            "origin": {"displayCode": "BKK"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-07-17T13:00:00",
            "arrival": "2026-07-17T14:35:00",
            "durationInMinutes": 95,
            "stopCount": 0,
        }
    )

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_skips_entire_route_when_any_segment_malformed() -> None:
    payload = _payload()
    result = _first_result(payload)
    result["legs"] = [
        {
            "origin": {"displayCode": "SGN"},
            "destination": {"displayCode": "BKK"},
            "departure": "2026-07-10T09:00:00",
            "arrival": "2026-07-10T12:35:00",
            "durationInMinutes": 155,
            "segments": [
                {
                    "origin": {"displayCode": "SGN"},
                    "destination": {"displayCode": "SIN"},
                    "departure": "2026-07-10T09:00:00",
                    "arrival": "2026-07-10T10:30:00",
                    "durationInMinutes": 90,
                    "marketingCarrier": {"displayCode": "VJ"},
                    "flightNumber": "801",
                },
                {
                    "origin": {"displayCode": "SIN"},
                    "destination": {"displayCode": "BKK"},
                    "departure": "2026-07-10T11:30:00",
                    "durationInMinutes": 65,
                    "marketingCarrier": {"displayCode": "SQ"},
                    "flightNumber": "712",
                },
            ],
        }
    ]

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_rejects_partial_leg_duration_metadata() -> None:
    payload = _payload()
    result = _first_result(payload)
    result["legs"].append(
        {
            "origin": {"displayCode": "BKK"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-07-17T13:00:00",
            "arrival": "2026-07-17T14:35:00",
            "stopCount": 0,
            "segments": [
                {
                    "origin": {"displayCode": "BKK"},
                    "destination": {"displayCode": "SGN"},
                    "departure": "2026-07-17T13:00:00",
                    "arrival": "2026-07-17T14:35:00",
                    "durationInMinutes": 95,
                    "marketingCarrier": {"displayCode": "TG"},
                    "flightNumber": "550",
                }
            ],
        }
    )

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_rejects_partial_leg_stop_metadata() -> None:
    payload = _payload()
    result = _first_result(payload)
    result["legs"][0]["stopCount"] = 0
    result["legs"].append(
        {
            "origin": {"displayCode": "BKK"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-07-17T13:00:00",
            "arrival": "2026-07-17T14:35:00",
            "durationInMinutes": 95,
            "segments": [
                {
                    "origin": {"displayCode": "BKK"},
                    "destination": {"displayCode": "SGN"},
                    "departure": "2026-07-17T13:00:00",
                    "arrival": "2026-07-17T14:35:00",
                    "durationInMinutes": 95,
                    "marketingCarrier": {"displayCode": "TG"},
                    "flightNumber": "550",
                }
            ],
        }
    )

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


@pytest.mark.parametrize(
    ("duration_minutes", "stop_count"),
    [(0, 0), (-20, 0), (155, -1)],
)
def test_extract_usable_itineraries_skips_invalid_aggregate_duration_or_stops(
    duration_minutes: int, stop_count: int
) -> None:
    payload = _payload()
    result = _first_result(payload)
    result["legs"] = [
        {
            "origin": {"displayCode": "SGN"},
            "destination": {"displayCode": "BKK"},
            "departure": "2026-07-10T09:00:00",
            "arrival": "2026-07-10T12:35:00",
            "durationInMinutes": duration_minutes,
            "stopCount": stop_count,
            "segments": [
                {
                    "origin": {"displayCode": "SGN"},
                    "destination": {"displayCode": "SIN"},
                    "departure": "2026-07-10T09:00:00",
                    "arrival": "2026-07-10T10:30:00",
                    "durationInMinutes": 90,
                    "marketingCarrier": {"displayCode": "VJ"},
                    "flightNumber": "801",
                },
                {
                    "origin": {"displayCode": "SIN"},
                    "destination": {"displayCode": "BKK"},
                    "departure": "2026-07-10T11:30:00",
                    "arrival": "2026-07-10T12:35:00",
                    "durationInMinutes": 65,
                    "marketingCarrier": {"displayCode": "SQ"},
                    "flightNumber": "712",
                },
            ],
        }
    ]

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_uses_selected_pricing_option_amount() -> None:
    payload = _payload(amount=500.0)
    result = _first_result(payload)
    result["pricingOptions"] = [
        {"price": {"amount": 220.96}, "items": [{"url": "/transport_deeplink/cheap"}]},
        {
            "price": {"amount": 300.0},
            "items": [{"url": "/transport_deeplink/expensive"}],
        },
    ]

    itineraries = search.extract_usable_itineraries(payload, config=_config())

    assert itineraries[0].price_amount == 220.96
    assert itineraries[0].deeplink_url.endswith("/transport_deeplink/cheap")


def test_extract_usable_itineraries_maps_multi_leg_segments_duration_and_stops() -> None:
    payload = _payload()
    results = payload["itineraries"]["results"]
    assert isinstance(results, list)
    results[0]["legs"] = [
        {
            "origin": {"displayCode": "SGN"},
            "destination": {"displayCode": "SIN"},
            "departure": "2026-07-10T09:00:00",
            "arrival": "2026-07-10T12:35:00",
            "durationInMinutes": 155,
            "stopCount": 1,
            "segments": [
                {
                    "origin": {"displayCode": "SGN"},
                    "destination": {"displayCode": "SIN"},
                    "departure": "2026-07-10T09:00:00",
                    "arrival": "2026-07-10T10:30:00",
                    "durationInMinutes": 90,
                    "marketingCarrier": {"displayCode": "VJ"},
                    "flightNumber": "801",
                },
                {
                    "origin": {"displayCode": "SIN"},
                    "destination": {"displayCode": "BKK"},
                    "departure": "2026-07-10T11:30:00",
                    "arrival": "2026-07-10T12:35:00",
                    "durationInMinutes": 65,
                    "marketingCarrier": {"displayCode": "SQ"},
                    "flightNumber": "712",
                },
            ],
        },
        {
            "origin": {"displayCode": "BKK"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-07-17T13:00:00",
            "arrival": "2026-07-17T14:35:00",
            "durationInMinutes": 95,
            "stopCount": 0,
            "segments": [
                {
                    "origin": {"displayCode": "BKK"},
                    "destination": {"displayCode": "SGN"},
                    "departure": "2026-07-17T13:00:00",
                    "arrival": "2026-07-17T14:35:00",
                    "durationInMinutes": 95,
                    "marketingCarrier": {"displayCode": "TG"},
                    "flightNumber": "550",
                }
            ],
        },
    ]

    results = search.extract_usable_itineraries(payload, config=_config())

    assert len(results) == 1
    itinerary = results[0]
    assert itinerary.total_duration_minutes == 250
    assert itinerary.stops == 1
    assert len(itinerary.segments) == 3
    assert [segment.origin for segment in itinerary.segments] == ["SGN", "SIN", "BKK"]
    assert [segment.destination for segment in itinerary.segments] == ["SIN", "BKK", "SGN"]
    assert [segment.flight_number for segment in itinerary.segments] == [
        "VJ801",
        "SQ712",
        "TG550",
    ]


def test_extract_usable_itineraries_counts_stops_per_leg_without_stop_metadata() -> None:
    payload = _payload()
    result = _first_result(payload)
    result["legs"] = [
        {
            "origin": {"displayCode": "SGN"},
            "destination": {"displayCode": "BKK"},
            "departure": "2026-07-10T09:00:00",
            "arrival": "2026-07-10T10:30:00",
            "segments": [
                {
                    "origin": {"displayCode": "SGN"},
                    "destination": {"displayCode": "BKK"},
                    "departure": "2026-07-10T09:00:00",
                    "arrival": "2026-07-10T10:30:00",
                    "durationInMinutes": 90,
                    "marketingCarrier": {"displayCode": "VJ"},
                    "flightNumber": "801",
                }
            ],
        },
        {
            "origin": {"displayCode": "BKK"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-07-17T13:00:00",
            "arrival": "2026-07-17T14:35:00",
            "segments": [
                {
                    "origin": {"displayCode": "BKK"},
                    "destination": {"displayCode": "SGN"},
                    "departure": "2026-07-17T13:00:00",
                    "arrival": "2026-07-17T14:35:00",
                    "durationInMinutes": 95,
                    "marketingCarrier": {"displayCode": "TG"},
                    "flightNumber": "550",
                }
            ],
        },
    ]

    itineraries = search.extract_usable_itineraries(payload, config=_config())

    assert itineraries[0].total_duration_minutes == 185
    assert itineraries[0].stops == 0


def test_extract_usable_itineraries_skips_invalid_segment_timestamps() -> None:
    payload = _payload()
    segment = _first_segment(payload)
    segment["departure"] = "not-a-date-time"

    with pytest.raises(search.NoUsableResults):
        search.extract_usable_itineraries(payload, config=_config())


def test_extract_usable_itineraries_uses_carrier_fallback_without_duplication() -> None:
    payload = _payload()
    results = payload["itineraries"]["results"]
    assert isinstance(results, list)
    segment = results[0]["legs"][0]["segments"][0]
    segment["marketingCarrier"] = {"alternateId": "VJ"}
    segment["flightNumber"] = "VJ801"

    itineraries = search.extract_usable_itineraries(payload, config=_config())

    assert itineraries[0].segments[0].airline_code == "VJ"
    assert itineraries[0].segments[0].flight_number == "VJ801"


def test_get_entity_maps_403_to_blocked() -> None:
    client = FakeClient([FakeResponse(status_code=403, payload={})])

    with pytest.raises(search.SearchError) as exc_info:
        search.get_entity("SGN", config=_config(), client=client)

    assert exc_info.value.code == "blocked"
    assert exc_info.value.failure_type == "blocked"


def test_get_entity_accepts_lowercase_place_id_and_ignores_not_airport_type() -> None:
    client = FakeClient(
        [
            FakeResponse(
                payload={
                    "places": [
                        {
                            "iataCode": "SGN",
                            "placeId": "lowercase-place-id",
                            "name": "False Airport",
                            "placeType": "not_airport",
                        },
                        {
                            "iataCode": "SGN",
                            "placeId": "airport-place-id",
                            "name": "Ho Chi Minh City",
                            "placeType": "Airport",
                        },
                    ]
                }
            )
        ]
    )

    entity = search.get_entity("SGN", config=_config(), client=client)

    assert entity.entity_id == "airport-place-id"


def test_fetch_search_payload_maps_429_to_rate_limited() -> None:
    client = FakeClient([FakeResponse(status_code=429, payload={})])

    with pytest.raises(search.SearchError) as exc_info:
        search.fetch_search_payload(
            origin=_entity("SGN", "origin-id"),
            destination=_entity("BKK", "destination-id"),
            departure_date="2026-07-10",
            return_date=None,
            config=_config(),
            client=client,
        )

    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.failure_type == "rate_limited"


def test_fetch_itineraries_rejects_non_positive_attempts_before_network() -> None:
    client = FakeClient([])

    with pytest.raises(ValueError):
        search.fetch_itineraries(
            origin=_entity("SGN", "origin-id"),
            destination=_entity("BKK", "destination-id"),
            departure_date="2026-07-10",
            return_date=None,
            config=_config(),
            client=client,
            no_usable_results_attempts=0,
        )

    assert client.calls == []


def test_fetch_itineraries_retries_no_usable_results() -> None:
    first = _payload(amount=0.0)
    second = _payload(amount=220.96)
    client = FakeClient([FakeResponse(payload=first), FakeResponse(payload=second)])

    results = search.fetch_itineraries(
        origin=_entity("SGN", "origin-id"),
        destination=_entity("BKK", "destination-id"),
        departure_date="2026-07-10",
        return_date=None,
        config=_config(),
        client=client,
        no_usable_results_attempts=2,
    )

    assert len(results) == 1
    assert results[0].price_amount == 220.96
