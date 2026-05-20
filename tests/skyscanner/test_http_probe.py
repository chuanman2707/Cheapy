from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def load_probe():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "skyscanner_http_probe.py"
    spec = importlib.util.spec_from_file_location("skyscanner_http_probe", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["skyscanner_http_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = load_probe()


def test_normalize_iata_uppercases_and_strips() -> None:
    assert probe.normalize_iata(" han ") == "HAN"


@pytest.mark.parametrize("value", ["", "HA", "HANO", "H1N", "h@n"])
def test_normalize_iata_rejects_invalid_values(value: str) -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.normalize_iata(value)

    assert exc_info.value.code == "invalid_argument"


def test_date_parts_validates_and_formats_date() -> None:
    assert probe.date_parts("2026-06-11") == {
        "@type": "date",
        "year": "2026",
        "month": "06",
        "day": "11",
    }


@pytest.mark.parametrize("value", ["2026-6-11", "2026-02-30", "11-06-2026"])
def test_date_parts_rejects_invalid_dates(value: str) -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.date_parts(value)

    assert exc_info.value.code == "invalid_argument"


def test_require_cookie_rejects_missing_cookie() -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.require_cookie({"CHEAPY_SKYSCANNER_COOKIE": ""})

    assert exc_info.value.code == "missing_cookie"
    assert "cookie" in exc_info.value.message.lower()


def test_default_config_from_env_uses_safe_defaults() -> None:
    config = probe.config_from_env(
        {"CHEAPY_SKYSCANNER_COOKIE": "abgroup=1; __Secure-anon_token=secret"},
        market="SG",
        locale="en-GB",
        currency="SGD",
    )

    assert config.base_url == "https://www.skyscanner.com.sg"
    assert config.market == "SG"
    assert config.locale == "en-GB"
    assert config.currency == "SGD"
    assert config.cookie.startswith("abgroup=1")
    assert config.timeout_seconds == 20.0


def test_config_repr_redacts_cookie() -> None:
    config = probe.config_from_env(
        {"CHEAPY_SKYSCANNER_COOKIE": "abgroup=1; __Secure-anon_token=secret"},
        market="SG",
        locale="en-GB",
        currency="SGD",
    )

    text = repr(config)

    assert "__Secure-anon_token" not in text
    assert "secret" not in text
    assert "cookie" not in text


def test_main_rejects_return_date_before_departure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_SKYSCANNER_COOKIE", "abgroup=1")

    result = probe.main(
        [
            "--origin",
            "HAN",
            "--destination",
            "SIN",
            "--departure-date",
            "2026-06-11",
            "--return-date",
            "2026-06-10",
        ]
    )

    assert result == 1


def test_main_rejects_non_positive_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_SKYSCANNER_COOKIE", "abgroup=1")

    result = probe.main(
        [
            "--origin",
            "HAN",
            "--destination",
            "SIN",
            "--departure-date",
            "2026-06-11",
            "--limit",
            "0",
        ]
    )

    assert result == 1


class FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: object = None, json_error: Exception | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.get_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def get(self, url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
        self.get_calls.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def post(self, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
        self.post_calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def config(cookie: str = "traveller_context=abc; __Secure-anon_token=secret") -> object:
    return probe.ProbeConfig(
        base_url="https://www.skyscanner.com.sg",
        market="SG",
        locale="en-GB",
        currency="SGD",
        cookie=cookie,
        timeout_seconds=7.0,
    )


def test_get_entity_id_encodes_path_components_with_slashes() -> None:
    client = FakeClient(
        FakeResponse(
            payload={
                "places": [
                    {
                        "iataCode": "HAN",
                        "entityId": "128668079",
                        "name": "Hanoi",
                        "type": "PLACE_TYPE_AIRPORT",
                    }
                ]
            }
        )
    )
    slash_config = probe.ProbeConfig(
        base_url="https://www.skyscanner.com.sg",
        market="S/G",
        locale="en/GB",
        currency="SGD",
        cookie="traveller_context=abc",
        timeout_seconds=7.0,
    )

    result = probe.get_entity_id("HAN", config=slash_config, client=client)

    assert result.entity_id == "128668079"
    assert client.get_calls[0]["url"] == (
        "https://www.skyscanner.com.sg/g/autosuggest-search/api/v1/search-flight/S%2FG/en%2FGB/HAN"
    )


def test_get_entity_id_resolves_web_style_airport_and_parent() -> None:
    client = FakeClient(
        FakeResponse(
            payload={
                "Places": [
                    {
                        "IataCode": "HAN",
                        "EntityId": "128668079",
                        "PlaceName": "Hanoi",
                        "PlaceType": "Airport",
                        "CityId": "27542680",
                    }
                ]
            }
        )
    )

    result = probe.get_entity_id(
        "han",
        config=config(),
        client=client,
        is_destination=True,
    )

    assert result == probe.EntityResult(
        iata="HAN",
        entity_id="128668079",
        name="Hanoi",
        place_type="Airport",
        parent_entity_id="27542680",
        place_of_stay_entity_id="27542680",
    )
    assert client.get_calls[0]["url"] == (
        "https://www.skyscanner.com.sg/g/autosuggest-search/api/v1/search-flight/SG/en-GB/HAN"
    )
    assert client.get_calls[0]["params"] == {
        "isDestination": "true",
        "enable_general_search_v2": "false",
    }
    assert client.get_calls[0]["headers"]["x-skyscanner-market"] == "SG"


def test_get_entity_id_resolves_partner_style_airport() -> None:
    client = FakeClient(
        FakeResponse(
            payload={
                "places": [
                    {
                        "iataCode": "SGN",
                        "entityId": "95673379",
                        "name": "Ho Chi Minh City",
                        "type": "PLACE_TYPE_AIRPORT",
                        "parentId": "27546329",
                    }
                ]
            }
        )
    )

    result = probe.get_entity_id("SGN", config=config(), client=client)

    assert result.iata == "SGN"
    assert result.entity_id == "95673379"
    assert result.parent_entity_id == "27546329"
    assert result.place_of_stay_entity_id is None


def test_get_entity_id_resolves_live_top_level_autosuggest_list() -> None:
    client = FakeClient(
        FakeResponse(
            payload=[
                {
                    "PlaceId": "SGNS",
                    "PlaceName": "Ho Chi Minh City",
                    "IataCode": "SGN",
                    "GeoId": "27546329",
                    "GeoContainerId": "27546329",
                    "CityId": "SGNS",
                },
                {
                    "PlaceId": "SGN",
                    "PlaceName": "Ho Chi Minh City",
                    "IataCode": "",
                    "GeoId": "95673379",
                    "GeoContainerId": "27546329",
                    "CityId": "SGNS",
                },
            ]
        )
    )

    result = probe.get_entity_id(
        "SGN",
        config=config(),
        client=client,
        is_destination=True,
    )

    assert result == probe.EntityResult(
        iata="SGN",
        entity_id="95673379",
        name="Ho Chi Minh City",
        place_type="Airport",
        parent_entity_id="27546329",
        place_of_stay_entity_id="27546329",
    )


def test_get_entity_id_maps_no_match_to_entity_not_found() -> None:
    client = FakeClient(FakeResponse(payload={"places": []}))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "entity_not_found"


@pytest.mark.parametrize("payload", [{}, {"places": {}}, {"Places": "not a list"}])
def test_get_entity_id_maps_missing_or_non_list_places_to_parse_error(payload: object) -> None:
    client = FakeClient(FakeResponse(payload=payload))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "autosuggest_parse_error"


def test_get_entity_id_maps_ambiguous_airports() -> None:
    client = FakeClient(
        FakeResponse(
            payload={
                "places": [
                    {"iataCode": "HAN", "entityId": "1", "name": "Hanoi A", "type": "PLACE_TYPE_AIRPORT"},
                    {"iataCode": "HAN", "entityId": "2", "name": "Hanoi B", "type": "PLACE_TYPE_AIRPORT"},
                ]
            }
        )
    )

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "entity_ambiguous"
    assert "Hanoi A" in exc_info.value.message
    assert "secret" not in exc_info.value.message


def test_get_entity_id_maps_http_error() -> None:
    client = FakeClient(FakeResponse(status_code=403, payload={"error": "blocked"}))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "autosuggest_http_error"


def test_get_entity_id_maps_invalid_json() -> None:
    client = FakeClient(FakeResponse(json_error=ValueError("raw secret body")))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "autosuggest_parse_error"
    assert "raw secret body" not in exc_info.value.message
    assert exc_info.value.__cause__ is None


def test_get_entity_id_maps_transport_error() -> None:
    client = FakeClient(RuntimeError("transport token secret"))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "autosuggest_transport_error"
    assert "transport token secret" not in exc_info.value.message
    assert exc_info.value.__cause__ is None


def entity(
    iata: str,
    entity_id: str,
    *,
    place_of_stay_entity_id: str | None = None,
) -> object:
    return probe.EntityResult(
        iata=iata,
        entity_id=entity_id,
        name=iata,
        place_type="Airport",
        parent_entity_id=place_of_stay_entity_id,
        place_of_stay_entity_id=place_of_stay_entity_id,
    )


def test_build_search_body_maps_one_way_without_place_of_stay() -> None:
    body = probe.build_search_body(
        origin=entity("HAN", "128668079"),
        destination=entity("SGN", "95673379"),
        departure_date="2026-06-11",
        return_date=None,
    )

    assert body["cabinClass"] == "ECONOMY"
    assert body["adults"] == 1
    assert len(body["legs"]) == 1
    assert body["legs"][0]["legOrigin"]["entityId"] == "128668079"
    assert body["legs"][0]["legDestination"]["entityId"] == "95673379"
    assert "placeOfStay" not in body["legs"][0]


def test_build_search_body_maps_round_trip_with_place_of_stay() -> None:
    body = probe.build_search_body(
        origin=entity("SIN", "95673375"),
        destination=entity("SGN", "95673379", place_of_stay_entity_id="27546329"),
        departure_date="2026-06-11",
        return_date="2026-06-16",
    )

    assert len(body["legs"]) == 2
    assert body["legs"][0]["placeOfStay"] == "27546329"
    assert body["legs"][1]["legOrigin"]["entityId"] == "95673379"
    assert body["legs"][1]["legDestination"]["entityId"] == "95673375"
    assert body["legs"][1]["dates"] == {
        "@type": "date",
        "year": "2026",
        "month": "06",
        "day": "16",
    }


def test_build_search_body_rejects_return_before_departure() -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.build_search_body(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date="2026-06-10",
        )

    assert exc_info.value.code == "invalid_argument"


def test_build_search_body_maps_invalid_return_date_to_probe_error() -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.build_search_body(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date="2026-02-30",
        )

    assert exc_info.value.code == "invalid_argument"


def test_search_posts_minimal_headers_and_uuid_view_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(FakeResponse(payload={"context": {"status": "complete"}, "itineraries": {"results": []}}))
    monkeypatch.setattr(probe.uuid, "uuid4", lambda: "11111111-2222-4333-8444-555555555555")

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.fetch_flights(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date=None,
            config=config(),
            client=client,
        )

    assert exc_info.value.code == "no_usable_results"
    post = client.post_calls[0]
    assert post["url"] == "https://www.skyscanner.com.sg/g/radar/api/v2/web-unified-search/"
    assert post["headers"] == {
        "cookie": "traveller_context=abc; __Secure-anon_token=secret",
        "x-skyscanner-channelid": "website",
        "x-skyscanner-currency": "SGD",
        "x-skyscanner-locale": "en-GB",
        "x-skyscanner-market": "SG",
        "content-type": "application/json",
        "x-skyscanner-viewid": "11111111-2222-4333-8444-555555555555",
    }


def test_fetch_flights_maps_search_http_error() -> None:
    client = FakeClient(FakeResponse(status_code=429, payload={}))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.fetch_flights(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date=None,
            config=config(),
            client=client,
        )

    assert exc_info.value.code == "search_http_error"


def test_fetch_flights_maps_search_invalid_json() -> None:
    client = FakeClient(FakeResponse(json_error=ValueError("jwt secret body")))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.fetch_flights(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date=None,
            config=config(),
            client=client,
        )

    assert exc_info.value.code == "search_parse_error"
    assert "jwt secret body" not in exc_info.value.message
    assert exc_info.value.__cause__ is None


def test_fetch_flights_maps_incomplete_status() -> None:
    client = FakeClient(
        FakeResponse(
            payload={
                "context": {"status": "pending token-secret-body"},
                "itineraries": {"results": []},
            }
        )
    )

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.fetch_flights(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date=None,
            config=config(),
            client=client,
        )

    assert exc_info.value.code == "search_incomplete"
    assert "token-secret-body" not in exc_info.value.message


def test_fetch_flights_maps_missing_results_path() -> None:
    client = FakeClient(FakeResponse(payload={"context": {"status": "complete"}, "itineraries": {}}))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.fetch_flights(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date=None,
            config=config(),
            client=client,
        )

    assert exc_info.value.code == "search_parse_error"


def itinerary(
    *,
    price: float,
    option_amount: float,
    url: str | None,
    carrier: str = "VJ",
) -> dict[str, object]:
    item: dict[str, object] = {"price": {"amount": option_amount}}
    if url is not None:
        item["url"] = url
    return {
        "id": f"itinerary-{price}",
        "price": {"raw": price, "formatted": f"${price}"},
        "legs": [
            {
                "stopCount": 0,
                "segments": [
                    {
                        "marketingCarrier": {
                            "displayCode": carrier,
                            "name": carrier,
                        }
                    }
                ],
            }
        ],
        "pricingOptions": [
            {
                "price": {"amount": option_amount},
                "items": [item],
            }
        ],
    }


def search_payload(results: list[dict[str, object]]) -> dict[str, object]:
    return {"context": {"status": "complete"}, "itineraries": {"results": results}}


def test_fetch_flights_extracts_sorted_fares_and_absolute_deeplinks() -> None:
    client = FakeClient(
        FakeResponse(
            payload=search_payload(
                [
                    itinerary(price=300.0, option_amount=300.0, url="/transport_deeplink/expensive", carrier="SQ"),
                    itinerary(price=220.96, option_amount=220.96, url="/transport_deeplink/cheap", carrier="VJ"),
                ]
            )
        )
    )

    results = probe.fetch_flights(
        origin=entity("SIN", "95673375"),
        destination=entity("SGN", "95673379"),
        departure_date="2026-06-11",
        return_date="2026-06-16",
        config=config(),
        client=client,
    )

    assert results == [
        probe.FlightProbeResult(
            airline="VJ",
            price_amount=220.96,
            currency="SGD",
            deeplink_url="https://www.skyscanner.com.sg/transport_deeplink/cheap",
        ),
        probe.FlightProbeResult(
            airline="SQ",
            price_amount=300.0,
            currency="SGD",
            deeplink_url="https://www.skyscanner.com.sg/transport_deeplink/expensive",
        ),
    ]


def test_fetch_flights_ignores_zero_amount_options_and_missing_deeplinks() -> None:
    client = FakeClient(
        FakeResponse(
            payload=search_payload(
                [
                    itinerary(price=100.0, option_amount=0.0, url="/transport_deeplink/free"),
                    itinerary(price=120.0, option_amount=120.0, url=None),
                    itinerary(price=130.0, option_amount=130.0, url="https://www.skyscanner.com.sg/transport_deeplink/usable"),
                ]
            )
        )
    )

    results = probe.fetch_flights(
        origin=entity("SIN", "95673375"),
        destination=entity("SGN", "95673379"),
        departure_date="2026-06-11",
        return_date=None,
        config=config(),
        client=client,
    )

    assert len(results) == 1
    assert results[0].price_amount == 130.0
    assert results[0].deeplink_url == "https://www.skyscanner.com.sg/transport_deeplink/usable"


def test_fetch_flights_skips_hostile_absolute_and_protocol_deeplinks() -> None:
    client = FakeClient(
        FakeResponse(
            payload=search_payload(
                [
                    itinerary(price=100.0, option_amount=100.0, url="https://evil.example/transport_deeplink/steal"),
                    itinerary(price=110.0, option_amount=110.0, url="//evil.example/transport_deeplink/steal"),
                    itinerary(price=120.0, option_amount=120.0, url="javascript:alert(1)"),
                    itinerary(price=130.0, option_amount=130.0, url="/transport_deeplink/usable"),
                ]
            )
        )
    )

    results = probe.fetch_flights(
        origin=entity("SIN", "95673375"),
        destination=entity("SGN", "95673379"),
        departure_date="2026-06-11",
        return_date=None,
        config=config(),
        client=client,
    )

    assert results == [
        probe.FlightProbeResult(
            airline="VJ",
            price_amount=130.0,
            currency="SGD",
            deeplink_url="https://www.skyscanner.com.sg/transport_deeplink/usable",
        )
    ]


def test_fetch_flights_accepts_same_origin_absolute_deeplink() -> None:
    client = FakeClient(
        FakeResponse(
            payload=search_payload(
                [
                    itinerary(
                        price=130.0,
                        option_amount=130.0,
                        url="https://www.skyscanner.com.sg/transport_deeplink/usable",
                    )
                ]
            )
        )
    )

    results = probe.fetch_flights(
        origin=entity("SIN", "95673375"),
        destination=entity("SGN", "95673379"),
        departure_date="2026-06-11",
        return_date=None,
        config=config(),
        client=client,
    )

    assert results[0].deeplink_url == "https://www.skyscanner.com.sg/transport_deeplink/usable"


def test_fetch_flights_accepts_relative_transport_deeplink() -> None:
    client = FakeClient(
        FakeResponse(
            payload=search_payload(
                [
                    itinerary(price=130.0, option_amount=130.0, url="/transport_deeplink/usable"),
                ]
            )
        )
    )

    results = probe.fetch_flights(
        origin=entity("SIN", "95673375"),
        destination=entity("SGN", "95673379"),
        departure_date="2026-06-11",
        return_date=None,
        config=config(),
        client=client,
    )

    assert results[0].deeplink_url == "https://www.skyscanner.com.sg/transport_deeplink/usable"


def test_fetch_flights_skips_itinerary_when_cheapest_positive_option_has_no_deeplink() -> None:
    fallback_itinerary = itinerary(
        price=120.0,
        option_amount=120.0,
        url=None,
        carrier="SQ",
    )
    fallback_itinerary["pricingOptions"] = [
        {
            "price": {"amount": 120.0},
            "items": [{"price": {"amount": 120.0}}],
        },
        {
            "price": {"amount": 150.0},
            "items": [
                {
                    "price": {"amount": 150.0},
                    "url": "/transport_deeplink/more-expensive",
                }
            ],
        },
    ]
    client = FakeClient(
        FakeResponse(
            payload=search_payload(
                [
                    fallback_itinerary,
                    itinerary(price=180.0, option_amount=180.0, url="/transport_deeplink/usable", carrier="VJ"),
                ]
            )
        )
    )

    results = probe.fetch_flights(
        origin=entity("SIN", "95673375"),
        destination=entity("SGN", "95673379"),
        departure_date="2026-06-11",
        return_date=None,
        config=config(),
        client=client,
    )

    assert results == [
        probe.FlightProbeResult(
            airline="VJ",
            price_amount=180.0,
            currency="SGD",
            deeplink_url="https://www.skyscanner.com.sg/transport_deeplink/usable",
        )
    ]


def test_print_results_respects_limit(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        probe.FlightProbeResult("VJ", 220.96, "SGD", "https://example.test/1"),
        probe.FlightProbeResult("SQ", 300.0, "SGD", "https://example.test/2"),
    ]

    probe.print_results(results, limit=1)

    captured = capsys.readouterr()
    assert captured.out == "1. VJ | 220.96 SGD | https://example.test/1\n"
    assert captured.err == ""


def test_print_results_rejects_non_positive_limit() -> None:
    results = [
        probe.FlightProbeResult("VJ", 220.96, "SGD", "https://example.test/1"),
    ]

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.print_results(results, limit=0)

    assert exc_info.value.code == "invalid_argument"


def test_main_prints_safe_error_for_missing_cookie(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHEAPY_SKYSCANNER_COOKIE", raising=False)

    exit_code = probe.main(
        [
            "--origin",
            "SIN",
            "--destination",
            "SGN",
            "--departure-date",
            "2026-06-11",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == (
        "missing_cookie: Set CHEAPY_SKYSCANNER_COOKIE before running the Skyscanner probe.\n"
    )
    assert "__Secure-anon_token" not in captured.err


def test_run_probe_rejects_non_positive_limit_before_network_calls() -> None:
    class NoNetworkClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
            self.calls.append("get")
            raise AssertionError("get should not be called")

        def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> FakeResponse:
            self.calls.append("post")
            raise AssertionError("post should not be called")

    client = NoNetworkClient()

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.run_probe(
            origin_iata="SIN",
            destination_iata="SGN",
            departure_date="2026-06-11",
            return_date=None,
            limit=0,
            config=config(),
            client=client,
        )

    assert exc_info.value.code == "invalid_argument"
    assert client.calls == []


def test_run_probe_resolves_entities_and_prints_results(capsys: pytest.CaptureFixture[str]) -> None:
    class ScriptedClient:
        def __init__(self) -> None:
            self.responses = [
                FakeResponse(
                    payload={
                        "places": [
                            {
                                "iataCode": "SIN",
                                "entityId": "95673375",
                                "name": "Singapore Changi",
                                "type": "PLACE_TYPE_AIRPORT",
                            }
                        ]
                    }
                ),
                FakeResponse(
                    payload={
                        "places": [
                            {
                                "iataCode": "SGN",
                                "entityId": "95673379",
                                "name": "Ho Chi Minh City",
                                "type": "PLACE_TYPE_AIRPORT",
                                "parentId": "27546329",
                            }
                        ]
                    }
                ),
                FakeResponse(
                    payload=search_payload(
                        [
                            itinerary(
                                price=220.96,
                                option_amount=220.96,
                                url="/transport_deeplink/cheap",
                                carrier="VJ",
                            )
                        ]
                    )
                ),
            ]

        def get(self, url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
            return self.responses.pop(0)

        def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> FakeResponse:
            return self.responses.pop(0)

    exit_code = probe.run_probe(
        origin_iata="SIN",
        destination_iata="SGN",
        departure_date="2026-06-11",
        return_date="2026-06-16",
        limit=3,
        config=config(),
        client=ScriptedClient(),
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == (
        "1. VJ | 220.96 SGD | https://www.skyscanner.com.sg/transport_deeplink/cheap\n"
    )
    assert captured.err == ""


def test_fetch_flights_maps_no_usable_results() -> None:
    client = FakeClient(
        FakeResponse(
            payload=search_payload(
                [
                    itinerary(price=0.0, option_amount=0.0, url="/transport_deeplink/free"),
                    itinerary(price=120.0, option_amount=120.0, url=None),
                ]
            )
        )
    )

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.fetch_flights(
            origin=entity("SIN", "95673375"),
            destination=entity("SGN", "95673379"),
            departure_date="2026-06-11",
            return_date=None,
            config=config(),
            client=client,
        )

    assert exc_info.value.code == "no_usable_results"
