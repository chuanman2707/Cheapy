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


def test_get_entity_id_maps_no_match_to_entity_not_found() -> None:
    client = FakeClient(FakeResponse(payload={"places": []}))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "entity_not_found"


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


def test_get_entity_id_maps_transport_error() -> None:
    client = FakeClient(RuntimeError("transport token secret"))

    with pytest.raises(probe.ProbeError) as exc_info:
        probe.get_entity_id("HAN", config=config(), client=client)

    assert exc_info.value.code == "autosuggest_transport_error"
    assert "transport token secret" not in exc_info.value.message
