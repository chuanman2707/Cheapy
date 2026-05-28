# Cheapy Skyscanner Live Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the existing Skyscanner plain-HTTP research flow into a default-enabled Cheapy live provider without exposing internal deeplinks, cookies, headers, request bodies, raw payloads, session data, or challenge URLs.

**Architecture:** Add a packaged `cheapy.providers.skyscanner` live provider with three boundaries: `adapter.py` handles upstream HTTP and returns minimal parsed itinerary candidates; `normalizer.py` maps candidates into Contract V1 offers; `provider.py` maps requests/errors into `ProviderResult`. `public_search_url` remains generated only by the existing safe public link layer after the provider returns clean offers.

**Tech Stack:** Python 3.12+, Pydantic Contract V1 models, Typer CLI, MCP server tests, pytest, uv, package resources via `importlib.resources`, default Skyscanner HTTP transport through the existing curl-based pattern.

---

## File Structure

- Create `cheapy/providers/skyscanner/manifest.toml`: provider registry metadata.
- Modify `cheapy/providers/skyscanner/__init__.py`: update package docstring now that the package contains a live provider and experimental scanner utilities.
- Create `cheapy/providers/skyscanner/adapter.py`: Skyscanner config, safe adapter errors, HTTP client protocol, curl client, autosuggest, radar search body, polling, and extraction into minimal candidate dataclasses.
- Create `cheapy/providers/skyscanner/normalizer.py`: map `SkyscannerItineraryCandidate` to `FlightOfferV1`.
- Create `cheapy/providers/skyscanner/provider.py`: async provider wrapper, passenger eligibility gate, adapter exception mapping, `ProviderResult` accounting.
- Modify `scripts/skyscanner_http_probe.py`: keep the diagnostic CLI behavior, but import shared safe HTTP/config/entity/search helpers from `cheapy.providers.skyscanner.adapter`; keep script-local diagnostic deeplink result printing.
- Modify `tests/test_providers.py`: registry/discovery assertions.
- Modify `tests/test_package_data.py`: wheel package data and installed CLI provider assertions.
- Modify `tests/test_cli.py`: provider list/test output and full-surface Markdown/JSON leak checks.
- Modify `tests/test_mcp.py`: MCP output no-leak check with Skyscanner public link.
- Modify `tests/test_search.py`: final `SearchResponseV1` public-link and denylist checks.
- Create `tests/skyscanner/test_adapter.py`: adapter unit tests with fake clients, no live network.
- Create `tests/skyscanner/test_normalizer.py`: Contract V1 normalization tests.
- Create `tests/skyscanner/test_provider.py`: provider accounting, passenger rejection, adapter error mapping, no stdout/stderr.
- Keep `tests/skyscanner/test_http_probe.py`: diagnostic script tests should continue to pass.

## Sensitive Token Denylist

Use this denylist in provider, search, CLI, MCP, storage, and Markdown tests:

```python
SENSITIVE_SKYSCANNER_TOKENS = (
    "/transport_deeplink/",
    "transport_deeplink",
    "session/id=secret",
    "sessionId",
    "challenge",
    "cookie",
    "__Secure-anon_token",
    "secret-cookie",
    "raw_payload",
    "request_body",
    "headers",
)


def assert_no_sensitive_skyscanner_tokens(value: object) -> None:
    import json

    text = json.dumps(value, sort_keys=True, default=str)
    for token in SENSITIVE_SKYSCANNER_TOKENS:
        assert token not in text
```

## Task 1: Manifest And Discovery

**Files:**
- Create: `cheapy/providers/skyscanner/manifest.toml`
- Modify: `cheapy/providers/skyscanner/__init__.py`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_package_data.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing registry and CLI tests**

In `tests/test_providers.py`, replace `test_skyscanner_experimental_scanner_is_not_discovered_as_provider` with these two tests:

```python
def test_skyscanner_manifest_is_discovered_from_package_resources() -> None:
    manifest = _manifest_by_name("skyscanner")

    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="skyscanner",
        display_name="Skyscanner live provider",
        default_enabled=True,
        provider_kind="live",
        module="cheapy.providers.skyscanner.provider",
        capabilities=["exact_one_way", "exact_round_trip"],
    )


def test_search_providers_include_skyscanner_live_provider() -> None:
    providers = {provider.name: provider for provider in registry.load_search_providers()}

    assert set(providers) >= {"google_fli", "traveloka", "skyscanner"}
    assert providers["skyscanner"].capabilities == (
        "exact_one_way",
        "exact_round_trip",
    )
```

In `test_provider_manifests_include_provider_kind`, add:

```python
assert kinds_by_name["skyscanner"] == "live"
```

In `tests/test_package_data.py`, update wheel and installed-resource checks:

```python
assert "cheapy/providers/skyscanner/manifest.toml" in names
assert "cheapy/providers/skyscanner/adapter.py" in names
assert "cheapy/providers/skyscanner/normalizer.py" in names
assert "cheapy/providers/skyscanner/provider.py" in names
```

Replace installed-resource assertions that currently require no Skyscanner manifest:

```python
skyscanner_manifest = skyscanner_root.joinpath("manifest.toml").read_text(encoding="utf-8")
assert 'name = "skyscanner"' in skyscanner_manifest
assert 'provider_kind = "live"' in skyscanner_manifest
assert "default_enabled = true" in skyscanner_manifest
```

Update installed `providers list` assertions:

```python
assert providers["skyscanner"]["provider_kind"] == "live"
assert providers["skyscanner"]["default_enabled"] is True
assert providers["skyscanner"]["capabilities"] == [
    "exact_one_way",
    "exact_round_trip",
]
```

Update installed `providers test` assertions:

```python
assert providers["skyscanner"]["status"] == "skipped"
assert providers["skyscanner"]["live_smoke"] == "not_run"
```

In `tests/test_cli.py`, update `test_providers_list_prints_json`, `test_providers_test_prints_json`, `test_providers_test_default_does_not_run_live_provider`, and `test_providers_test_human_prints_success_report` so Skyscanner appears as a skipped live provider and `providers_tested == 4`.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```sh
uv run pytest tests/test_providers.py tests/test_package_data.py tests/test_cli.py::test_providers_list_prints_json tests/test_cli.py::test_providers_test_prints_json tests/test_cli.py::test_providers_test_default_does_not_run_live_provider tests/test_cli.py::test_providers_test_human_prints_success_report -v
```

Expected: failures mention missing Skyscanner manifest/provider and missing provider module.

- [ ] **Step 3: Add manifest and package docstring**

Create `cheapy/providers/skyscanner/manifest.toml`:

```toml
manifest_schema_version = "1"
name = "skyscanner"
display_name = "Skyscanner live provider"
default_enabled = true
provider_kind = "live"
module = "cheapy.providers.skyscanner.provider"
capabilities = ["exact_one_way", "exact_round_trip"]
```

Update `cheapy/providers/skyscanner/__init__.py`:

```python
"""Skyscanner provider and experimental discovery utilities."""

from __future__ import annotations
```

Temporarily create `cheapy/providers/skyscanner/provider.py` with a loadable stub. This stub will be replaced in Task 4:

```python
"""Skyscanner live provider."""

from __future__ import annotations


class SkyscannerProvider:
    name = "skyscanner"
    capabilities = ("exact_one_way", "exact_round_trip")

    async def search_exact_one_way(self, request):
        raise NotImplementedError("Skyscanner provider is not implemented yet.")

    async def search_exact_round_trip(self, request):
        raise NotImplementedError("Skyscanner provider is not implemented yet.")


def create_provider() -> SkyscannerProvider:
    return SkyscannerProvider()
```

- [ ] **Step 4: Run focused tests**

Run:

```sh
uv run pytest tests/test_providers.py::test_skyscanner_manifest_is_discovered_from_package_resources tests/test_providers.py::test_search_providers_include_skyscanner_live_provider tests/test_cli.py::test_providers_list_prints_json tests/test_cli.py::test_providers_test_prints_json tests/test_cli.py::test_providers_test_default_does_not_run_live_provider tests/test_cli.py::test_providers_test_human_prints_success_report -v
```

Expected: provider list tests pass; provider test failures may remain until Task 4 replaces the stub. If a provider-test path calls the stub in default mode, that is a bug because live providers should be skipped without `--live`.

- [ ] **Step 5: Commit**

```sh
git add cheapy/providers/skyscanner/manifest.toml cheapy/providers/skyscanner/__init__.py cheapy/providers/skyscanner/provider.py tests/test_providers.py tests/test_package_data.py tests/test_cli.py
git commit -m "feat: register skyscanner live provider"
```

## Task 2: Adapter HTTP Core

**Files:**
- Create: `cheapy/providers/skyscanner/adapter.py`
- Create: `tests/skyscanner/test_adapter.py`
- No script edits in this task; diagnostic script migration is Task 5.

- [ ] **Step 1: Write failing adapter tests**

Create `tests/skyscanner/test_adapter.py` with these core tests:

```python
from __future__ import annotations

import json

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner import adapter


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
    def __init__(self, responses: list[FakeResponse] | Exception) -> None:
        self.responses = responses
        self.get_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def get(self, url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
        self.get_calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if isinstance(self.responses, Exception):
            raise self.responses
        return self.responses.pop(0)

    def post(self, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
        self.post_calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
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
                                    "marketingCarrier": {"displayCode": "VJ", "name": "VietJet"},
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
    for token in ("/transport_deeplink/", "__Secure-anon_token", "secret", "cookie"):
        assert token not in text


def test_config_repr_redacts_cookie() -> None:
    text = repr(config())
    assert "__Secure-anon_token" not in text
    assert "secret" not in text
    assert "cookie" not in text


def test_build_search_body_uses_requested_adult_count() -> None:
    origin = adapter.SkyscannerEntity(iata="SIN", entity_id="95673375", name="Singapore")
    destination = adapter.SkyscannerEntity(iata="SGN", entity_id="95673379", name="Ho Chi Minh City")

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


def test_http_403_maps_to_blocked_error() -> None:
    client = FakeClient([FakeResponse(status_code=403, payload={"error": "blocked"})])

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.get_entity_id("SIN", config=config(), client=client)

    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.http_status_code == 403
    assert_no_sensitive_tokens(exc_info.value.__dict__)
```

- [ ] **Step 2: Run adapter tests and confirm failure**

Run:

```sh
uv run pytest tests/skyscanner/test_adapter.py -v
```

Expected: import failure for missing `cheapy.providers.skyscanner.adapter`.

- [ ] **Step 3: Implement adapter module**

Create `cheapy/providers/skyscanner/adapter.py` by moving the safe reusable pieces from `scripts/skyscanner_http_probe.py` into package code:

- Constants: `DEFAULT_BASE_URL`, `DEFAULT_TIMEOUT_SECONDS`, `SEARCH_POLL_ATTEMPTS`, `SEARCH_POLL_INTERVAL_SECONDS`, `DEFAULT_USER_AGENT`, `AUTOSUGGEST_PATH`, `SEARCH_PATH`, `IATA_RE`, `DATE_RE`.
- Protocols: `HttpResponse`, `HttpClient`.
- Curl transport: `CurlResponse`, `CurlClient`, `_curl_config_quote`.
- Validation/helpers: `normalize_iata`, `date_parts`, `validate_date_range`, `_cookie_value`, `_field`, `_as_str`.
- Entity functions: `SkyscannerEntity`, `get_entity_id`.
- Search functions: `build_search_body`, `_search_referer`, `search_headers`, `_search_payload`.

Use these provider-specific dataclasses:

```python
from dataclasses import dataclass, field

from cheapy.models import ErrorCode


@dataclass(frozen=True)
class SkyscannerConfig:
    base_url: str
    market: str
    locale: str
    currency: str
    cookie: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(frozen=True)
class SkyscannerEntity:
    iata: str
    entity_id: str
    name: str
    place_type: str | None = None
    parent_entity_id: str | None = None
    place_of_stay_entity_id: str | None = None


@dataclass(frozen=True)
class SkyscannerLegCandidate:
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    airline_code: str
    flight_number: str
    duration_minutes: int


@dataclass(frozen=True)
class SkyscannerItineraryCandidate:
    item_id: str
    price_amount: float
    currency: str
    legs: tuple[SkyscannerLegCandidate, ...]
    total_duration_minutes: int
    stops: int
```

Use this safe adapter error shape:

```python
class SkyscannerProviderError(Exception):
    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        error_code: ErrorCode = ErrorCode.PROVIDER_FAILED,
        retryable: bool = False,
        http_status_code: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
        self.error_code = error_code
        self.retryable = retryable
        self.http_status_code = http_status_code
        self.exception_type = exception_type
```

Map HTTP status codes in both autosuggest and search reads:

```python
def _http_error(
    *,
    operation: str,
    status_code: int,
) -> SkyscannerProviderError:
    if status_code in {401, 403}:
        return SkyscannerProviderError(
            failure_type="blocked",
            message_en=f"Skyscanner {operation} was blocked.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=status_code,
        )
    if status_code == 429:
        return SkyscannerProviderError(
            failure_type="rate_limited",
            message_en=f"Skyscanner {operation} was rate limited.",
            error_code=ErrorCode.PROVIDER_RATE_LIMITED,
            retryable=True,
            http_status_code=status_code,
        )
    return SkyscannerProviderError(
        failure_type="http_error",
        message_en=f"Skyscanner {operation} returned HTTP {status_code}.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
        http_status_code=status_code,
    )
```

Implement `config_from_env()` without importing `httpx`:

```python
def config_from_env(
    env: Mapping[str, str],
    *,
    market: str = "SG",
    locale: str = "en-GB",
    currency: str = "SGD",
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> SkyscannerConfig:
    cookie = env.get("CHEAPY_SKYSCANNER_COOKIE", "").strip()
    if not cookie:
        raise SkyscannerProviderError(
            failure_type="missing_cookie",
            message_en="Skyscanner cookie is not configured.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    return SkyscannerConfig(
        base_url=base_url.rstrip("/"),
        market=market,
        locale=locale,
        currency=currency,
        cookie=cookie,
        timeout_seconds=timeout_seconds,
        user_agent=(
            env.get("CHEAPY_SKYSCANNER_USER_AGENT", DEFAULT_USER_AGENT).strip()
            or DEFAULT_USER_AGENT
        ),
    )
```

Implement `build_search_body()` with an `adults` keyword argument so `adults` is not hardcoded:

```python
return {
    "cabinClass": "ECONOMY",
    "childAges": [],
    "adults": adults,
    "legs": legs,
}
```

Implement `SkyscannerAdapter.search_exact_one_way()` and `search_exact_round_trip()`:

- Resolve origin with `is_destination=False`.
- Resolve destination with `is_destination=True`.
- Fetch radar payload.
- Extract only candidates that have a positive price, a same-origin `/transport_deeplink/` pricing option, complete route/timing fields, and at least one leg.
- Discard the deeplink value after using it as a usability signal.
- Sort candidates by `(price_amount, item_id)`.

- [ ] **Step 4: Run adapter tests**

Run:

```sh
uv run pytest tests/skyscanner/test_adapter.py -v
```

Expected: all adapter tests pass without live network calls.

- [ ] **Step 5: Commit**

```sh
git add cheapy/providers/skyscanner/adapter.py tests/skyscanner/test_adapter.py
git commit -m "feat: add skyscanner adapter core"
```

## Task 3: Contract Normalizer

**Files:**
- Create: `cheapy/providers/skyscanner/normalizer.py`
- Create: `tests/skyscanner/test_normalizer.py`

- [ ] **Step 1: Write failing normalizer tests**

Create `tests/skyscanner/test_normalizer.py`:

```python
from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner.adapter import (
    SkyscannerItineraryCandidate,
    SkyscannerLegCandidate,
)
from cheapy.providers.skyscanner.normalizer import normalize_candidates


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


def _candidate(*legs: SkyscannerLegCandidate) -> SkyscannerItineraryCandidate:
    return SkyscannerItineraryCandidate(
        item_id="itinerary-1",
        price_amount=220.96,
        currency="SGD",
        legs=tuple(legs) if legs else (_leg(),),
        total_duration_minutes=sum(leg.duration_minutes for leg in legs) if legs else 90,
        stops=0,
    )


def test_normalize_one_way_candidate_to_contract_offer() -> None:
    request = ProviderExactOneWayRequest(
        origin="SIN",
        destination="SGN",
        departure_date="2026-06-11",
    )

    offers, errors = normalize_candidates([_candidate()], request)

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "skyscanner:SIN-SGN:2026-06-11:itinerary-1"
    assert offer.provider == "skyscanner"
    assert offer.price_amount == 220.96
    assert offer.currency == "SGD"
    assert offer.public_search_url is None
    assert offer.actual_origin == "SIN"
    assert offer.actual_destination == "SGN"
    assert offer.actual_departure_date == "2026-06-11"
    assert offer.actual_return_date is None
    assert offer.total_duration_minutes == 90
    assert offer.stops == 0
    assert offer.legs[0].flight_number == "VJ814"


def test_normalize_round_trip_candidate_sets_return_date_and_legs() -> None:
    request = ProviderExactRoundTripRequest(
        origin="SIN",
        destination="SGN",
        departure_date="2026-06-11",
        return_date="2026-06-18",
    )
    outbound = _leg()
    inbound = _leg(
        origin="SGN",
        destination="SIN",
        departure_time="2026-06-18T12:00:00",
        arrival_time="2026-06-18T15:30:00",
        airline_code="VJ",
        flight_number="VJ815",
        duration_minutes=210,
    )

    offers, errors = normalize_candidates([_candidate(outbound, inbound)], request)

    assert errors == []
    offer = offers[0]
    assert offer.offer_id == "skyscanner:SIN-SGN:2026-06-11:2026-06-18:itinerary-1"
    assert offer.actual_return_date == "2026-06-18"
    assert offer.return_offset_days == 0
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SIN", "SGN"),
        ("SGN", "SIN"),
    ]
    assert offer.total_duration_minutes == 300


def test_normalize_candidate_with_no_legs_returns_parse_error() -> None:
    request = ProviderExactOneWayRequest(
        origin="SIN",
        destination="SGN",
        departure_date="2026-06-11",
    )
    candidate = SkyscannerItineraryCandidate(
        item_id="broken",
        price_amount=220.96,
        currency="SGD",
        legs=(),
        total_duration_minutes=0,
        stops=0,
    )

    offers, errors = normalize_candidates([candidate], request)

    assert offers == []
    assert len(errors) == 1
    assert errors[0].details["provider"] == "skyscanner"
    assert errors[0].details["failure_type"] == "parse_error"
```

- [ ] **Step 2: Run normalizer tests and confirm failure**

Run:

```sh
uv run pytest tests/skyscanner/test_normalizer.py -v
```

Expected: import failure for missing `normalizer.py`.

- [ ] **Step 3: Implement normalizer**

Create `cheapy/providers/skyscanner/normalizer.py` with:

- `PROVIDER_NAME = "skyscanner"`
- `EXACT_ONE_WAY_CAPABILITY = "exact_one_way"`
- `EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"`
- `ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest`
- `normalize_candidates(candidates, request) -> tuple[list[FlightOfferV1], list[ErrorV1]]`

Mapping rules:

- `offer_id` one-way: `skyscanner:{origin}-{destination}:{departure_date}:{item_id}`
- `offer_id` round-trip: `skyscanner:{origin}-{destination}:{departure_date}:{return_date}:{item_id}`
- `actual_departure_date = first leg departure_time[:10]`
- round-trip `actual_return_date = first leg whose origin == request.destination and destination == request.origin`, using that leg departure date
- one-way `actual_return_date = None`
- `actual_origin = request.origin`
- `actual_destination = request.destination`
- offsets via `date.fromisoformat(actual_date) - date.fromisoformat(requested_date)`
- `public_search_url = None`
- `fare_details_status = "not_collected"`
- `flags.uses_flexible_departure_date` and `flags.uses_flexible_return_date` reflect non-zero offsets.

Use only `FlightLegV1.model_validate()` or direct `FlightLegV1(...)` for leg construction. On per-candidate exceptions, return an `ErrorV1` with:

```python
ErrorV1(
    code=ErrorCode.PROVIDER_FAILED,
    severity=Severity.ERROR,
    message_en="Skyscanner itinerary could not be normalized.",
    details={
        "provider": PROVIDER_NAME,
        "failure_type": "parse_error",
        "item_id": candidate.item_id,
        "exception_type": type(exc).__name__,
    },
    retryable=False,
)
```

- [ ] **Step 4: Run normalizer tests**

Run:

```sh
uv run pytest tests/skyscanner/test_normalizer.py -v
```

Expected: all normalizer tests pass.

- [ ] **Step 5: Commit**

```sh
git add cheapy/providers/skyscanner/normalizer.py tests/skyscanner/test_normalizer.py
git commit -m "feat: normalize skyscanner offers"
```

## Task 4: Provider Wrapper And Accounting

**Files:**
- Modify: `cheapy/providers/skyscanner/provider.py`
- Create: `tests/skyscanner/test_provider.py`

- [ ] **Step 1: Write failing provider tests**

Create `tests/skyscanner/test_provider.py`:

```python
from __future__ import annotations

import asyncio
import json

from cheapy.models import ErrorCode, PassengersV1, ProviderStatusCode
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner.adapter import (
    SkyscannerItineraryCandidate,
    SkyscannerLegCandidate,
    SkyscannerProviderError,
)
from cheapy.providers.skyscanner.provider import SkyscannerProvider, create_provider


class FakeAdapter:
    configured_currency = "SGD"

    def __init__(self, result: list[SkyscannerItineraryCandidate] | Exception) -> None:
        self.result = result
        self.one_way_calls = 0
        self.round_trip_calls = 0

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> list[SkyscannerItineraryCandidate]:
        self.one_way_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(self, request: ProviderExactRoundTripRequest) -> list[SkyscannerItineraryCandidate]:
        self.round_trip_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _candidate() -> SkyscannerItineraryCandidate:
    return SkyscannerItineraryCandidate(
        item_id="itinerary-1",
        price_amount=220.96,
        currency="SGD",
        legs=(
            SkyscannerLegCandidate(
                origin="SIN",
                destination="SGN",
                departure_time="2026-06-11T09:15:00",
                arrival_time="2026-06-11T10:45:00",
                airline_code="VJ",
                flight_number="VJ814",
                duration_minutes=90,
            ),
        ),
        total_duration_minutes=90,
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


def assert_no_sensitive_tokens(value: object) -> None:
    text = json.dumps(value, sort_keys=True, default=str)
    for token in ("/transport_deeplink/", "__Secure-anon_token", "secret-cookie", "headers", "request_body"):
        assert token not in text


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
    assert_no_sensitive_tokens(result.model_dump(mode="json"))


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
```

- [ ] **Step 2: Run provider tests and confirm failure**

Run:

```sh
uv run pytest tests/skyscanner/test_provider.py -v
```

Expected: failures from stub `provider.py`.

- [ ] **Step 3: Implement provider**

Replace the Task 1 stub in `cheapy/providers/skyscanner/provider.py`.

Required structure:

```python
"""Skyscanner live provider."""

from __future__ import annotations

import asyncio
import os
from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest, ProviderResult
from cheapy.providers.skyscanner.adapter import SkyscannerAdapter, SkyscannerProviderError
from cheapy.providers.skyscanner.normalizer import (
    EXACT_ONE_WAY_CAPABILITY,
    EXACT_ROUND_TRIP_CAPABILITY,
    PROVIDER_NAME,
    normalize_candidates,
)


DEFAULT_TIMEOUT_SECONDS = 30.0
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
```

Provider behavior:

- `name = PROVIDER_NAME`
- `capabilities = (EXACT_ONE_WAY_CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)`
- constructor accepts `adapter: object | None = None`, `timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS`, `env: Mapping[str, str] | None = None`
- constructor stores `self._adapter = adapter` and `self._env = dict(os.environ if env is None else env)` without reading `CHEAPY_SKYSCANNER_COOKIE`
- default adapter is created lazily inside the search path with `SkyscannerAdapter.from_env(self._env)` so provider registry loading never fails because a cookie is missing
- reject passengers with children or infants before creating or touching an adapter
- use `asyncio.wait_for(asyncio.to_thread(self._search_sync, request, search_method_name=search_method_name), timeout=self._timeout_seconds)` so lazy adapter construction errors are mapped to `ProviderResult`
- map adapter errors into `ErrorV1`
- status rules match Google Fli and Traveloka:
  - offers and errors: `partial`
  - errors only: `failed`
  - offers and no errors: `success`

Lazy adapter helper:

```python
def _adapter_for_call(self) -> object:
    if self._adapter is not None:
        return self._adapter
    return SkyscannerAdapter.from_env(self._env)


def _search_sync(
    self,
    request: ProviderRequest,
    *,
    search_method_name: str,
) -> list[object]:
    adapter = self._adapter_for_call()
    search_method = getattr(adapter, search_method_name)
    return search_method(request)
```

Error helper:

```python
def _provider_error(
    *,
    code: ErrorCode,
    message_en: str,
    failure_type: str,
    retryable: bool,
    capability: str,
    http_status_code: int | None = None,
    exception_type: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": capability,
        "failure_type": failure_type,
    }
    if http_status_code is not None:
        details["http_status_code"] = http_status_code
    if exception_type is not None:
        details["exception_type"] = exception_type
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )
```

Unsupported passenger error:

```python
def _unsupported_passengers_error(capability: str) -> ErrorV1:
    return _provider_error(
        code=ErrorCode.PROVIDER_FAILED,
        message_en="Skyscanner provider supports adult passengers only.",
        failure_type="unsupported_passengers",
        retryable=False,
        capability=capability,
    )
```

- [ ] **Step 4: Run provider tests**

Run:

```sh
uv run pytest tests/skyscanner/test_provider.py -v
```

Expected: all provider tests pass.

- [ ] **Step 5: Re-run provider discovery and CLI provider tests**

Run:

```sh
uv run pytest tests/test_providers.py tests/test_cli.py::test_providers_list_prints_json tests/test_cli.py::test_providers_test_prints_json tests/test_cli.py::test_providers_test_default_does_not_run_live_provider tests/test_cli.py::test_providers_test_human_prints_success_report -v
```

Expected: all pass, with Skyscanner skipped for default provider tests.

- [ ] **Step 6: Commit**

```sh
git add cheapy/providers/skyscanner/provider.py tests/skyscanner/test_provider.py tests/test_providers.py tests/test_cli.py
git commit -m "feat: add skyscanner provider wrapper"
```

## Task 5: Diagnostic Probe Wrapper

**Files:**
- Modify: `scripts/skyscanner_http_probe.py`
- Modify: `tests/skyscanner/test_http_probe.py` only where names move from script to adapter.

- [ ] **Step 1: Run existing probe tests before refactor**

Run:

```sh
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: pass before refactor. If they fail before edits, stop and inspect unrelated state.

- [ ] **Step 2: Refactor script to use package helpers**

Update `scripts/skyscanner_http_probe.py`:

- import `SkyscannerConfig as ProbeConfig`
- import `SkyscannerEntity as EntityResult`
- import `SkyscannerProviderError as ProbeError`
- import `CurlClient`, `CurlResponse`, `HttpClient`, `HttpResponse`
- import `DEFAULT_BASE_URL`, `DEFAULT_TIMEOUT_SECONDS`, `DEFAULT_USER_AGENT`
- import `normalize_iata`, `date_parts`, `validate_date_range`, `get_entity_id`, `request_headers`, `search_headers`, `build_search_body`
- keep script-local `FlightProbeResult`, `_safe_deeplink_url`, `_extract_results`, `fetch_flights`, `print_results`, `run_probe`, and `main`

The script can call an adapter helper that returns a radar search payload, but the provider path must not call the script.

If `ProbeError.safe_text()` no longer exists because the package error class uses Contract metadata, add a script-local wrapper:

```python
def _safe_error_text(exc: ProbeError) -> str:
    return f"{exc.failure_type}: {exc.message_en}"
```

Then in `main()`:

```python
except ProbeError as exc:
    print(_safe_error_text(exc), file=sys.stderr)
    return 1
```

Keep `--transport httpx` support in the script only. Do not import `httpx` from package provider modules.

- [ ] **Step 3: Run probe tests**

Run:

```sh
uv run pytest tests/skyscanner/test_http_probe.py -v
```

Expected: all diagnostic probe tests pass; terminal output may still include transport deeplinks because this script is manual diagnostic output, not provider/MCP output.

- [ ] **Step 4: Run import/package smoke**

Run:

```sh
uv run python - <<'PY'
from cheapy.providers.skyscanner.provider import create_provider
provider = create_provider()
print(provider.name, provider.capabilities)
PY
```

Expected stdout:

```text
skyscanner ('exact_one_way', 'exact_round_trip')
```

- [ ] **Step 5: Commit**

```sh
git add scripts/skyscanner_http_probe.py tests/skyscanner/test_http_probe.py
git commit -m "refactor: share skyscanner http helpers"
```

## Task 6: Search And Public Link Integration

**Files:**
- Modify: `tests/test_search.py`
- Modify: `tests/test_public_links.py` only if a new assertion is needed.

- [ ] **Step 1: Write failing search integration tests**

In `tests/test_search.py`, add a Skyscanner offer helper near existing `_offer()` usage:

```python
def test_search_exact_attaches_safe_skyscanner_public_search_url(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_result = ProviderResult(
        provider_name="skyscanner",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="skyscanner:CXR-SGN:2026-07-10:itinerary-1",
                provider="skyscanner",
                currency="SGD",
                price_amount=220.96,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=1,
        retryable=False,
    )
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(provider_result)],
    )

    response = search_exact(_request(origin="CXR", destination="SGN"))

    assert response.status == SearchStatus.SUCCESS
    url = response.offers[0].public_search_url
    assert url is not None
    assert url.startswith("https://www.skyscanner.com.sg/transport/flights/cxr/sgn/260710/")
    assert "/transport_deeplink/" not in response.model_dump_json()
```

Add a full-response denylist test:

```python
def test_search_exact_skyscanner_response_has_no_internal_url_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_result = ProviderResult(
        provider_name="skyscanner",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="skyscanner:CXR-SGN:2026-07-10:itinerary-1",
                provider="skyscanner",
                currency="SGD",
                price_amount=220.96,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=1,
        retryable=False,
    )
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(provider_result)],
    )

    response = search_exact(_request(origin="CXR", destination="SGN"))
    text = response.model_dump_json()

    for token in ("/transport_deeplink/", "sessionId", "cookie", "headers", "request_body", "raw_payload"):
        assert token not in text
```

- [ ] **Step 2: Run search tests**

Run:

```sh
uv run pytest tests/test_search.py::test_search_exact_attaches_safe_skyscanner_public_search_url tests/test_search.py::test_search_exact_skyscanner_response_has_no_internal_url_tokens -v
```

Expected: fail because `_offer()` currently fixes actual/requested route fields to `CXR -> SGN` and the new tests must control those fields explicitly.

- [ ] **Step 3: Add route overrides to the search test helper**

Add `requested_origin`, `requested_destination`, `actual_origin`, and `actual_destination` override parameters to `_offer()` and use them in the Skyscanner tests. Keep existing defaults unchanged for older tests:

```python
def _offer(
    *,
    offer_id: str,
    provider: str,
    currency: str,
    price_amount: float,
    requested_origin: str = "CXR",
    requested_destination: str = "SGN",
    actual_origin: str = "CXR",
    actual_destination: str = "SGN",
    requested_departure_date: str = "2026-07-10",
    actual_departure_date: str = "2026-07-10",
    departure_offset_days: int = 0,
    requested_return_date: str | None = None,
    actual_return_date: str | None = None,
    return_offset_days: int | None = None,
    departure_time: str = "2026-07-10T08:15:00",
    arrival_time: str = "2026-07-10T09:25:00",
) -> FlightOfferV1:
    return FlightOfferV1(
        offer_id=offer_id,
        price_amount=price_amount,
        currency=currency,
        comparable=True,
        rank_within_currency=1,
        global_rank=1,
        provider=provider,
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        actual_origin=actual_origin,
        actual_destination=actual_destination,
        nearby_origin_distance_km=None,
        nearby_destination_distance_km=None,
        requested_departure_date=requested_departure_date,
        actual_departure_date=actual_departure_date,
        departure_offset_days=departure_offset_days,
        requested_return_date=requested_return_date,
        actual_return_date=actual_return_date,
        return_offset_days=return_offset_days,
        legs=[
            FlightLegV1(
                origin=actual_origin,
                destination=actual_destination,
                departure_time=departure_time,
                arrival_time=arrival_time,
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=70,
            )
        ],
        total_duration_minutes=70,
        stops=0,
        flags=OfferFlagsV1(
            uses_flexible_departure_date=departure_offset_days != 0,
            uses_flexible_return_date=return_offset_days not in (None, 0),
        ),
        fare_details_status="not_collected",
    )
```

- [ ] **Step 4: Run public link tests**

Run:

```sh
uv run pytest tests/test_public_links.py -v
```

Expected: existing Skyscanner public URL tests pass unchanged.

- [ ] **Step 5: Commit**

```sh
git add tests/test_search.py tests/test_public_links.py
git commit -m "test: cover skyscanner public search integration"
```

## Task 7: CLI, MCP, And Storage No-Leak Surfaces

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_mcp.py`
- Modify: `tests/test_markdown_report.py` if a formatter-specific Skyscanner assertion is missing.
- Modify: storage tests only if existing CLI history tests do not cover persisted response payloads.

- [ ] **Step 1: Add CLI no-leak tests**

In `tests/test_cli.py`, add a Skyscanner public URL constant in the local test section:

```python
SKYSCANNER_PUBLIC_SEARCH_URL = (
    "https://www.skyscanner.com.sg/transport/flights/cxr/sgn/260710/"
    "?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0"
)
```

Add a history JSON test:

```python
def test_history_show_json_skyscanner_has_no_internal_url_tokens(tmp_path, monkeypatch) -> None:
    response = _cli_response(
        offers=[
            _offer(
                offer_id="skyscanner:CXR-SGN:2026-07-10:itinerary-1",
                provider="skyscanner",
                public_search_url=SKYSCANNER_PUBLIC_SEARCH_URL,
            )
        ],
        provider_statuses=[_provider_status(provider_name="skyscanner")],
    )
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, _cli_request(), response)

    result = runner.invoke(app, ["history", "show", str(run_id)])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    text = json.dumps(payload, sort_keys=True)
    assert payload["response"]["offers"][0]["public_search_url"] == SKYSCANNER_PUBLIC_SEARCH_URL
    for token in ("/transport_deeplink/", "sessionId", "cookie", "headers", "request_body", "raw_payload"):
        assert token not in text
```

Add a history Markdown test:

```python
def test_history_show_markdown_links_skyscanner_price_without_raw_url(tmp_path, monkeypatch) -> None:
    response = _cli_response(
        offers=[
            _offer(
                offer_id="skyscanner:CXR-SGN:2026-07-10:itinerary-1",
                price_amount=221.0,
                provider="skyscanner",
                public_search_url=SKYSCANNER_PUBLIC_SEARCH_URL,
            )
        ],
        provider_statuses=[_provider_status(provider_name="skyscanner")],
    )
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, _cli_request(), response)

    result = runner.invoke(app, ["history", "show", str(run_id), "--markdown"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert f"[221 VND on Skyscanner]({SKYSCANNER_PUBLIC_SEARCH_URL})" in result.stdout
    assert result.stdout.count(SKYSCANNER_PUBLIC_SEARCH_URL) == 1
    assert "public_search_url" not in result.stdout
    assert "/transport_deeplink/" not in result.stdout
```

- [ ] **Step 2: Add MCP no-leak test**

In `tests/test_mcp.py`, add a Skyscanner response helper or modify `_successful_search_response()` locally in a new test:

```python
def test_mcp_search_tool_skyscanner_output_has_public_link_only(monkeypatch: Any) -> None:
    skyscanner_url = (
        "https://www.skyscanner.com.sg/transport/flights/cxr/sgn/260710/"
        "?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0"
    )

    response = _successful_search_response().model_copy(
        update={
            "offers": [
                _successful_search_response().offers[0].model_copy(
                    update={
                        "provider": "skyscanner",
                        "public_search_url": skyscanner_url,
                    }
                )
            ]
        }
    )

    def fake_search_with_storage(request: Any) -> SearchWithStorageResult:
        return SearchWithStorageResult(
            response=response,
            search_run_id=1,
            storage_enabled=True,
            storage_warning=None,
        )

    monkeypatch.setattr("cheapy.mcp.search_with_storage", fake_search_with_storage)
    result = asyncio.run(_mcp_tool().run(_successful_search_arguments(), convert_result=True))

    payload_text = json.dumps(_structured_content(result), sort_keys=True)
    markdown = _text_content(result)
    assert skyscanner_url in payload_text
    assert skyscanner_url in markdown
    assert markdown.count(skyscanner_url) == 1
    for token in ("/transport_deeplink/", "sessionId", "cookie", "headers", "request_body", "raw_payload"):
        assert token not in payload_text
        assert token not in markdown
```

- [ ] **Step 3: Run surface tests**

Run:

```sh
uv run pytest tests/test_cli.py::test_history_show_json_skyscanner_has_no_internal_url_tokens tests/test_cli.py::test_history_show_markdown_links_skyscanner_price_without_raw_url tests/test_mcp.py::test_mcp_search_tool_skyscanner_output_has_public_link_only tests/test_markdown_report.py -v
```

Expected: all pass. If Markdown price/currency text differs, update only the literal expected text to match existing formatter output.

- [ ] **Step 4: Commit**

```sh
git add tests/test_cli.py tests/test_mcp.py tests/test_markdown_report.py
git commit -m "test: cover skyscanner output safety surfaces"
```

## Task 8: Packaging And Provider Smoke

**Files:**
- Modify: `tests/test_package_data.py`
- Modify: `tests/test_cli.py`
- Modify: `cheapy/providers/skyscanner/adapter.py` if packaging reveals a runtime-only dependency issue.

- [ ] **Step 1: Run packaging test**

Run:

```sh
uv run pytest tests/test_package_data.py -v
```

Expected: wheel includes Skyscanner manifest and provider modules. Installed `cheapy providers test` skips Skyscanner live smoke by default.

- [ ] **Step 2: Verify no runtime `httpx` import in provider package**

Run:

```sh
rg -n "import httpx|from httpx" cheapy/providers/skyscanner
```

Expected: no matches. `scripts/skyscanner_http_probe.py` may still import `httpx` for the diagnostic `--transport httpx` mode.

- [ ] **Step 3: Verify MCP tool list remains clean**

Run:

```sh
uv run pytest tests/test_mcp.py::test_mcp_lists_only_search_cheapest_flights_tool tests/test_mcp.py::test_mcp_does_not_expose_skyscanner_discovery_tool -v
```

Expected: only `search_cheapest_flights` is listed; no Skyscanner scanner/debug MCP tool exists.

- [ ] **Step 4: Commit packaging adjustments**

Commit only if this task changed files:

```sh
git add tests/test_package_data.py tests/test_cli.py cheapy/providers/skyscanner/adapter.py
git commit -m "test: verify skyscanner provider packaging"
```

If this task changed no files, record that in the implementation summary and do not create an empty commit.

## Task 9: Full Verification

**Files:**
- No planned code changes.

- [ ] **Step 1: Run focused Skyscanner tests**

Run:

```sh
uv run pytest tests/skyscanner/test_adapter.py tests/skyscanner/test_normalizer.py tests/skyscanner/test_provider.py tests/skyscanner/test_http_probe.py -v
```

Expected: all pass, no live network calls.

- [ ] **Step 2: Run relevant integration tests**

Run:

```sh
uv run pytest tests/test_providers.py tests/test_package_data.py tests/test_cli.py tests/test_mcp.py tests/test_search.py tests/test_public_links.py tests/test_markdown_report.py -v
```

Expected: all pass.

- [ ] **Step 3: Run full suite**

Run:

```sh
uv run pytest -v
```

Expected: all default tests pass; live tests remain skipped unless explicitly gated.

- [ ] **Step 4: Optional live provider smoke**

Only run this if `CHEAPY_SKYSCANNER_COOKIE` is configured and the user explicitly wants a live check:

```sh
CHEAPY_RUN_LIVE_TESTS=1 uv run cheapy providers test --live --human
```

Expected: Skyscanner may succeed, fail blocked, rate-limit, or report missing cookie depending on environment. Any output must be structured or concise human provider status and must not print cookies, headers, request bodies, raw payloads, session IDs, challenge URLs, or transport deeplinks.

- [ ] **Step 5: Final review**

Run:

```sh
git status --short
git log --oneline -10
```

Expected: worktree contains only intentional changes. Summarize commits and tests in the handoff.
