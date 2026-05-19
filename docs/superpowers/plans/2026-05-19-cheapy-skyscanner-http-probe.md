# Cheapy Skyscanner HTTP Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone browserless Skyscanner HTTP probe script that resolves entity IDs, performs exact-date search, and prints airline, price, and deep link results.

**Architecture:** Add `scripts/skyscanner_http_probe.py` as an isolated research script with explicit `ProbeConfig`, injected `httpx.Client`, Autosuggest resolution, minimal search POST construction, response extraction, and CLI output. Add offline tests under `tests/skyscanner/test_http_probe.py`; the script must not be imported by Cheapy runtime code or registered as a provider.

**Tech Stack:** Python 3.12, `httpx`, pytest, uv, existing Cheapy repository conventions.

---

## File Structure

- Create `scripts/skyscanner_http_probe.py`: standalone executable probe script with dataclasses, errors, validation, Autosuggest, search request building, result extraction, and CLI entrypoint.
- Create `tests/skyscanner/test_http_probe.py`: offline unit tests using fake HTTP clients and fake responses.
- Modify `pyproject.toml`: add `httpx>=0.28.1` to the `dev` optional dependency list if it is not already a direct dependency.
- Modify `uv.lock`: refresh only if `pyproject.toml` changes.

`scripts/skyscanner_http_probe.py` is intentionally outside `cheapy/` and must not be added to provider manifests, `cheapy.cli`, or MCP code.

## Task 1: Dependency, Skeleton, And Validation

**Files:**
- Create: `scripts/skyscanner_http_probe.py`
- Create: `tests/skyscanner/test_http_probe.py`
- Modify: `pyproject.toml`
- Modify when dependency metadata changes: `uv.lock`

- [ ] **Step 1: Write failing skeleton and validation tests**

Create `tests/skyscanner/test_http_probe.py`:

```python
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
```

- [ ] **Step 2: Run the new test file and verify it fails**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: FAIL with `FileNotFoundError` or `ModuleNotFoundError` for `scripts/skyscanner_http_probe.py`.

- [ ] **Step 3: Add `httpx` as a direct dev dependency if needed**

Inspect `pyproject.toml`. If `httpx>=0.28.1` is not already listed in `[project.optional-dependencies].dev`, add it:

```toml
[project.optional-dependencies]
dev = [
    "hatchling>=1.29.0",
    "httpx>=0.28.1",
    "pytest>=8.3",
]
```

Run:

```bash
uv lock
```

Expected: `uv.lock` updates only if the direct dependency entry changes lock metadata.

- [ ] **Step 4: Create the minimal probe skeleton**

Create `scripts/skyscanner_http_probe.py`:

```python
#!/usr/bin/env python3
"""Browserless Skyscanner HTTP research probe.

This script is intentionally not a Cheapy provider. It resolves Skyscanner
entity IDs, calls the researched web-unified-search endpoint, and prints a
small terminal report for manual inspection.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import os
import re
import sys
from typing import Mapping


DEFAULT_BASE_URL = "https://www.skyscanner.com.sg"
DEFAULT_TIMEOUT_SECONDS = 20.0
IATA_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    market: str
    locale: str
    currency: str
    cookie: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class EntityResult:
    iata: str
    entity_id: str
    name: str
    place_type: str | None = None
    parent_entity_id: str | None = None
    place_of_stay_entity_id: str | None = None


@dataclass(frozen=True)
class FlightProbeResult:
    airline: str
    price_amount: float
    currency: str
    deeplink_url: str


class ProbeError(Exception):
    """Safe, user-facing probe error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def safe_text(self) -> str:
        return f"{self.code}: {self.message}"


def normalize_iata(value: str) -> str:
    iata = value.strip().upper()
    if not IATA_RE.fullmatch(iata):
        raise ProbeError("invalid_argument", "IATA code must be exactly 3 letters.")
    return iata


def date_parts(value: str) -> dict[str, str]:
    if not DATE_RE.fullmatch(value):
        raise ProbeError("invalid_argument", "Date must use YYYY-MM-DD format.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ProbeError("invalid_argument", "Date must use YYYY-MM-DD format.") from exc
    year, month, day = value.split("-")
    return {"@type": "date", "year": year, "month": month, "day": day}


def require_cookie(env: Mapping[str, str]) -> str:
    cookie = env.get("CHEAPY_SKYSCANNER_COOKIE", "").strip()
    if not cookie:
        raise ProbeError(
            "missing_cookie",
            "Set CHEAPY_SKYSCANNER_COOKIE before running the Skyscanner probe.",
        )
    return cookie


def config_from_env(
    env: Mapping[str, str],
    *,
    market: str,
    locale: str,
    currency: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProbeConfig:
    return ProbeConfig(
        base_url=base_url.rstrip("/"),
        market=market,
        locale=locale,
        currency=currency,
        cookie=require_cookie(env),
        timeout_seconds=timeout_seconds,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Skyscanner HTTP search.")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--departure-date", required=True)
    parser.add_argument("--return-date")
    parser.add_argument("--market", default="SG")
    parser.add_argument("--locale", default="en-GB")
    parser.add_argument("--currency", default="SGD")
    parser.add_argument("--limit", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        normalize_iata(args.origin)
        normalize_iata(args.destination)
        date_parts(args.departure_date)
        if args.return_date is not None:
            date_parts(args.return_date)
        config_from_env(
            os.environ,
            market=args.market,
            locale=args.locale,
            currency=args.currency,
        )
    except ProbeError as exc:
        print(exc.safe_text(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests for Task 1**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: PASS for Task 1 tests.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add pyproject.toml uv.lock scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py
git commit -m "test: scaffold Skyscanner HTTP probe"
```

Expected: commit includes only the skeleton script, tests, and dependency metadata touched by this task.

## Task 2: Autosuggest Resolver

**Files:**
- Modify: `scripts/skyscanner_http_probe.py`
- Modify: `tests/skyscanner/test_http_probe.py`

- [ ] **Step 1: Add fake HTTP helpers and Autosuggest tests**

Append to `tests/skyscanner/test_http_probe.py`:

```python
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
```

- [ ] **Step 2: Run Autosuggest tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: FAIL with `AttributeError: module 'skyscanner_http_probe' has no attribute 'get_entity_id'`.

- [ ] **Step 3: Implement Autosuggest helpers and resolver**

Add these imports near the top of `scripts/skyscanner_http_probe.py`:

```python
from collections.abc import Sequence
from urllib.parse import quote

import httpx
```

Add below `config_from_env`:

```python
AUTOSUGGEST_PATH = "/g/autosuggest-search/api/v1/search-flight"


def request_headers(config: ProbeConfig, *, accept_json: bool = True) -> dict[str, str]:
    headers = {
        "cookie": config.cookie,
        "x-skyscanner-channelid": "website",
        "x-skyscanner-currency": config.currency,
        "x-skyscanner-locale": config.locale,
        "x-skyscanner-market": config.market,
    }
    if accept_json:
        headers["accept"] = "application/json"
    return headers


def _field(mapping: object, names: Sequence[str]) -> object | None:
    if not isinstance(mapping, dict):
        return None
    current: object | None
    for name in names:
        if "." in name:
            current = mapping
            for part in name.split("."):
                if not isinstance(current, dict) or part not in current:
                    current = None
                    break
                current = current[part]
            if current is not None:
                return current
        elif name in mapping:
            return mapping[name]
    return None


def _as_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _places_from_payload(payload: object) -> list[object]:
    if not isinstance(payload, dict):
        raise ProbeError(
            "autosuggest_parse_error",
            "Autosuggest response did not contain a JSON object.",
        )
    places = payload.get("places", payload.get("Places"))
    if not isinstance(places, list):
        raise ProbeError(
            "autosuggest_parse_error",
            "Autosuggest response did not contain a places list.",
        )
    return places


def _candidate_to_entity(candidate: object, *, requested_iata: str, is_destination: bool) -> EntityResult | None:
    iata = _as_str(_field(candidate, ("iataCode", "IataCode", "iata", "IATA")))
    if iata is None or iata.upper() != requested_iata:
        return None
    entity_id = _as_str(_field(candidate, ("entityId", "EntityId", "PlaceId")))
    name = _as_str(_field(candidate, ("name", "Name", "PlaceName")))
    if entity_id is None or name is None:
        return None
    place_type = _as_str(_field(candidate, ("type", "Type", "placeType", "PlaceType")))
    parent_id = _as_str(
        _field(
            candidate,
            ("parentId", "ParentId", "CityId", "cityId", "parent.entityId"),
        )
    )
    return EntityResult(
        iata=requested_iata,
        entity_id=entity_id,
        name=name,
        place_type=place_type,
        parent_entity_id=parent_id,
        place_of_stay_entity_id=parent_id if is_destination and parent_id else None,
    )


def _is_airport(entity: EntityResult) -> bool:
    if entity.place_type is None:
        return False
    normalized = entity.place_type.upper()
    return "AIRPORT" in normalized or normalized == "AIRPORT"


def _safe_candidate_summary(entities: list[EntityResult]) -> str:
    return "; ".join(
        f"{entity.iata} {entity.entity_id} {entity.name} {entity.place_type or 'unknown'}"
        for entity in entities
    )


def get_entity_id(
    iata_code: str,
    *,
    config: ProbeConfig,
    client: httpx.Client,
    is_destination: bool = False,
) -> EntityResult:
    requested_iata = normalize_iata(iata_code)
    url = (
        f"{config.base_url}{AUTOSUGGEST_PATH}/"
        f"{quote(config.market)}/{quote(config.locale)}/{quote(requested_iata)}"
    )
    try:
        response = client.get(
            url,
            params={
                "isDestination": "true" if is_destination else "false",
                "enable_general_search_v2": "false",
            },
            headers=request_headers(config),
            timeout=config.timeout_seconds,
        )
    except Exception as exc:
        raise ProbeError(
            "autosuggest_transport_error",
            f"Autosuggest request failed with {type(exc).__name__}.",
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise ProbeError(
            "autosuggest_http_error",
            f"Autosuggest returned HTTP {response.status_code}.",
        )

    try:
        payload = response.json()
        places = _places_from_payload(payload)
    except ProbeError:
        raise
    except Exception as exc:
        raise ProbeError(
            "autosuggest_parse_error",
            f"Autosuggest response could not be parsed as JSON: {type(exc).__name__}.",
        ) from exc

    entities = [
        entity
        for candidate in places
        if (entity := _candidate_to_entity(
            candidate,
            requested_iata=requested_iata,
            is_destination=is_destination,
        ))
        is not None
    ]
    if not entities:
        raise ProbeError(
            "entity_not_found",
            f"No Skyscanner entity matched IATA {requested_iata}.",
        )

    airport_entities = [entity for entity in entities if _is_airport(entity)]
    preferred = airport_entities or entities
    if len(preferred) > 1:
        raise ProbeError(
            "entity_ambiguous",
            f"Multiple Skyscanner entities matched {requested_iata}: {_safe_candidate_summary(preferred)}",
        )
    return preferred[0]
```

- [ ] **Step 4: Run tests for Task 2**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: PASS for Task 1 and Task 2 tests.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py
git commit -m "feat: resolve Skyscanner entity ids"
```

Expected: commit contains Autosuggest resolver implementation and tests only.

## Task 3: Search Request Builder And Search Error Handling

**Files:**
- Modify: `scripts/skyscanner_http_probe.py`
- Modify: `tests/skyscanner/test_http_probe.py`

- [ ] **Step 1: Add tests for search body, headers, and search failure modes**

Append to `tests/skyscanner/test_http_probe.py`:

```python
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
    assert post["headers"]["content-type"] == "application/json"
    assert post["headers"]["x-skyscanner-viewid"] == "11111111-2222-4333-8444-555555555555"
    assert "origin" not in post["headers"]


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


def test_fetch_flights_maps_incomplete_status() -> None:
    client = FakeClient(FakeResponse(payload={"context": {"status": "pending"}, "itineraries": {"results": []}}))

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
```

- [ ] **Step 2: Run tests and verify search tests fail**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: FAIL with `AttributeError` for `build_search_body` or `fetch_flights`.

- [ ] **Step 3: Implement search request builder and response validation**

Add these imports near the top of `scripts/skyscanner_http_probe.py`:

```python
from urllib.parse import urljoin
import uuid
```

Add below the Autosuggest code:

```python
SEARCH_PATH = "/g/radar/api/v2/web-unified-search/"


def _entity_ref(entity: EntityResult) -> dict[str, str]:
    return {"@type": "entity", "entityId": entity.entity_id}


def build_search_body(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
) -> dict[str, object]:
    outbound_leg: dict[str, object] = {
        "legOrigin": _entity_ref(origin),
        "legDestination": _entity_ref(destination),
        "dates": date_parts(departure_date),
    }
    if destination.place_of_stay_entity_id is not None:
        outbound_leg["placeOfStay"] = destination.place_of_stay_entity_id

    legs: list[dict[str, object]] = [outbound_leg]
    if return_date is not None:
        legs.append(
            {
                "legOrigin": _entity_ref(destination),
                "legDestination": _entity_ref(origin),
                "dates": date_parts(return_date),
            }
        )

    return {
        "cabinClass": "ECONOMY",
        "childAges": [],
        "adults": 1,
        "legs": legs,
    }


def search_headers(config: ProbeConfig, *, view_id: str) -> dict[str, str]:
    headers = request_headers(config, accept_json=False)
    headers["content-type"] = "application/json"
    headers["x-skyscanner-viewid"] = view_id
    return headers


def _search_payload(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
    config: ProbeConfig,
    client: httpx.Client,
) -> object:
    url = urljoin(config.base_url + "/", SEARCH_PATH.lstrip("/"))
    body = build_search_body(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
    )
    try:
        response = client.post(
            url,
            json=body,
            headers=search_headers(config, view_id=str(uuid.uuid4())),
            timeout=config.timeout_seconds,
        )
    except Exception as exc:
        raise ProbeError(
            "search_transport_error",
            f"Search request failed with {type(exc).__name__}.",
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise ProbeError("search_http_error", f"Search returned HTTP {response.status_code}.")

    try:
        payload = response.json()
    except Exception as exc:
        raise ProbeError(
            "search_parse_error",
            f"Search response could not be parsed as JSON: {type(exc).__name__}.",
        ) from exc

    if not isinstance(payload, dict):
        raise ProbeError("search_parse_error", "Search response was not a JSON object.")
    status = _field(payload.get("context"), ("status",))
    if status != "complete":
        raise ProbeError("search_incomplete", f"Search did not complete; status={status!r}.")
    itineraries = payload.get("itineraries")
    results = _field(itineraries, ("results",))
    if not isinstance(results, list):
        raise ProbeError("search_parse_error", "Search response did not contain itineraries.results.")
    return payload


def fetch_flights(
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None = None,
    *,
    config: ProbeConfig,
    client: httpx.Client,
) -> list[FlightProbeResult]:
    _search_payload(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        config=config,
        client=client,
    )
    raise ProbeError(
        "no_usable_results",
        "Search completed but no itinerary had a positive price and deep link.",
    )
```

- [ ] **Step 4: Run tests for Task 3**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: PASS for validation, Autosuggest, request-body, header, and search error tests.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py
git commit -m "feat: build Skyscanner search request"
```

Expected: commit contains search request construction and validation only.

## Task 4: Fare And Deep Link Extraction

**Files:**
- Modify: `scripts/skyscanner_http_probe.py`
- Modify: `tests/skyscanner/test_http_probe.py`

- [ ] **Step 1: Add fare extraction tests**

Append to `tests/skyscanner/test_http_probe.py`:

```python
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
```

- [ ] **Step 2: Run fare extraction tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py::test_fetch_flights_extracts_sorted_fares_and_absolute_deeplinks -v
```

Expected: FAIL because `fetch_flights()` still raises `no_usable_results`.

- [ ] **Step 3: Implement fare extraction**

Replace the temporary `fetch_flights()` body and add helpers below `_search_payload`:

```python
def _float_value(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _iter_segments(itinerary: object) -> list[object]:
    if not isinstance(itinerary, dict):
        return []
    segments: list[object] = []
    legs = itinerary.get("legs")
    if not isinstance(legs, list):
        return []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        leg_segments = leg.get("segments")
        if isinstance(leg_segments, list):
            segments.extend(leg_segments)
    return segments


def _airline_label(itinerary: object) -> str:
    labels: list[str] = []
    for segment in _iter_segments(itinerary):
        carrier = _field(segment, ("marketingCarrier",))
        label = _as_str(_field(carrier, ("displayCode", "name")))
        if label is not None and label not in labels:
            labels.append(label)
    return "+".join(labels) if labels else "unknown"


def _positive_price_option(itinerary: object) -> tuple[float, str] | None:
    if not isinstance(itinerary, dict):
        return None
    options = itinerary.get("pricingOptions")
    if not isinstance(options, list):
        return None
    candidates: list[tuple[float, str]] = []
    for option in options:
        amount = _float_value(_field(option, ("price.amount",)))
        if amount is None or not isinstance(option, dict):
            continue
        items = option.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            url = _as_str(_field(item, ("url",)))
            if url is not None:
                candidates.append((amount, url))
                break
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0]


def _extract_results(payload: object, *, config: ProbeConfig) -> list[FlightProbeResult]:
    results = _field(_field(payload, ("itineraries",)), ("results",))
    if not isinstance(results, list):
        raise ProbeError("search_parse_error", "Search response did not contain itineraries.results.")

    extracted: list[FlightProbeResult] = []
    for itinerary in sorted(
        results,
        key=lambda item: _float_value(_field(item, ("price.raw",))) or float("inf"),
    ):
        canonical_price = _float_value(_field(itinerary, ("price.raw",)))
        if canonical_price is None:
            continue
        option = _positive_price_option(itinerary)
        if option is None:
            continue
        _, deeplink = option
        extracted.append(
            FlightProbeResult(
                airline=_airline_label(itinerary),
                price_amount=canonical_price,
                currency=config.currency,
                deeplink_url=urljoin(config.base_url + "/", deeplink),
            )
        )

    if not extracted:
        raise ProbeError(
            "no_usable_results",
            "Search completed but no itinerary had a positive price and deep link.",
        )
    return extracted


def fetch_flights(
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None = None,
    *,
    config: ProbeConfig,
    client: httpx.Client,
) -> list[FlightProbeResult]:
    payload = _search_payload(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        config=config,
        client=client,
    )
    return _extract_results(payload, config=config)
```

- [ ] **Step 4: Run tests for Task 4**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: PASS for all probe unit tests.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py
git commit -m "feat: extract Skyscanner probe fares"
```

Expected: commit contains fare extraction implementation and tests only.

## Task 5: CLI Runtime And Secret-Safe Output

**Files:**
- Modify: `scripts/skyscanner_http_probe.py`
- Modify: `tests/skyscanner/test_http_probe.py`

- [ ] **Step 1: Add CLI tests for output, limit, and secret redaction**

Append to `tests/skyscanner/test_http_probe.py`:

```python
def test_print_results_respects_limit(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        probe.FlightProbeResult("VJ", 220.96, "SGD", "https://example.test/1"),
        probe.FlightProbeResult("SQ", 300.0, "SGD", "https://example.test/2"),
    ]

    probe.print_results(results, limit=1)

    captured = capsys.readouterr()
    assert "VJ" in captured.out
    assert "220.96 SGD" in captured.out
    assert "https://example.test/1" in captured.out
    assert "SQ" not in captured.out


def test_main_prints_safe_error_for_missing_cookie(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "missing_cookie" in captured.err
    assert "__Secure-anon_token" not in captured.err


def test_run_probe_resolves_entities_and_prints_results(capsys: pytest.CaptureFixture[str]) -> None:
    class ScriptedClient:
        def __init__(self) -> None:
            self.responses = [
                FakeResponse(payload={"places": [{"iataCode": "SIN", "entityId": "95673375", "name": "Singapore Changi", "type": "PLACE_TYPE_AIRPORT"}]}),
                FakeResponse(payload={"places": [{"iataCode": "SGN", "entityId": "95673379", "name": "Ho Chi Minh City", "type": "PLACE_TYPE_AIRPORT", "parentId": "27546329"}]}),
                FakeResponse(payload=search_payload([itinerary(price=220.96, option_amount=220.96, url="/transport_deeplink/cheap", carrier="VJ")])),
            ]

        def get(self, url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
            return self.responses.pop(0)

        def post(self, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
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
    assert "VJ" in captured.out
    assert "220.96 SGD" in captured.out
    assert "transport_deeplink/cheap" in captured.out
```

- [ ] **Step 2: Run CLI tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py::test_run_probe_resolves_entities_and_prints_results -v
```

Expected: FAIL with `AttributeError` for `run_probe` or `print_results`.

- [ ] **Step 3: Implement CLI output and full runtime flow**

Replace `main()` and add these functions near the bottom of `scripts/skyscanner_http_probe.py`:

```python
def print_results(results: list[FlightProbeResult], *, limit: int) -> None:
    for index, result in enumerate(results[:limit], start=1):
        print(
            f"{index}. {result.airline} | "
            f"{result.price_amount:.2f} {result.currency} | "
            f"{result.deeplink_url}"
        )


def run_probe(
    *,
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    return_date: str | None,
    limit: int,
    config: ProbeConfig,
    client: httpx.Client,
) -> int:
    origin = get_entity_id(
        origin_iata,
        config=config,
        client=client,
        is_destination=False,
    )
    destination = get_entity_id(
        destination_iata,
        config=config,
        client=client,
        is_destination=True,
    )
    results = fetch_flights(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        config=config,
        client=client,
    )
    print_results(results, limit=limit)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        origin_iata = normalize_iata(args.origin)
        destination_iata = normalize_iata(args.destination)
        date_parts(args.departure_date)
        if args.return_date is not None:
            date_parts(args.return_date)
        if args.limit < 1:
            raise ProbeError("invalid_argument", "--limit must be at least 1.")
        config = config_from_env(
            os.environ,
            market=args.market,
            locale=args.locale,
            currency=args.currency,
        )
        with httpx.Client() as client:
            return run_probe(
                origin_iata=origin_iata,
                destination_iata=destination_iata,
                departure_date=args.departure_date,
                return_date=args.return_date,
                limit=args.limit,
                config=config,
                client=client,
            )
    except ProbeError as exc:
        print(exc.safe_text(), file=sys.stderr)
        return 1
```

- [ ] **Step 4: Run tests for Task 5**

Run:

```bash
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: PASS for all probe tests.

- [ ] **Step 5: Commit Task 5**

Run:

```bash
git add scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py
git commit -m "feat: add Skyscanner probe CLI"
```

Expected: commit contains CLI runtime and tests only.

## Task 6: Final Verification And Documentation Check

**Files:**
- Modify only if required by verification: `scripts/skyscanner_http_probe.py`, `tests/skyscanner/test_http_probe.py`, `pyproject.toml`, `uv.lock`

- [ ] **Step 1: Run focused Skyscanner tests**

Run:

```bash
uv run pytest tests/skyscanner -v
```

Expected: PASS. Existing `test_scan_graphql_bundles.py` and new `test_http_probe.py` both pass.

- [ ] **Step 2: Run CLI test suite because this adds a script entrypoint but no Cheapy CLI integration**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS. No `cheapy` command behavior changes.

- [ ] **Step 3: Run provider registry tests because Skyscanner must remain unregistered**

Run:

```bash
uv run pytest tests/test_providers.py -v
```

Expected: PASS. Skyscanner still has no provider manifest and does not appear in enabled providers.

- [ ] **Step 4: Run full tests**

Run:

```bash
uv run pytest -v
```

Expected: PASS. If unrelated dirty worktree files cause failures, identify the unrelated failure and do not modify those files without a separate request.

- [ ] **Step 5: Optional manual live probe**

Run only when the user supplies a current cookie through the environment:

```bash
CHEAPY_SKYSCANNER_COOKIE='<redacted>' uv run python scripts/skyscanner_http_probe.py \
  --origin SIN \
  --destination SGN \
  --departure-date 2026-06-11 \
  --return-date 2026-06-16 \
  --limit 3
```

Expected: terminal prints up to three lines like:

```text
1. VJ | 220.96 SGD | https://www.skyscanner.com.sg/transport_deeplink/...
```

Do not paste the cookie, request headers, JWT, or full raw JSON into commits or issue comments.

- [ ] **Step 6: Inspect changed files**

Run:

```bash
git diff -- scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py pyproject.toml uv.lock
```

Expected: diff is limited to the probe script, its tests, and direct dev dependency metadata.

- [ ] **Step 7: Commit final verification fixes if any were needed**

If Task 6 required edits after previous commits, run:

```bash
git add scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py pyproject.toml uv.lock
git commit -m "test: verify Skyscanner HTTP probe"
```

Expected: no commit is created when there are no verification edits.

## Self-Review Checklist

- Spec coverage:
  - Standalone script path covered by Tasks 1 and 5.
  - Env cookie and safe error behavior covered by Tasks 1 and 5.
  - Autosuggest resolver covered by Task 2.
  - Minimal `web-unified-search` POST covered by Task 3.
  - `placeOfStay` propagation covered by Task 3.
  - Fare extraction, zero-price filtering, sorting, and deep links covered by Task 4.
  - Offline tests and no provider registry integration covered by Tasks 1 through 6.
- Placeholder scan: no task contains deferred implementation language.
- Type consistency: `ProbeConfig`, `EntityResult`, `FlightProbeResult`, `ProbeError`, `get_entity_id()`, `fetch_flights()`, and `run_probe()` use the same signatures throughout.
