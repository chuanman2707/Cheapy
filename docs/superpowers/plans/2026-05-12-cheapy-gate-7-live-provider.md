# Cheapy Gate 7 Live Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the default live `google_fli` provider backed by upstream `flights`, keep fixture data out of user-facing MCP search, and preserve Contract V1 structured output.

**Architecture:** Add `cheapy.providers.google_fli` as a focused provider package with `adapter.py` for upstream `fli` interaction, `normalizer.py` for Contract V1 conversion, and `provider.py` for async provider execution and structured failures. Amend provider manifests with `provider_kind`, add `load_search_providers()` for normal user-facing search, keep fixture providers available for deterministic tests, and update MCP/CLI behavior so default tests never make live network calls.

**Tech Stack:** Python 3.12+, uv, `flights`/`fli`, Pydantic v2, Typer, FastMCP, pytest, stdlib `asyncio`, `time.perf_counter`, `os.environ`, `datetime`, `types.SimpleNamespace`.

---

## Current Baseline

Run before starting implementation:

```bash
git status --short
uv run pytest -q
```

Observed baseline on 2026-05-12:

```text
148 passed, 1 failed
```

The failing test was `tests/test_package_data.py::test_built_wheel_can_load_packaged_airport_and_provider_data`, caused by the offline wheel-install subprocess missing cached dependency `mcp` in a temporary venv. This is an environment/cache failure in the existing packaging lane, not a Gate 7 code failure. Run focused tests after each task. Before final completion, run `uv sync --extra dev` to refresh the local dependency cache, then rerun the full suite.

Required project-local skills were read before writing this plan:

```bash
sed -n '1,220p' .codex/skills/mcp-builder/SKILL.md
sed -n '1,220p' .codex/skills/ai-native-cli/SKILL.md
sed -n '1,220p' .codex/skills/python-testing-patterns/SKILL.md
sed -n '1,220p' .codex/skills/python-packaging/SKILL.md
sed -n '1,220p' .codex/skills/pydantic-models-py/SKILL.md
sed -n '1,180p' .codex/skills/uv-package-manager/SKILL.md
```

## File Structure

Create:

- `cheapy/providers/google_fli/__init__.py`: package marker and provider name export.
- `cheapy/providers/google_fli/manifest.toml`: bundled provider manifest with `provider_kind="live"`.
- `cheapy/providers/google_fli/adapter.py`: upstream `fli` imports, filter construction, sync search call, provider-local exception types.
- `cheapy/providers/google_fli/normalizer.py`: conversion from upstream flight result objects into `FlightOfferV1` and structured parse errors.
- `cheapy/providers/google_fli/provider.py`: `FlightProvider` implementation, async timeout/thread boundary, result status mapping.
- `tests/test_google_fli_normalizer.py`: deterministic normalizer tests using fake upstream objects.
- `tests/test_google_fli_provider.py`: provider and adapter tests with mocked upstream search, no live network.
- `tests/test_live_google_fli.py`: opt-in live smoke test gated by marker and environment variable.

Modify:

- `pyproject.toml`: add runtime dependency `flights`.
- `uv.lock`: update via uv.
- `cheapy/providers/registry.py`: add required `provider_kind`, add `load_search_providers()`.
- `cheapy/providers/manual_fixture/manifest.toml`: add `provider_kind="fixture"`.
- `cheapy/search.py`: use `load_search_providers()` for normal search and re-rank returned offers after global sorting/truncation.
- `cheapy/mcp.py`: change MCP annotation `openWorldHint` to `True`.
- `cheapy/cli.py`: in Task 2 show `provider_kind`; in Task 6 keep default provider checks offline and add `cheapy providers test --live`.
- `cheapy/agent_hooks.py`: update managed agent instructions to mention provider attribution in returned offers.
- `.codex/skills/cheapy/SKILL.md`: update project-local skill text consistently with installer-managed instructions.
- `tests/test_providers.py`: registry and manifest tests.
- `tests/test_search.py`: user-facing provider loader and ranking tests.
- `tests/test_mcp.py`: split subprocess protocol tests from in-process tool behavior tests.
- `tests/test_cli.py`: provider list/test/live JSON and exit-code tests.
- `tests/test_agent_hooks.py`: managed instruction text tests.
- `tests/test_package_data.py`: packaged `google_fli` manifest and installed-wheel provider command behavior.

Do not modify:

- `cheapy/models/contracts.py`: Gate 7 does not add Contract V1 enum values or fields.
- `cheapy/airports.py` and airport data snapshots.
- MCP tool name or input schema.

---

### Task 1: Add Upstream Runtime Dependency

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add `flights` with uv**

Run:

```bash
uv add flights
```

Expected:

```text
pyproject.toml and uv.lock are updated.
```

- [ ] **Step 2: Verify the upstream API surface**

Run:

```bash
uv run python - <<'PY'
import inspect
from fli.search import SearchFlights
from fli.models import (
    Airport,
    FlightResult,
    FlightSearchFilters,
    FlightSegment,
    PassengerInfo,
    SeatType,
    SortBy,
    TripType,
)

print(SearchFlights)
print(inspect.signature(FlightSearchFilters))
print(inspect.signature(FlightSegment))
print(inspect.signature(PassengerInfo))
print("currency" in FlightResult.model_fields)
print(SeatType.ECONOMY.name)
print(TripType.ONE_WAY.name)
print(SortBy.CHEAPEST.name)
print(Airport.SGN.name, Airport.BKK.name)
PY
```

Expected stdout contains:

```text
<class 'fli.search.flights.SearchFlights'>
True
ECONOMY
ONE_WAY
CHEAPEST
SGN BKK
```

- [ ] **Step 3: Run dependency verification tests**

Run:

```bash
uv run python -c "from fli.search import SearchFlights; from fli.models import FlightResult; print(SearchFlights.__name__, 'currency' in FlightResult.model_fields)"
```

Expected:

```text
SearchFlights True
```

- [ ] **Step 4: Commit dependency update**

Run:

```bash
git add pyproject.toml uv.lock
git commit -m "build: add flights dependency" -m "AI-Model: GPT-5 Codex"
```

Expected: commit contains only `pyproject.toml` and `uv.lock`.

---

### Task 2: Add Provider Kind To Registry And Manifests

**Files:**

- Modify: `cheapy/providers/registry.py`
- Modify: `cheapy/providers/manual_fixture/manifest.toml`
- Create: `cheapy/providers/google_fli/__init__.py`
- Create: `cheapy/providers/google_fli/manifest.toml`
- Modify: `cheapy/cli.py`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing registry and CLI tests**

Append to `tests/test_providers.py`:

```python
def test_provider_manifests_include_provider_kind() -> None:
    manifests = discover_provider_manifests()
    kinds_by_name = {manifest.name: manifest.provider_kind for manifest in manifests}

    assert kinds_by_name["manual_fixture"] == "fixture"
    assert kinds_by_name["google_fli"] == "live"


def test_google_fli_manifest_is_discovered_from_package_resources() -> None:
    manifest = _manifest_by_name("google_fli")

    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="google_fli",
        display_name="Google Fli live provider",
        default_enabled=False,
        provider_kind="live",
        module="cheapy.providers.google_fli.provider",
        capabilities=["exact_one_way"],
    )


def test_discover_provider_manifests_requires_provider_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeManifestResource:
        def is_file(self) -> bool:
            return True

        def read_text(self, encoding: str) -> str:
            return (
                'manifest_schema_version = "1"\n'
                'name = "broken_provider"\n'
                'display_name = "Broken provider"\n'
                "default_enabled = true\n"
                'module = "broken.provider"\n'
                'capabilities = ["exact_one_way"]\n'
            )

    class FakeProviderResource:
        name = "broken_provider"

        def is_dir(self) -> bool:
            return True

        def joinpath(self, name: str) -> FakeManifestResource:
            assert name == "manifest.toml"
            return FakeManifestResource()

    class FakeRootResource:
        def iterdir(self) -> list[FakeProviderResource]:
            return [FakeProviderResource()]

    monkeypatch.setattr(registry, "_provider_resource_root", FakeRootResource)

    with pytest.raises(
        ProviderManifestError,
        match="Invalid provider manifest for 'broken_provider'",
    ):
        discover_provider_manifests()


def test_load_search_providers_excludes_fixture_providers() -> None:
    providers = registry.load_search_providers()

    assert providers == []
    assert all(provider.name != "manual_fixture" for provider in providers)
```

Update `tests/test_cli.py::test_providers_list_prints_json` to build providers by name so discovery order does not matter, and assert the current Task 2 provider metadata:

```python
def test_providers_list_prints_json() -> None:
    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    providers = {provider["name"]: provider for provider in payload["providers"]}
    assert payload["status"] == "ok"
    assert providers["manual_fixture"]["provider_kind"] == "fixture"
    assert providers["manual_fixture"]["default_enabled"] is True
    assert providers["manual_fixture"]["enabled"] is True
    assert providers["google_fli"]["provider_kind"] == "live"
    assert providers["google_fli"]["default_enabled"] is False
    assert providers["google_fli"]["enabled"] is False
```

Update existing assertions in `tests/test_providers.py`:

```python
def test_manual_fixture_manifest_is_discovered_from_package_resources() -> None:
    manifests = discover_provider_manifests()

    assert "manual_fixture" in [manifest.name for manifest in manifests]
    manifest = _manifest_by_name("manual_fixture")
    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="manual_fixture",
        display_name="Manual fixture provider",
        default_enabled=True,
        provider_kind="fixture",
        module="cheapy.providers.manual_fixture.provider",
        capabilities=["exact_one_way"],
    )


def test_load_enabled_providers_loads_all_default_enabled_providers() -> None:
    from cheapy.providers.registry import load_enabled_providers

    providers = load_enabled_providers()

    assert [provider.name for provider in providers] == ["manual_fixture"]
    assert providers[0].capabilities == ("exact_one_way",)
```

- [ ] **Step 2: Run registry tests and verify failure**

Run:

```bash
uv run pytest tests/test_providers.py::test_provider_manifests_include_provider_kind tests/test_providers.py::test_google_fli_manifest_is_discovered_from_package_resources tests/test_providers.py::test_discover_provider_manifests_requires_provider_kind tests/test_providers.py::test_load_search_providers_excludes_fixture_providers -v
```

Also run the CLI provider list test:

```bash
uv run pytest tests/test_cli.py::test_providers_list_prints_json -v
```

Expected: FAIL because `provider_kind`, `google_fli`, `load_search_providers()`, and the provider list `provider_kind` output do not exist.

- [ ] **Step 3: Update provider manifests**

Modify `cheapy/providers/manual_fixture/manifest.toml` to:

```toml
manifest_schema_version = "1"
name = "manual_fixture"
display_name = "Manual fixture provider"
default_enabled = true
provider_kind = "fixture"
module = "cheapy.providers.manual_fixture.provider"
capabilities = ["exact_one_way"]
```

Create `cheapy/providers/google_fli/__init__.py`:

```python
"""Google Fli live provider package."""

PROVIDER_NAME = "google_fli"
```

Create `cheapy/providers/google_fli/manifest.toml`:

```toml
manifest_schema_version = "1"
name = "google_fli"
display_name = "Google Fli live provider"
default_enabled = false
provider_kind = "live"
module = "cheapy.providers.google_fli.provider"
capabilities = ["exact_one_way"]
```

- [ ] **Step 4: Update registry model and loader**

Modify `cheapy/providers/registry.py`:

```python
class ProviderManifest(BaseModel):
    """Validated provider manifest loaded from package resources."""

    model_config = ConfigDict(extra="forbid", strict=True)

    manifest_schema_version: Literal["1"]
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    default_enabled: bool
    provider_kind: Literal["live", "fixture"]
    module: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
```

Add below `load_enabled_providers()`:

```python
def load_search_providers() -> list[FlightProvider]:
    """Load bundled providers enabled for normal user-facing search."""
    return [
        load_provider(manifest)
        for manifest in discover_provider_manifests()
        if manifest.default_enabled and manifest.provider_kind != "fixture"
    ]
```

- [ ] **Step 5: Update provider list output**

In `cheapy/cli.py`, add `provider_kind` to each provider dict in `providers_list()`:

```python
{
    "name": manifest.name,
    "display_name": manifest.display_name,
    "capabilities": manifest.capabilities,
    "default_enabled": manifest.default_enabled,
    "enabled": manifest.default_enabled,
    "provider_kind": manifest.provider_kind,
}
```

- [ ] **Step 6: Add a minimal temporary google provider stub**

Create `cheapy/providers/google_fli/provider.py`:

```python
"""Google Fli live provider."""

from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult


class GoogleFliProvider:
    """Live provider backed by upstream fli."""

    name = "google_fli"
    capabilities = ("exact_one_way",)

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        raise NotImplementedError("google_fli provider implementation is added in Task 4")


def create_provider() -> GoogleFliProvider:
    return GoogleFliProvider()
```

This stub remains disabled until Task 4 replaces it with the real provider and enables the manifest.

- [ ] **Step 7: Run registry and CLI tests**

Run:

```bash
uv run pytest tests/test_providers.py::test_provider_manifests_include_provider_kind tests/test_providers.py::test_google_fli_manifest_is_discovered_from_package_resources tests/test_providers.py::test_discover_provider_manifests_requires_provider_kind tests/test_providers.py::test_load_search_providers_excludes_fixture_providers -v
uv run pytest tests/test_cli.py::test_providers_list_prints_json -v
```

Expected: PASS.

- [ ] **Step 8: Run existing provider tests**

Run:

```bash
uv run pytest tests/test_providers.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit registry, manifest, and provider list changes**

Run:

```bash
git add cheapy/providers/registry.py cheapy/providers/manual_fixture/manifest.toml cheapy/providers/google_fli cheapy/cli.py tests/test_providers.py tests/test_cli.py
git commit -m "feat: classify bundled providers" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 3: Build Google Fli Normalizer

**Files:**

- Create: `cheapy/providers/google_fli/normalizer.py`
- Create: `tests/test_google_fli_normalizer.py`

- [ ] **Step 1: Write failing normalizer tests**

Create `tests/test_google_fli_normalizer.py`:

```python
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from cheapy.models import ErrorCode
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.google_fli.normalizer import normalize_flights


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-11",
    )


def _leg(
    *,
    airline: str = "VJ",
    flight_number: str = "VJ801",
    origin: str = "SGN",
    destination: str = "BKK",
) -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(value=airline),
        flight_number=flight_number,
        departure_airport=SimpleNamespace(value=origin),
        arrival_airport=SimpleNamespace(value=destination),
        departure_datetime=datetime(2026, 6, 11, 9, 15),
        arrival_datetime=datetime(2026, 6, 11, 10, 45),
        duration=90,
    )


def _flight(
    *,
    price: float = 88.5,
    currency: str | None = "USD",
    legs: list[SimpleNamespace] | None = None,
    duration: int = 90,
    stops: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        price=price,
        currency=currency,
        duration=duration,
        stops=stops,
        legs=legs if legs is not None else [_leg()],
    )


def test_normalize_flights_maps_google_fli_result_to_contract_offer() -> None:
    offers, errors = normalize_flights([_flight()], _request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "google_fli:SGN-BKK:2026-06-11:1"
    assert offer.price_amount == 88.5
    assert offer.currency == "USD"
    assert offer.provider == "google_fli"
    assert offer.requested_origin == "SGN"
    assert offer.requested_destination == "BKK"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.requested_departure_date == "2026-06-11"
    assert offer.actual_departure_date == "2026-06-11"
    assert offer.total_duration_minutes == 90
    assert offer.stops == 0
    assert offer.fare_details_status == "not_collected"
    assert offer.flags.baggage_unknown is True
    assert [(leg.airline_code, leg.flight_number) for leg in offer.legs] == [
        ("VJ", "VJ801")
    ]


def test_normalize_flights_uses_configured_currency_when_result_currency_is_missing() -> None:
    offers, errors = normalize_flights([_flight(currency=None)], _request(), configured_currency="VND")

    assert errors == []
    assert offers[0].currency == "VND"


def test_normalize_flights_fails_item_when_currency_is_unavailable() -> None:
    offers, errors = normalize_flights([_flight(currency=None)], _request())

    assert offers == []
    assert len(errors) == 1
    error = errors[0]
    assert error.code == ErrorCode.PROVIDER_FAILED
    assert error.details == {
        "provider": "google_fli",
        "capability": "exact_one_way",
        "failure_type": "currency_unavailable",
        "item_index": 1,
    }
    assert error.retryable is False


def test_normalize_flights_skips_malformed_item_without_leaking_payload() -> None:
    bad_flight = SimpleNamespace(
        price="secret-price",
        currency="USD",
        duration=90,
        stops=0,
        legs=[],
        raw_payload="secret raw payload",
    )

    offers, errors = normalize_flights([bad_flight], _request())

    assert offers == []
    assert len(errors) == 1
    payload = errors[0].model_dump_json()
    assert errors[0].details["failure_type"] == "parse_error"
    assert "secret raw payload" not in payload
    assert "secret-price" not in payload
```

- [ ] **Step 2: Run normalizer tests and verify failure**

Run:

```bash
uv run pytest tests/test_google_fli_normalizer.py -v
```

Expected: FAIL because `cheapy.providers.google_fli.normalizer` does not exist.

- [ ] **Step 3: Implement normalizer**

Create `cheapy/providers/google_fli/normalizer.py`:

```python
"""Normalize upstream fli results into Cheapy Contract V1 offers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest


PROVIDER_NAME = "google_fli"
CAPABILITY = "exact_one_way"


def normalize_flights(
    flights: list[object],
    request: ProviderExactOneWayRequest,
    *,
    configured_currency: str | None = None,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    """Convert upstream fli flight result objects into Contract V1 offers."""
    offers: list[FlightOfferV1] = []
    errors: list[ErrorV1] = []
    for index, flight in enumerate(flights, start=1):
        try:
            offers.append(
                _normalize_flight(
                    flight,
                    request,
                    index=index,
                    configured_currency=configured_currency,
                )
            )
        except _ItemNormalizationError as exc:
            errors.append(exc.error)
    return offers, errors


class _ItemNormalizationError(Exception):
    """Internal wrapper for a structured item-level normalization error."""

    def __init__(self, error: ErrorV1) -> None:
        super().__init__(error.message_en)
        self.error = error


def _normalize_flight(
    flight: object,
    request: ProviderExactOneWayRequest,
    *,
    index: int,
    configured_currency: str | None,
) -> FlightOfferV1:
    currency = _currency(flight, configured_currency=configured_currency)
    if currency is None:
        raise _ItemNormalizationError(_currency_unavailable_error(index))

    try:
        legs = [_normalize_leg(leg) for leg in _attr(flight, "legs")]
        if not legs:
            raise ValueError("flight has no legs")
        first_leg = legs[0]
        last_leg = legs[-1]
        price_amount = float(_attr(flight, "price"))
        duration = int(_attr(flight, "duration"))
        stops = int(_attr(flight, "stops"))
    except Exception as exc:
        raise _ItemNormalizationError(_parse_error(index, exc)) from exc

    return FlightOfferV1(
        offer_id=f"{PROVIDER_NAME}:{request.origin}-{request.destination}:{request.departure_date}:{index}",
        price_amount=price_amount,
        currency=currency,
        comparable=True,
        rank_within_currency=index,
        global_rank=index,
        provider=PROVIDER_NAME,
        requested_origin=request.origin,
        requested_destination=request.destination,
        actual_origin=first_leg.origin,
        actual_destination=last_leg.destination,
        requested_departure_date=request.departure_date,
        actual_departure_date=first_leg.departure_time[:10],
        departure_offset_days=0,
        requested_return_date=None,
        actual_return_date=None,
        return_offset_days=None,
        legs=legs,
        total_duration_minutes=duration,
        stops=stops,
        flags=OfferFlagsV1(),
        fare_details_status="not_collected",
    )


def _normalize_leg(leg: object) -> FlightLegV1:
    departure_time = _iso_datetime(_attr(leg, "departure_datetime"))
    arrival_time = _iso_datetime(_attr(leg, "arrival_datetime"))
    return FlightLegV1(
        origin=_enum_value(_attr(leg, "departure_airport")),
        destination=_enum_value(_attr(leg, "arrival_airport")),
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=_enum_value(_attr(leg, "airline")),
        flight_number=str(_attr(leg, "flight_number")),
        duration_minutes=int(_attr(leg, "duration")),
    )


def _currency(flight: object, *, configured_currency: str | None) -> str | None:
    raw_currency = getattr(flight, "currency", None)
    if isinstance(raw_currency, str) and len(raw_currency.strip()) == 3:
        return raw_currency.strip().upper()
    if configured_currency is not None and len(configured_currency.strip()) == 3:
        return configured_currency.strip().upper()
    return None


def _attr(value: object, name: str) -> Any:
    return getattr(value, name)


def _enum_value(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _iso_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    return str(value)


def _currency_unavailable_error(index: int) -> ErrorV1:
    return _error(
        message_en="Provider result did not include a reliable currency.",
        failure_type="currency_unavailable",
        item_index=index,
    )


def _parse_error(index: int, exc: Exception) -> ErrorV1:
    return _error(
        message_en="Provider result could not be normalized.",
        failure_type="parse_error",
        item_index=index,
        exception_type=type(exc).__name__,
    )


def _error(
    *,
    message_en: str,
    failure_type: str,
    item_index: int,
    exception_type: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": CAPABILITY,
        "failure_type": failure_type,
        "item_index": item_index,
    }
    if exception_type is not None:
        details["exception_type"] = exception_type
    return ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=False,
    )
```

- [ ] **Step 4: Run normalizer tests**

Run:

```bash
uv run pytest tests/test_google_fli_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit normalizer**

Run:

```bash
git add cheapy/providers/google_fli/normalizer.py tests/test_google_fli_normalizer.py
git commit -m "feat: normalize google fli results" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 4: Implement Google Fli Adapter And Provider

**Files:**

- Create: `cheapy/providers/google_fli/adapter.py`
- Modify: `cheapy/providers/google_fli/provider.py`
- Modify: `cheapy/providers/google_fli/manifest.toml`
- Modify: `tests/test_providers.py`
- Create: `tests/test_google_fli_provider.py`

- [ ] **Step 1: Write failing adapter/provider tests**

Create `tests/test_google_fli_provider.py`:

```python
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from cheapy.models import ErrorCode, ProviderStatusCode
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.google_fli.adapter import (
    GoogleFliProviderError,
    build_search_filters,
)
from cheapy.providers.google_fli.provider import GoogleFliProvider


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-11",
    )


def _leg() -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(value="VJ"),
        flight_number="VJ801",
        departure_airport=SimpleNamespace(value="SGN"),
        arrival_airport=SimpleNamespace(value="BKK"),
        departure_datetime=datetime(2026, 6, 11, 9, 15),
        arrival_datetime=datetime(2026, 6, 11, 10, 45),
        duration=90,
    )


def _flight(currency: str | None = "USD") -> SimpleNamespace:
    return SimpleNamespace(
        price=88.5,
        currency=currency,
        duration=90,
        stops=0,
        legs=[_leg()],
    )


class FakeAdapter:
    configured_currency = None

    def __init__(self, result: list[object] | Exception) -> None:
        self.result = result
        self.seen_request: ProviderExactOneWayRequest | None = None

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> list[object]:
        self.seen_request = request
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_build_search_filters_maps_contract_request_to_fli_filters() -> None:
    filters = build_search_filters(_request())

    assert filters.trip_type.name == "ONE_WAY"
    assert filters.seat_type.name == "ECONOMY"
    assert filters.sort_by.name == "CHEAPEST"
    assert filters.passenger_info.adults == 1
    assert filters.flight_segments[0].departure_airport[0][0].name == "SGN"
    assert filters.flight_segments[0].arrival_airport[0][0].name == "BKK"
    assert filters.flight_segments[0].travel_date == "2026-06-11"


def test_google_fli_provider_returns_success_result() -> None:
    adapter = FakeAdapter([_flight()])
    provider = GoogleFliProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.seen_request == _request()
    assert result.provider_name == "google_fli"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert [offer.provider for offer in result.offers] == ["google_fli"]
    assert result.duration_ms >= 0


def test_google_fli_provider_treats_empty_results_as_success() -> None:
    provider = GoogleFliProvider(adapter=FakeAdapter([]), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert result.offers == []
    assert result.errors == []


def test_google_fli_provider_fails_when_currency_is_unavailable() -> None:
    provider = GoogleFliProvider(adapter=FakeAdapter([_flight(currency=None)]), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details["failure_type"] == "currency_unavailable"


def test_google_fli_provider_maps_timeout() -> None:
    provider = GoogleFliProvider(
        adapter=FakeAdapter(TimeoutError("secret timeout details")),
        timeout_seconds=1,
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].details["failure_type"] == "timeout"
    assert "secret timeout details" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("error", "code", "failure_type", "retryable"),
    [
        (
            GoogleFliProviderError(
                failure_type="dependency_unavailable",
                message_en="Google Fli dependency is unavailable.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=False,
            ),
            ErrorCode.PROVIDER_FAILED,
            "dependency_unavailable",
            False,
        ),
        (
            GoogleFliProviderError(
                failure_type="transport_error",
                message_en="Google Fli transport failed.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=True,
            ),
            ErrorCode.PROVIDER_FAILED,
            "transport_error",
            True,
        ),
    ],
)
def test_google_fli_provider_maps_structured_adapter_errors(
    error: GoogleFliProviderError,
    code: ErrorCode,
    failure_type: str,
    retryable: bool,
) -> None:
    provider = GoogleFliProvider(adapter=FakeAdapter(error), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.errors[0].code == code
    assert result.errors[0].details["failure_type"] == failure_type
    assert result.errors[0].retryable is retryable
```

- [ ] **Step 2: Run adapter/provider tests and verify failure**

Run:

```bash
uv run pytest tests/test_google_fli_provider.py -v
```

Expected: FAIL because `adapter.py` and provider implementation do not exist.

- [ ] **Step 3: Implement adapter**

Create `cheapy/providers/google_fli/adapter.py`:

```python
"""Adapter for upstream fli Google Flights search."""

from __future__ import annotations

from typing import Any

from cheapy.models import ErrorCode
from cheapy.providers.base import ProviderExactOneWayRequest


class GoogleFliProviderError(Exception):
    """Structured provider-local error safe to map into Contract V1."""

    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        error_code: ErrorCode,
        retryable: bool,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
        self.error_code = error_code
        self.retryable = retryable
        self.exception_type = exception_type


class GoogleFliAdapter:
    """Sync adapter around upstream fli search."""

    configured_currency: str | None = None

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> list[object]:
        try:
            search = _search_class()()
            filters = build_search_filters(request)
            results = search.search(filters)
        except GoogleFliProviderError:
            raise
        except TimeoutError as exc:
            raise exc
        except Exception as exc:
            raise GoogleFliProviderError(
                failure_type="transport_error",
                message_en="Google Fli transport failed.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=True,
                exception_type=type(exc).__name__,
            ) from exc

        if isinstance(results, list):
            return results
        return list(results)


def build_search_filters(request: ProviderExactOneWayRequest) -> Any:
    """Build upstream fli search filters for exact one-way search."""
    try:
        from fli.models import (
            Airport,
            FlightSearchFilters,
            FlightSegment,
            PassengerInfo,
            SeatType,
            SortBy,
            TripType,
        )
    except Exception as exc:
        raise GoogleFliProviderError(
            failure_type="dependency_unavailable",
            message_en="Google Fli dependency is unavailable.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
            exception_type=type(exc).__name__,
        ) from exc

    origin = _airport(Airport, request.origin)
    destination = _airport(Airport, request.destination)
    return FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(
            adults=request.passengers.adults,
            children=request.passengers.children,
            infants_in_seat=request.passengers.infants_in_seat,
            infants_on_lap=request.passengers.infants_on_lap,
        ),
        flight_segments=[
            FlightSegment(
                departure_airport=[[origin, 0]],
                arrival_airport=[[destination, 0]],
                travel_date=request.departure_date,
            )
        ],
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
    )


def _airport(airport_enum: Any, iata: str) -> Any:
    try:
        return airport_enum[iata]
    except KeyError as exc:
        raise GoogleFliProviderError(
            failure_type="unsupported_airport_by_upstream",
            message_en="Google Fli does not support the requested airport.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        ) from exc


def _search_class() -> Any:
    try:
        from fli.search import SearchFlights
    except Exception as exc:
        raise GoogleFliProviderError(
            failure_type="dependency_unavailable",
            message_en="Google Fli dependency is unavailable.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
            exception_type=type(exc).__name__,
        ) from exc
    return SearchFlights
```

- [ ] **Step 4: Implement provider**

Replace `cheapy/providers/google_fli/provider.py`:

```python
"""Google Fli live provider."""

from __future__ import annotations

import asyncio
from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers.google_fli.adapter import (
    GoogleFliAdapter,
    GoogleFliProviderError,
)
from cheapy.providers.google_fli.normalizer import CAPABILITY, PROVIDER_NAME, normalize_flights


class GoogleFliProvider:
    """Live provider backed by upstream fli."""

    name = PROVIDER_NAME
    capabilities = (CAPABILITY,)

    def __init__(
        self,
        *,
        adapter: object | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._adapter = adapter if adapter is not None else GoogleFliAdapter()
        self._timeout_seconds = timeout_seconds

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        started = perf_counter()
        try:
            flights = await asyncio.wait_for(
                asyncio.to_thread(self._adapter.search_exact_one_way, request),
                timeout=self._timeout_seconds,
            )
            offers, errors = normalize_flights(
                flights,
                request,
                configured_currency=getattr(self._adapter, "configured_currency", None),
            )
        except TimeoutError:
            return self._failed_result(
                started,
                _provider_error(
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    message_en="Google Fli provider timed out.",
                    failure_type="timeout",
                    retryable=True,
                ),
            )
        except GoogleFliProviderError as exc:
            return self._failed_result(
                started,
                _provider_error(
                    code=exc.error_code,
                    message_en=exc.message_en,
                    failure_type=exc.failure_type,
                    retryable=exc.retryable,
                    exception_type=exc.exception_type,
                ),
            )
        except Exception as exc:
            return self._failed_result(
                started,
                _provider_error(
                    code=ErrorCode.PROVIDER_FAILED,
                    message_en="Google Fli provider raised an unexpected exception.",
                    failure_type="unexpected_error",
                    retryable=False,
                    exception_type=type(exc).__name__,
                ),
            )

        if errors and offers:
            status = ProviderStatusCode.PARTIAL
        elif errors:
            status = ProviderStatusCode.FAILED
        else:
            status = ProviderStatusCode.SUCCESS

        return ProviderResult(
            provider_name=self.name,
            capability=CAPABILITY,
            status=status,
            offers=offers,
            warnings=[],
            errors=errors,
            duration_ms=_duration_ms(started),
            retryable=any(error.retryable for error in errors),
        )

    def _failed_result(self, started: float, error: ErrorV1) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            capability=CAPABILITY,
            status=ProviderStatusCode.FAILED,
            offers=[],
            warnings=[],
            errors=[error],
            duration_ms=_duration_ms(started),
            retryable=error.retryable,
        )


def create_provider() -> GoogleFliProvider:
    return GoogleFliProvider()


def _provider_error(
    *,
    code: ErrorCode,
    message_en: str,
    failure_type: str,
    retryable: bool,
    exception_type: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": CAPABILITY,
        "failure_type": failure_type,
    }
    if exception_type is not None:
        details["exception_type"] = exception_type
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
```

- [ ] **Step 5: Enable the live provider after replacing the stub**

Modify `cheapy/providers/google_fli/manifest.toml`:

```toml
manifest_schema_version = "1"
name = "google_fli"
display_name = "Google Fli live provider"
default_enabled = true
provider_kind = "live"
module = "cheapy.providers.google_fli.provider"
capabilities = ["exact_one_way"]
```

Update `tests/test_providers.py` so `test_google_fli_manifest_is_discovered_from_package_resources` expects `default_enabled=True`, `test_load_enabled_providers_loads_all_default_enabled_providers` expects `["google_fli", "manual_fixture"]`, and `test_load_search_providers_excludes_fixture_providers` verifies `load_search_providers()` returns `["google_fli"]`.

- [ ] **Step 6: Run provider tests**

Run:

```bash
uv run pytest tests/test_google_fli_provider.py -v
```

Expected: PASS.

- [ ] **Step 7: Verify search provider loading**

Run:

```bash
uv run pytest tests/test_providers.py::test_google_fli_manifest_is_discovered_from_package_resources tests/test_providers.py::test_load_enabled_providers_loads_all_default_enabled_providers tests/test_providers.py::test_load_search_providers_excludes_fixture_providers -v
```

Expected: PASS, and `load_search_providers()` returns `google_fli` because the real provider has replaced the temporary stub.

- [ ] **Step 8: Run provider package tests**

Run:

```bash
uv run pytest tests/test_providers.py tests/test_google_fli_normalizer.py tests/test_google_fli_provider.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit google_fli provider**

Run:

```bash
git add cheapy/providers/google_fli tests/test_providers.py tests/test_google_fli_normalizer.py tests/test_google_fli_provider.py
git commit -m "feat: add google fli provider" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 5: Route Normal Search To Live Providers And Re-rank Offers

**Files:**

- Modify: `cheapy/search.py`
- Modify: `tests/test_search.py`

- [ ] **Step 1: Write failing search tests**

Append to `tests/test_search.py`:

```python
def test_search_exact_uses_search_providers_not_fixture_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_result = ProviderResult(
        provider_name="google_fli",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="google_fli:expensive",
                provider="google_fli",
                currency="USD",
                price_amount=120.0,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=1,
        retryable=False,
    )

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(live_result)],
    )
    monkeypatch.setattr(
        "cheapy.search.load_enabled_providers",
        lambda: (_ for _ in ()).throw(AssertionError("fixture loader must not be used")),
        raising=False,
    )

    response = search_exact(_request(origin="CXR", destination="SGN"))

    assert response.status == SearchStatus.SUCCESS
    assert [offer.provider for offer in response.offers] == ["google_fli"]
    assert all(offer.provider != "manual_fixture" for offer in response.offers)


def test_search_exact_reassigns_global_ranks_after_sorting_and_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ProviderResult(
        provider_name="google_fli",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="google_fli:second",
                provider="google_fli",
                currency="USD",
                price_amount=200.0,
            ).model_copy(update={"rank_within_currency": 99, "global_rank": 99}),
            _offer(
                offer_id="google_fli:first",
                provider="google_fli",
                currency="USD",
                price_amount=100.0,
            ).model_copy(update={"rank_within_currency": 88, "global_rank": 88}),
        ],
        warnings=[],
        errors=[],
        duration_ms=1,
        retryable=False,
    )

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(result)],
    )

    response = search_exact(_request(max_results=1))

    assert [offer.offer_id for offer in response.offers] == ["google_fli:first"]
    assert response.offers[0].comparable is True
    assert response.offers[0].rank_within_currency == 1
    assert response.offers[0].global_rank == 1


def test_search_exact_mixed_currency_ranks_within_currency_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ProviderResult(
        provider_name="google_fli",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="google_fli:usd",
                provider="google_fli",
                currency="USD",
                price_amount=100.0,
            ),
            _offer(
                offer_id="google_fli:vnd",
                provider="google_fli",
                currency="VND",
                price_amount=1000000.0,
            ),
        ],
        warnings=[],
        errors=[],
        duration_ms=1,
        retryable=False,
    )

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(result)],
    )

    response = search_exact(_request())

    assert response.mixed_currency is True
    assert all(offer.comparable is False for offer in response.offers)
    assert all(offer.global_rank is None for offer in response.offers)
    assert [offer.rank_within_currency for offer in response.offers] == [1, 1]
```

- [ ] **Step 2: Run search tests and verify failure**

Run:

```bash
uv run pytest tests/test_search.py::test_search_exact_uses_search_providers_not_fixture_loader tests/test_search.py::test_search_exact_reassigns_global_ranks_after_sorting_and_truncation tests/test_search.py::test_search_exact_mixed_currency_ranks_within_currency_only -v
```

Expected: FAIL because `search.py` still imports and uses `load_enabled_providers`, and ranks are not reassigned.

- [ ] **Step 3: Update search provider loader**

In `cheapy/search.py`, replace the registry import:

```python
from cheapy.providers.registry import (
    ProviderLoadError,
    ProviderManifestError,
    load_search_providers,
)
```

Replace:

```python
providers = load_enabled_providers()
```

with:

```python
providers = load_search_providers()
```

Update existing test monkeypatches in `tests/test_search.py` from:

```python
monkeypatch.setattr("cheapy.search.load_enabled_providers", ...)
```

to:

```python
monkeypatch.setattr("cheapy.search.load_search_providers", ...)
```

- [ ] **Step 4: Add ranking helper in search**

In `_response_from_provider_results`, replace:

```python
returned_offers = _sort_offers(offers)[: request.max_results]
```

with:

```python
returned_offers = _rank_offers(_sort_offers(offers)[: request.max_results])
```

Add below `_sort_offers`:

```python
def _rank_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers}
    if len(currencies) <= 1:
        return [
            offer.model_copy(
                update={
                    "comparable": True,
                    "rank_within_currency": index,
                    "global_rank": index,
                }
            )
            for index, offer in enumerate(offers, start=1)
        ]

    currency_rank_counts: dict[str, int] = {}
    ranked: list[FlightOfferV1] = []
    for offer in offers:
        rank = currency_rank_counts.get(offer.currency, 0) + 1
        currency_rank_counts[offer.currency] = rank
        ranked.append(
            offer.model_copy(
                update={
                    "comparable": False,
                    "rank_within_currency": rank,
                    "global_rank": None,
                }
            )
        )
    return ranked
```

- [ ] **Step 5: Run search tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit search routing and ranking**

Run:

```bash
git add cheapy/search.py tests/test_search.py
git commit -m "feat: route search to live providers" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 6: Update Provider CLI For Live-Safe Checks

**Files:**

- Modify: `cheapy/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `tests/test_cli.py`:

```python
def test_providers_test_default_does_not_run_live_provider(monkeypatch) -> None:
    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    providers = {provider["name"]: provider for provider in payload["providers"]}
    assert providers["manual_fixture"]["status"] == "success"
    assert providers["manual_fixture"]["live_smoke"] == "not_applicable"
    assert providers["google_fli"]["status"] == "skipped"
    assert providers["google_fli"]["live_smoke"] == "not_run"


def test_providers_test_live_requires_environment_gate(monkeypatch) -> None:
    monkeypatch.delenv("CHEAPY_RUN_LIVE_TESTS", raising=False)

    result = runner.invoke(app, ["providers", "test", "--live"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "LIVE_TESTS_NOT_ENABLED",
        "message": "Live provider tests require CHEAPY_RUN_LIVE_TESTS=1.",
        "suggestion": "Set CHEAPY_RUN_LIVE_TESTS=1 and rerun 'cheapy providers test --live'.",
    }


def test_providers_test_live_reports_provider_failure(monkeypatch) -> None:
    class FailingLiveProvider:
        name = "google_fli"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            return ProviderResult(
                provider_name=self.name,
                capability="exact_one_way",
                status=ProviderStatusCode.FAILED,
                offers=[],
                warnings=[],
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_FAILED,
                        severity=Severity.ERROR,
                        message_en="Live provider failed.",
                    )
                ],
                duration_ms=1,
                retryable=False,
            )

    monkeypatch.setenv("CHEAPY_RUN_LIVE_TESTS", "1")
    monkeypatch.setattr("cheapy.cli.load_live_test_providers", lambda: [FailingLiveProvider()])

    result = runner.invoke(app, ["providers", "test", "--live"])

    assert result.exit_code == 1
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["code"] == "PROVIDER_LIVE_TEST_FAILED"
```

Update existing provider check output tests in `tests/test_cli.py`:

```python
def test_providers_test_prints_json() -> None:
    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    providers = {provider["name"]: provider for provider in payload["providers"]}
    assert payload["status"] == "ok"
    assert providers["manual_fixture"]["status"] == "success"
    assert providers["manual_fixture"]["live_smoke"] == "not_applicable"
    assert providers["google_fli"]["status"] == "skipped"
    assert providers["google_fli"]["live_smoke"] == "not_run"


def test_providers_test_human_prints_success_report() -> None:
    result = runner.invoke(app, ["providers", "test", "--human"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "manual_fixture fixture exact_one_way: success" in result.stdout
    assert "google_fli live exact_one_way: skipped" in result.stdout
    assert result.stdout.endswith("status: ok\n")
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_providers_test_default_does_not_run_live_provider tests/test_cli.py::test_providers_test_live_requires_environment_gate tests/test_cli.py::test_providers_test_live_reports_provider_failure -v
```

Expected: FAIL because live-safe provider checks and `--live` do not exist.

- [ ] **Step 3: Add live option and environment guard**

In imports:

```python
import os
```

Add:

```python
LIVE_TEST_ENV = "CHEAPY_RUN_LIVE_TESTS"
```

Change `providers_test` signature:

```python
def providers_test(
    human: bool = typer.Option(
        False,
        "--human",
        help="Print a concise human-readable provider report.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Run opt-in live provider smoke checks.",
    ),
) -> None:
```

At the start of `providers_test`, after loading providers:

```python
if live and os.environ.get(LIVE_TEST_ENV) != "1":
    _json_echo(
        _error_payload(
            "LIVE_TESTS_NOT_ENABLED",
            "Live provider tests require CHEAPY_RUN_LIVE_TESTS=1.",
            "Set CHEAPY_RUN_LIVE_TESTS=1 and rerun 'cheapy providers test --live'.",
        ),
        err=True,
    )
    raise typer.Exit(code=2)
```

- [ ] **Step 4: Add provider check helpers**

Replace `_run_provider_checks` with:

```python
async def _run_provider_checks(
    providers: list[Any],
    *,
    live: bool,
) -> list[dict[str, Any]]:
    request = _provider_fixture_request()
    reports: list[dict[str, Any]] = []
    for provider in providers:
        provider_kind = _provider_kind(provider.name)
        if provider_kind == "live" and not live:
            reports.append(
                {
                    "name": provider.name,
                    "provider_kind": provider_kind,
                    "capability": "exact_one_way",
                    "status": ProviderStatusCode.SKIPPED.value,
                    "offer_count": 0,
                    "error_count": 0,
                    "live_smoke": "not_run",
                }
            )
            continue
        check_request = _live_smoke_request() if provider_kind == "live" else request
        result = await provider.search_exact_one_way(check_request)
        reports.append(
            {
                "name": result.provider_name,
                "provider_kind": provider_kind,
                "capability": result.capability,
                "status": result.status.value,
                "offer_count": len(result.offers),
                "error_count": len(result.errors),
                "live_smoke": "run" if provider_kind == "live" else "not_applicable",
            }
        )
    return reports
```

Add helper functions:

```python
def _provider_kind(provider_name: str) -> str:
    for manifest in discover_provider_manifests():
        if manifest.name == provider_name:
            return manifest.provider_kind
    return "fixture"


def _live_smoke_request() -> ProviderExactOneWayRequest:
    from datetime import date, timedelta

    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date=(date.today() + timedelta(days=30)).isoformat(),
    )
```

Update the call site:

```python
reports = asyncio.run(_run_provider_checks(providers, live=live))
```

Add:

```python
def load_live_test_providers() -> list[Any]:
    return load_enabled_providers()
```

When `live` is true, load providers through `load_live_test_providers()` so tests can monkeypatch only the live lane:

```python
providers = load_live_test_providers() if live else load_enabled_providers()
```

- [ ] **Step 5: Update failure classification**

Replace:

```python
if any(report["status"] != ProviderStatusCode.SUCCESS.value for report in reports):
```

with:

```python
failed_reports = [
    report
    for report in reports
    if report["status"] == ProviderStatusCode.FAILED.value
]
if failed_reports:
    code = "PROVIDER_LIVE_TEST_FAILED" if live else "PROVIDER_TEST_FAILED"
    message = (
        "One or more live provider checks failed."
        if live
        else "One or more provider checks failed."
    )
    _json_echo(
        _error_payload(
            code,
            message,
            "Run 'cheapy providers test --human' for a concise provider report.",
        ),
        err=True,
    )
    raise typer.Exit(code=1)
```

In the unexpected exception handler, use `PROVIDER_LIVE_TEST_ERROR` when `live` is true.

- [ ] **Step 6: Update human report output**

Modify `_echo_provider_human_report` to include provider kind and live state:

```python
def _echo_provider_human_report(reports: list[dict[str, Any]], *, status: str) -> None:
    typer.echo("Cheapy providers test")
    for report in reports:
        typer.echo(
            f"{report['name']} {report['provider_kind']} {report['capability']}: "
            f"{report['status']} (offers: {report['offer_count']}, "
            f"errors: {report['error_count']}, live: {report['live_smoke']})"
        )
    typer.echo(f"status: {status}")
```

- [ ] **Step 7: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit CLI updates**

Run:

```bash
git add cheapy/cli.py tests/test_cli.py
git commit -m "feat: add live-safe provider checks" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 7: Update MCP Annotation And Test Seams

**Files:**

- Modify: `cheapy/mcp.py`
- Modify: `tests/test_mcp.py`

- [ ] **Step 1: Write failing MCP annotation and in-process behavior test**

Modify `tests/test_mcp.py`:

Add imports:

```python
from cheapy.mcp import create_mcp_server
```

Add helpers:

```python
def _mcp_tool():
    server = create_mcp_server()
    tool = server._tool_manager.get_tool("search_cheapest_flights")
    assert tool is not None
    return tool
```

Add test:

```python
def test_mcp_search_tool_annotation_marks_open_world() -> None:
    tool = _mcp_tool()

    assert tool.annotations["openWorldHint"] is True
```

Replace the subprocess tool-call test with an in-process test that monkeypatches search:

```python
def test_mcp_search_tool_returns_structured_contract_response(monkeypatch) -> None:
    def fake_search_exact(request):
        return SearchResponseV1.model_validate(
            {
                "schema_version": "1",
                "status": "success",
                "request_id": "exact:CXR:SGN:2026-07-10:exact:1:0:0:0:5",
                "offers": [],
                "warnings": [],
                "errors": [],
                "provider_statuses": [],
                "search_plan": {
                    "search_mode": "exact",
                    "planned_candidate_count": 1,
                    "executed_candidate_count": 1,
                    "planned_provider_call_count": 1,
                    "executed_provider_call_count": 1,
                    "candidate_count_by_family": {"exact": 1},
                    "provider_call_count_by_family": {"exact": 1},
                    "truncated": False,
                    "truncated_families": [],
                    "candidate_families": ["exact"],
                },
                "mixed_currency": False,
                "currency_groups": [],
                "currency_notes": [],
                "candidates": None,
            }
        )

    monkeypatch.setattr("cheapy.mcp.search_exact", fake_search_exact)
    tool = _mcp_tool()
    payload = asyncio.run(
        tool.run(
            {
                "schema_version": "1",
                "origin": "CXR",
                "destination": "SGN",
                "departure_date": "2026-07-10",
                "return_date": None,
                "search_mode": "exact",
                "passengers": {
                    "adults": 1,
                    "children": 0,
                    "infants_on_lap": 0,
                    "infants_in_seat": 0,
                },
                "max_results": 5,
            },
            convert_result=True,
        )
    )

    response = SearchResponseV1.model_validate(payload)
    assert response.status == SearchStatus.SUCCESS
```

Keep subprocess tests for initialize/list-tools/schema only. Do not call `session.call_tool(...)` from subprocess default tests.

- [ ] **Step 2: Run MCP tests and verify failure**

Run:

```bash
uv run pytest tests/test_mcp.py::test_mcp_search_tool_annotation_marks_open_world tests/test_mcp.py::test_mcp_search_tool_returns_structured_contract_response -v
```

Expected: FAIL because annotation is still `False` and the old subprocess behavior test may still call the live path.

- [ ] **Step 3: Update MCP annotation**

In `cheapy/mcp.py`, change:

```python
"openWorldHint": False,
```

to:

```python
"openWorldHint": True,
```

- [ ] **Step 4: Ensure in-process tool tests patch `search_exact`**

In `tests/test_mcp.py`, ensure all tests that call tool behavior use `_mcp_tool().run(...)` with monkeypatched `cheapy.mcp.search_exact` or invalid input that fails before search execution. Keep `_with_mcp_session` tests limited to:

- `list_tools`
- schema inspection
- protocol cleanliness through initialize/list-tools

- [ ] **Step 5: Run MCP tests**

Run:

```bash
uv run pytest tests/test_mcp.py -v
```

Expected: PASS without live network calls.

- [ ] **Step 6: Commit MCP updates**

Run:

```bash
git add cheapy/mcp.py tests/test_mcp.py
git commit -m "test: keep mcp default tests offline" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 8: Update Agent Instructions For Provider Attribution

**Files:**

- Modify: `cheapy/agent_hooks.py`
- Modify: `.codex/skills/cheapy/SKILL.md`
- Modify: `tests/test_agent_hooks.py`

- [ ] **Step 1: Write failing agent instruction assertions**

In `tests/test_agent_hooks.py`, update the shared assertion helper that checks Gate 6 instruction text to include:

```python
assert "Use each offer's `provider` field when explaining where a fare came from." in text
assert "Choose the cheapest result from the returned `offers` list when currencies are comparable." in text
```

- [ ] **Step 2: Run agent hook tests and verify failure**

Run:

```bash
uv run pytest tests/test_agent_hooks.py -v
```

Expected: FAIL because the new provider-attribution instructions are absent.

- [ ] **Step 3: Update managed instruction body**

In `cheapy/agent_hooks.py`, add these bullets to `INSTRUCTION_BODY` after `Do not ask the user to choose providers.`:

```text
- Use each offer's `provider` field when explaining where a fare came from.
- Choose the cheapest result from the returned `offers` list when currencies are comparable.
```

Update the managed block in `.codex/skills/cheapy/SKILL.md` with the same two bullets in the same location.

- [ ] **Step 4: Run agent hook tests**

Run:

```bash
uv run pytest tests/test_agent_hooks.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit instruction updates**

Run:

```bash
git add cheapy/agent_hooks.py .codex/skills/cheapy/SKILL.md tests/test_agent_hooks.py
git commit -m "docs: teach agents provider attribution" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 9: Update Packaging Tests And Live Smoke Test

**Files:**

- Modify: `tests/test_package_data.py`
- Create: `tests/test_live_google_fli.py`

- [ ] **Step 1: Update package data test expectations**

Modify `tests/test_package_data.py` wheel checks:

```python
assert "cheapy/providers/manual_fixture/manifest.toml" in names
assert "cheapy/providers/google_fli/manifest.toml" in names
```

Modify the resource script:

```python
manual_manifest = files("cheapy.providers").joinpath("manual_fixture", "manifest.toml").read_text(encoding="utf-8")
google_manifest = files("cheapy.providers").joinpath("google_fli", "manifest.toml").read_text(encoding="utf-8")

assert 'name = "manual_fixture"' in manual_manifest
assert 'provider_kind = "fixture"' in manual_manifest
assert 'name = "google_fli"' in google_manifest
assert 'provider_kind = "live"' in google_manifest
```

Modify installed `providers list` assertion:

```python
provider_payload = json.loads(list_result.stdout)
providers = {provider["name"]: provider for provider in provider_payload["providers"]}
assert providers["manual_fixture"]["provider_kind"] == "fixture"
assert providers["google_fli"]["provider_kind"] == "live"
```

Modify installed `providers test` assertion:

```python
test_payload = json.loads(test_result.stdout)
providers = {provider["name"]: provider for provider in test_payload["providers"]}
assert providers["manual_fixture"]["status"] == "success"
assert providers["google_fli"]["live_smoke"] == "not_run"
```

- [ ] **Step 2: Add opt-in live smoke test**

Create `tests/test_live_google_fli.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
import os

import pytest

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.google_fli.provider import create_provider


pytestmark = pytest.mark.live


if os.environ.get("CHEAPY_RUN_LIVE_TESTS") != "1":
    pytest.skip(
        "Set CHEAPY_RUN_LIVE_TESTS=1 to run live provider smoke tests.",
        allow_module_level=True,
    )


def test_google_fli_live_smoke_returns_structured_result() -> None:
    provider = create_provider()
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date=(date.today() + timedelta(days=30)).isoformat(),
    )

    import asyncio

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.provider_name == "google_fli"
    assert result.capability == "exact_one_way"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    for offer in result.offers:
        assert offer.provider == "google_fli"
```

- [ ] **Step 3: Run package and live marker tests**

Run:

```bash
uv run pytest tests/test_package_data.py -v
uv run pytest tests/test_live_google_fli.py -v
```

Expected:

```text
tests/test_package_data.py passes when dependency cache is warm.
tests/test_live_google_fli.py is skipped unless CHEAPY_RUN_LIVE_TESTS=1.
```

If `tests/test_package_data.py` fails on offline dependency cache, run:

```bash
uv sync --extra dev
uv run pytest tests/test_package_data.py -v
```

Expected: PASS after cache is warm.

- [ ] **Step 4: Commit packaging and live tests**

Run:

```bash
git add tests/test_package_data.py tests/test_live_google_fli.py
git commit -m "test: cover google fli packaging" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds.

---

### Task 10: Final Verification

**Files:**

- No new files.
- Verify all changed files from Tasks 1-9.

- [ ] **Step 1: Run focused test lanes**

Run:

```bash
uv run pytest tests/test_providers.py tests/test_google_fli_normalizer.py tests/test_google_fli_provider.py -v
uv run pytest tests/test_search.py tests/test_mcp.py tests/test_cli.py -v
uv run pytest tests/test_agent_hooks.py tests/test_package_data.py -v
```

Expected: PASS. If `tests/test_package_data.py` fails on offline cache, run `uv sync --extra dev` and rerun the package test.

- [ ] **Step 2: Run default full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS with no live network calls. `tests/test_live_google_fli.py` is skipped unless `CHEAPY_RUN_LIVE_TESTS=1`.

- [ ] **Step 3: Verify CLI provider output**

Run:

```bash
uv run cheapy providers list
uv run cheapy providers test
```

Expected `providers list` stdout contains both provider kinds:

```json
{
  "status": "ok",
  "providers": [
    {
      "name": "google_fli",
      "provider_kind": "live"
    },
    {
      "name": "manual_fixture",
      "provider_kind": "fixture"
    }
  ]
}
```

Expected `providers test` exits `0`, does not make live network calls, and reports `google_fli` with `live_smoke="not_run"`.

- [ ] **Step 4: Verify MCP schema without live call**

Run:

```bash
uv run pytest tests/test_mcp.py::test_mcp_lists_only_search_cheapest_flights_tool tests/test_mcp.py::test_mcp_search_tool_uses_top_level_contract_fields tests/test_mcp.py::test_mcp_search_tool_annotation_marks_open_world -v
```

Expected: PASS.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status --short
git log --oneline -10
```

Expected: working tree is clean, with Gate 7 commits on top of the design commits.

---

## Self-Review Checklist

- Spec coverage:
  - `flights` runtime dependency: Task 1.
  - `google_fli` provider package: Tasks 2-4.
  - fixture excluded from normal search: Tasks 2 and 5.
  - global `max_results` and final ranking: Task 5.
  - no silent currency invention: Tasks 3-4.
  - structured provider errors with existing Contract V1 enums: Task 4.
  - `openWorldHint=True`: Task 7.
  - provider CLI live-safe behavior: Task 6.
  - subprocess MCP tests avoiding live network: Task 7.
  - packaging and opt-in live smoke: Task 9.
  - agent provider attribution instructions: Task 8.
- Red flag scan: no incomplete implementation markers are intentionally left in this plan.
- Type consistency:
  - Provider statuses use `ProviderStatusCode`.
  - Provider error classifications use `ErrorV1.code` and `details.failure_type`.
  - Normal search imports `load_search_providers()`.
  - MCP in-process tests call `Tool.run(..., convert_result=True)`.
