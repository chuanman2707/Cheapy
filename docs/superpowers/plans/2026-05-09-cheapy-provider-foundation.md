# Cheapy Provider Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Gate 3: a provider foundation with packaged manifest discovery, a deterministic `manual_fixture` provider, and provider CLI checks.

**Architecture:** Cheapy stays offline and MCP remains blocked. New provider-local models live in `cheapy.providers.base`; manifest discovery lives in `cheapy.providers.registry`; `manual_fixture` returns fixed `FlightOfferV1` objects for one exact one-way fixture route. The CLI only lists and tests packaged providers, and all output remains structured JSON.

**Tech Stack:** Python 3.12+, Pydantic v2, `importlib.resources`, stdlib `tomllib`, Typer, Hatchling, uv, pytest.

---

## Current Baseline

Run this before starting:

```bash
uv run pytest -v
git status --short
```

Expected test baseline: `48 passed`.

The working tree may contain unrelated untracked project docs. Do not stage or edit them unless this plan explicitly names them.

Gate 3 must not add live network calls, `google_fli`, storage, search orchestration, or a real MCP server.

## File Structure

Create:

- `cheapy/providers/__init__.py`: package exports for provider foundation helpers.
- `cheapy/providers/base.py`: provider request/result models and provider protocol.
- `cheapy/providers/registry.py`: bundled manifest discovery and provider loading.
- `cheapy/providers/manual_fixture/__init__.py`: marker package for the fixture provider.
- `cheapy/providers/manual_fixture/manifest.toml`: packaged provider manifest.
- `cheapy/providers/manual_fixture/provider.py`: deterministic sample-offer provider.
- `tests/test_providers.py`: provider model, registry, and fixture-provider tests.

Modify:

- `cheapy/cli.py`: add `cheapy providers list` and `cheapy providers test`.
- `tests/test_cli.py`: add provider CLI success and failure tests.
- `tests/test_package_data.py`: verify the provider manifest is present in a built wheel and the installed wheel can run provider CLI commands.

---

### Task 1: Provider Base Models

**Files:**

- Create: `cheapy/providers/__init__.py`
- Create: `cheapy/providers/base.py`
- Create: `tests/test_providers.py`

- [ ] **Step 1: Write failing provider base tests**

Create `tests/test_providers.py`:

```python
from __future__ import annotations

import asyncio

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    PassengersV1,
    ProviderStatusCode,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult


def test_provider_exact_one_way_request_defaults_to_one_adult() -> None:
    request = ProviderExactOneWayRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
    )

    assert request.origin == "CXR"
    assert request.destination == "SGN"
    assert request.departure_date == "2026-07-10"
    assert request.passengers == PassengersV1()


def test_provider_result_reuses_contract_error_models() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="No manual fixture exists for the requested route/date.",
        details={
            "provider": "manual_fixture",
            "capability": "exact_one_way",
            "origin": "HAN",
            "destination": "SGN",
            "departure_date": "2026-07-10",
        },
        retryable=False,
    )

    result = ProviderResult(
        provider_name="manual_fixture",
        capability="exact_one_way",
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[error],
        duration_ms=0,
        retryable=False,
    )

    assert result.provider_name == "manual_fixture"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors == [error]
```

Run:

```bash
uv run pytest tests/test_providers.py::test_provider_exact_one_way_request_defaults_to_one_adult -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.providers'`.

- [ ] **Step 2: Add provider package exports**

Create `cheapy/providers/__init__.py`:

```python
"""Provider foundation for Cheapy."""

from __future__ import annotations

from cheapy.providers.base import (
    FlightProvider,
    ProviderExactOneWayRequest,
    ProviderResult,
)

__all__ = [
    "FlightProvider",
    "ProviderExactOneWayRequest",
    "ProviderResult",
]
```

- [ ] **Step 3: Add provider base models**

Create `cheapy/providers/base.py`:

```python
"""Provider-local request and result models."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cheapy.models import (
    ErrorV1,
    FlightOfferV1,
    PassengersV1,
    ProviderStatusCode,
    WarningV1,
)


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProviderExactOneWayRequest(_ProviderModel):
    """Provider-local request for an exact one-way flight fixture."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: str
    passengers: PassengersV1 = Field(default_factory=PassengersV1)

    @field_validator("departure_date")
    @classmethod
    def validate_departure_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Date must use YYYY-MM-DD format") from exc
        return value


class ProviderResult(_ProviderModel):
    """Provider-level result before orchestrator conversion."""

    provider_name: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    status: ProviderStatusCode
    offers: list[FlightOfferV1] = Field(default_factory=list)
    warnings: list[WarningV1] = Field(default_factory=list)
    errors: list[ErrorV1] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    retryable: bool = False


class FlightProvider(Protocol):
    """Async interface implemented by packaged flight providers."""

    name: str
    capabilities: tuple[str, ...]

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        """Return exact one-way provider results."""
```

- [ ] **Step 4: Run provider base tests**

Run:

```bash
uv run pytest tests/test_providers.py::test_provider_exact_one_way_request_defaults_to_one_adult tests/test_providers.py::test_provider_result_reuses_contract_error_models -v
```

Expected: PASS.

- [ ] **Step 5: Commit provider base models**

Run:

```bash
git add cheapy/providers/__init__.py cheapy/providers/base.py tests/test_providers.py
git commit -m "feat: add provider base models" -m "AI-Model: GPT-5 Codex"
```

---

### Task 2: Provider Manifest Registry

**Files:**

- Modify: `tests/test_providers.py`
- Create: `cheapy/providers/registry.py`
- Create: `cheapy/providers/manual_fixture/__init__.py`
- Create: `cheapy/providers/manual_fixture/manifest.toml`

- [ ] **Step 1: Add failing manifest registry tests**

Append to `tests/test_providers.py`:

```python
from cheapy.providers.registry import (
    ProviderManifest,
    discover_provider_manifests,
)


def test_manual_fixture_manifest_is_discovered_from_package_resources() -> None:
    manifests = discover_provider_manifests()

    assert [manifest.name for manifest in manifests] == ["manual_fixture"]
    manifest = manifests[0]
    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="manual_fixture",
        display_name="Manual fixture provider",
        default_enabled=True,
        module="cheapy.providers.manual_fixture.provider",
        capabilities=["exact_one_way"],
    )


def test_registry_exposes_exact_one_way_as_stable_capability() -> None:
    manifest = discover_provider_manifests()[0]

    assert manifest.capabilities == ["exact_one_way"]
```

Run:

```bash
uv run pytest tests/test_providers.py::test_manual_fixture_manifest_is_discovered_from_package_resources -v
```

Expected: FAIL because `cheapy.providers.registry` does not exist.

- [ ] **Step 2: Add manual fixture package marker and manifest**

Create `cheapy/providers/manual_fixture/__init__.py`:

```python
"""Deterministic local fixture provider."""
```

Create `cheapy/providers/manual_fixture/manifest.toml`:

```toml
manifest_schema_version = "1"
name = "manual_fixture"
display_name = "Manual fixture provider"
default_enabled = true
module = "cheapy.providers.manual_fixture.provider"
capabilities = ["exact_one_way"]
```

- [ ] **Step 3: Add registry implementation**

Create `cheapy/providers/registry.py`:

```python
"""Packaged provider manifest discovery."""

from __future__ import annotations

from importlib import import_module
from importlib.resources import files
from typing import Any, Literal
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cheapy.providers.base import FlightProvider


class ProviderRegistryError(RuntimeError):
    """Base provider registry error."""


class ProviderManifestError(ProviderRegistryError):
    """Raised when a packaged provider manifest is invalid."""


class ProviderManifest(BaseModel):
    """Validated provider manifest loaded from package resources."""

    model_config = ConfigDict(extra="forbid", strict=True)

    manifest_schema_version: Literal["1"]
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    default_enabled: bool
    module: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)


def _provider_resource_root():
    return files("cheapy.providers")


def discover_provider_manifests() -> list[ProviderManifest]:
    """Discover bundled provider manifests from package resources."""
    manifests: list[ProviderManifest] = []
    root = _provider_resource_root()

    for child in sorted(root.iterdir(), key=lambda resource: resource.name):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        manifest_resource = child.joinpath("manifest.toml")
        if not manifest_resource.is_file():
            continue
        data = tomllib.loads(manifest_resource.read_text(encoding="utf-8"))
        try:
            manifest = ProviderManifest.model_validate(data)
        except ValidationError as exc:
            raise ProviderManifestError(
                f"Invalid provider manifest for {child.name!r}"
            ) from exc
        manifests.append(manifest)

    return manifests


def load_provider(manifest: ProviderManifest) -> FlightProvider:
    """Load a provider object from a validated bundled manifest."""
    module = import_module(manifest.module)
    factory: Any = getattr(module, "create_provider")
    provider = factory()
    return provider


def load_enabled_providers() -> list[FlightProvider]:
    """Load all bundled providers enabled by default."""
    return [
        load_provider(manifest)
        for manifest in discover_provider_manifests()
        if manifest.default_enabled
    ]
```

- [ ] **Step 4: Run manifest registry tests**

Run:

```bash
uv run pytest tests/test_providers.py::test_manual_fixture_manifest_is_discovered_from_package_resources tests/test_providers.py::test_registry_exposes_exact_one_way_as_stable_capability -v
```

Expected: PASS.

- [ ] **Step 5: Commit registry and manifest**

Run:

```bash
git add cheapy/providers/registry.py cheapy/providers/manual_fixture/__init__.py cheapy/providers/manual_fixture/manifest.toml tests/test_providers.py
git commit -m "feat: discover packaged provider manifests" -m "AI-Model: GPT-5 Codex"
```

---

### Task 3: Manual Fixture Provider

**Files:**

- Modify: `tests/test_providers.py`
- Create: `cheapy/providers/manual_fixture/provider.py`

- [ ] **Step 1: Add failing fixture provider tests**

Append to `tests/test_providers.py`:

```python
from cheapy.providers.manual_fixture.provider import create_provider


def test_manual_fixture_returns_two_valid_offers_for_fixture_route() -> None:
    provider = create_provider()
    request = ProviderExactOneWayRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
    )

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.provider_name == "manual_fixture"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert len(result.offers) == 2
    assert [offer.provider for offer in result.offers] == [
        "manual_fixture",
        "manual_fixture",
    ]
    assert [offer.global_rank for offer in result.offers] == [1, 2]
    assert all(offer.fare_details_status == "not_collected" for offer in result.offers)
    assert all(offer.flags.baggage_unknown is True for offer in result.offers)


def test_manual_fixture_returns_controlled_failure_for_unsupported_input() -> None:
    provider = create_provider()
    request = ProviderExactOneWayRequest(
        origin="HAN",
        destination="SGN",
        departure_date="2026-07-10",
    )

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.code == ErrorCode.PROVIDER_FAILED
    assert error.severity == Severity.ERROR
    assert error.message_en == "No manual fixture exists for the requested route/date."
    assert error.details == {
        "provider": "manual_fixture",
        "capability": "exact_one_way",
        "origin": "HAN",
        "destination": "SGN",
        "departure_date": "2026-07-10",
    }
    assert error.retryable is False
```

Run:

```bash
uv run pytest tests/test_providers.py::test_manual_fixture_returns_two_valid_offers_for_fixture_route -v
```

Expected: FAIL because `cheapy.providers.manual_fixture.provider` does not exist.

- [ ] **Step 2: Add manual fixture provider**

Create `cheapy/providers/manual_fixture/provider.py`:

```python
"""Deterministic local fixture provider."""

from __future__ import annotations

from time import perf_counter

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    ProviderStatusCode,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult


class ManualFixtureProvider:
    """Offline provider that returns fixed sample offers."""

    name = "manual_fixture"
    capabilities = ("exact_one_way",)

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        started = perf_counter()
        if not _is_supported_request(request):
            return _unsupported_result(request, started)

        return ProviderResult(
            provider_name=self.name,
            capability="exact_one_way",
            status=ProviderStatusCode.SUCCESS,
            offers=_fixture_offers(),
            warnings=[],
            errors=[],
            duration_ms=_elapsed_ms(started),
            retryable=False,
        )


def create_provider() -> ManualFixtureProvider:
    """Create the packaged manual fixture provider."""
    return ManualFixtureProvider()


def _is_supported_request(request: ProviderExactOneWayRequest) -> bool:
    return (
        request.origin == "CXR"
        and request.destination == "SGN"
        and request.departure_date == "2026-07-10"
        and request.passengers.adults == 1
        and request.passengers.children == 0
        and request.passengers.infants_on_lap == 0
        and request.passengers.infants_in_seat == 0
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _unsupported_result(
    request: ProviderExactOneWayRequest,
    started: float,
) -> ProviderResult:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="No manual fixture exists for the requested route/date.",
        details={
            "provider": "manual_fixture",
            "capability": "exact_one_way",
            "origin": request.origin,
            "destination": request.destination,
            "departure_date": request.departure_date,
        },
        retryable=False,
    )
    return ProviderResult(
        provider_name="manual_fixture",
        capability="exact_one_way",
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[error],
        duration_ms=_elapsed_ms(started),
        retryable=False,
    )


def _fixture_offers() -> list[FlightOfferV1]:
    return [
        FlightOfferV1(
            offer_id="manual_fixture:cxr-sgn-20260710-1",
            price_amount=1280000.0,
            currency="VND",
            comparable=True,
            rank_within_currency=1,
            global_rank=1,
            provider="manual_fixture",
            requested_origin="CXR",
            requested_destination="SGN",
            actual_origin="CXR",
            actual_destination="SGN",
            nearby_origin_distance_km=None,
            nearby_destination_distance_km=None,
            requested_departure_date="2026-07-10",
            actual_departure_date="2026-07-10",
            departure_offset_days=0,
            requested_return_date=None,
            actual_return_date=None,
            return_offset_days=None,
            legs=[
                FlightLegV1(
                    origin="CXR",
                    destination="SGN",
                    departure_time="2026-07-10T08:15:00",
                    arrival_time="2026-07-10T09:25:00",
                    airline_code="VJ",
                    flight_number="VJ601",
                    duration_minutes=70,
                )
            ],
            total_duration_minutes=70,
            stops=0,
            flags=OfferFlagsV1(),
            fare_details_status="not_collected",
        ),
        FlightOfferV1(
            offer_id="manual_fixture:cxr-sgn-20260710-2",
            price_amount=1490000.0,
            currency="VND",
            comparable=True,
            rank_within_currency=2,
            global_rank=2,
            provider="manual_fixture",
            requested_origin="CXR",
            requested_destination="SGN",
            actual_origin="CXR",
            actual_destination="SGN",
            nearby_origin_distance_km=None,
            nearby_destination_distance_km=None,
            requested_departure_date="2026-07-10",
            actual_departure_date="2026-07-10",
            departure_offset_days=0,
            requested_return_date=None,
            actual_return_date=None,
            return_offset_days=None,
            legs=[
                FlightLegV1(
                    origin="CXR",
                    destination="SGN",
                    departure_time="2026-07-10T14:40:00",
                    arrival_time="2026-07-10T15:50:00",
                    airline_code="VN",
                    flight_number="VN1341",
                    duration_minutes=70,
                )
            ],
            total_duration_minutes=70,
            stops=0,
            flags=OfferFlagsV1(),
            fare_details_status="not_collected",
        ),
    ]
```

- [ ] **Step 3: Run fixture provider tests**

Run:

```bash
uv run pytest tests/test_providers.py::test_manual_fixture_returns_two_valid_offers_for_fixture_route tests/test_providers.py::test_manual_fixture_returns_controlled_failure_for_unsupported_input -v
```

Expected: PASS.

- [ ] **Step 4: Run full provider tests**

Run:

```bash
uv run pytest tests/test_providers.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit manual fixture provider**

Run:

```bash
git add cheapy/providers/manual_fixture/provider.py tests/test_providers.py
git commit -m "feat: add manual fixture provider" -m "AI-Model: GPT-5 Codex"
```

---

### Task 4: Provider CLI Commands

**Files:**

- Modify: `tests/test_cli.py`
- Modify: `cheapy/cli.py`

- [ ] **Step 1: Add failing provider CLI tests**

Append to `tests/test_cli.py`:

```python
from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers.registry import ProviderManifestError


def test_providers_list_prints_json() -> None:
    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "providers": [
            {
                "capabilities": ["exact_one_way"],
                "default_enabled": True,
                "display_name": "Manual fixture provider",
                "enabled": True,
                "name": "manual_fixture",
            }
        ],
        "status": "ok",
    }


def test_providers_test_prints_json() -> None:
    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "providers": [
            {
                "capability": "exact_one_way",
                "error_count": 0,
                "name": "manual_fixture",
                "offer_count": 2,
                "status": "success",
            }
        ],
        "providers_tested": 1,
        "status": "ok",
    }


def test_providers_list_reports_no_provider_on_stderr(monkeypatch) -> None:
    monkeypatch.setattr("cheapy.cli.discover_provider_manifests", lambda: [])

    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "NO_PROVIDER_AVAILABLE",
        "message": "No packaged Cheapy providers were found.",
        "suggestion": "Reinstall Cheapy and verify package data is present.",
    }


def test_providers_list_reports_invalid_manifest_on_stderr(monkeypatch) -> None:
    def raise_manifest_error():
        raise ProviderManifestError("Invalid provider manifest for 'broken'")

    monkeypatch.setattr("cheapy.cli.discover_provider_manifests", raise_manifest_error)

    result = runner.invoke(app, ["providers", "list"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_MANIFEST_INVALID",
        "message": "Invalid provider manifest for 'broken'",
        "suggestion": "Reinstall Cheapy and verify provider package data is valid.",
    }


def test_providers_test_reports_provider_failure(monkeypatch) -> None:
    class FailingProvider:
        name = "manual_fixture"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            return ProviderResult(
                provider_name="manual_fixture",
                capability="exact_one_way",
                status=ProviderStatusCode.FAILED,
                offers=[],
                warnings=[],
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_FAILED,
                        severity=Severity.ERROR,
                        message_en="No manual fixture exists for the requested route/date.",
                        details={
                            "provider": "manual_fixture",
                            "capability": "exact_one_way",
                            "origin": "CXR",
                            "destination": "SGN",
                            "departure_date": "2026-07-10",
                        },
                        retryable=False,
                    )
                ],
                duration_ms=0,
                retryable=False,
            )

    monkeypatch.setattr("cheapy.cli.load_enabled_providers", lambda: [FailingProvider()])

    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_TEST_FAILED",
        "message": "One or more provider checks failed.",
        "suggestion": "Run 'cheapy providers test --human' for a concise provider report.",
    }


def test_providers_test_reports_unexpected_exception(monkeypatch) -> None:
    class RaisingProvider:
        name = "manual_fixture"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            raise RuntimeError("boom")

    monkeypatch.setattr("cheapy.cli.load_enabled_providers", lambda: [RaisingProvider()])

    result = runner.invoke(app, ["providers", "test"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "PROVIDER_TEST_ERROR",
        "message": "A provider check raised an unexpected exception.",
        "suggestion": "Run 'cheapy providers test --human' for a concise provider report.",
    }
```

Run:

```bash
uv run pytest tests/test_cli.py::test_providers_list_prints_json -v
```

Expected: FAIL because the `providers` command group does not exist.

- [ ] **Step 2: Add provider CLI imports and Typer group**

Modify the imports near the top of `cheapy/cli.py`:

```python
import asyncio
import json
import shutil
import sys
from typing import Any
```

Add provider imports below model imports:

```python
from cheapy.models import ProviderStatusCode, SearchRequestV1, SearchResponseV1
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.registry import (
    ProviderManifestError,
    discover_provider_manifests,
    load_enabled_providers,
)
```

Add this after `app = typer.Typer(...)`:

```python
providers_app = typer.Typer(
    help="Inspect packaged Cheapy providers.",
    no_args_is_help=True,
)
app.add_typer(providers_app, name="providers")
```

- [ ] **Step 3: Add provider CLI helpers and commands**

Append this above the existing `mcp()` command in `cheapy/cli.py`:

```python
def _provider_fixture_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
    )


@providers_app.command("list")
def providers_list() -> None:
    """List packaged Cheapy providers."""
    try:
        manifests = discover_provider_manifests()
    except ProviderManifestError as exc:
        _json_echo(
            _error_payload(
                "PROVIDER_MANIFEST_INVALID",
                str(exc),
                "Reinstall Cheapy and verify provider package data is valid.",
            ),
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if not manifests:
        _json_echo(
            _error_payload(
                "NO_PROVIDER_AVAILABLE",
                "No packaged Cheapy providers were found.",
                "Reinstall Cheapy and verify package data is present.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    _json_echo(
        {
            "status": "ok",
            "providers": [
                {
                    "name": manifest.name,
                    "display_name": manifest.display_name,
                    "default_enabled": manifest.default_enabled,
                    "enabled": manifest.default_enabled,
                    "capabilities": manifest.capabilities,
                }
                for manifest in manifests
            ],
        }
    )


@providers_app.command("test")
def providers_test() -> None:
    """Run offline packaged provider checks."""
    try:
        providers = load_enabled_providers()
    except ProviderManifestError as exc:
        _json_echo(
            _error_payload(
                "PROVIDER_MANIFEST_INVALID",
                str(exc),
                "Reinstall Cheapy and verify provider package data is valid.",
            ),
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if not providers:
        _json_echo(
            _error_payload(
                "NO_PROVIDER_AVAILABLE",
                "No enabled packaged Cheapy providers were found.",
                "Reinstall Cheapy and verify package data is present.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        reports = asyncio.run(_run_provider_checks(providers))
    except Exception as exc:
        _json_echo(
            _error_payload(
                "PROVIDER_TEST_ERROR",
                "A provider check raised an unexpected exception.",
                "Run 'cheapy providers test --human' for a concise provider report.",
            ),
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if any(report["status"] != "success" for report in reports):
        _json_echo(
            _error_payload(
                "PROVIDER_TEST_FAILED",
                "One or more provider checks failed.",
                "Run 'cheapy providers test --human' for a concise provider report.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    _json_echo(
        {
            "status": "ok",
            "providers_tested": len(reports),
            "providers": reports,
        }
    )


async def _run_provider_checks(providers: list[Any]) -> list[dict[str, Any]]:
    request = _provider_fixture_request()
    reports: list[dict[str, Any]] = []
    for provider in providers:
        result = await provider.search_exact_one_way(request)
        reports.append(
            {
                "name": result.provider_name,
                "capability": result.capability,
                "status": result.status.value,
                "offer_count": len(result.offers),
                "error_count": len(result.errors),
            }
        )
    return reports
```

- [ ] **Step 4: Run provider CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_providers_list_prints_json tests/test_cli.py::test_providers_test_prints_json tests/test_cli.py::test_providers_list_reports_no_provider_on_stderr tests/test_cli.py::test_providers_list_reports_invalid_manifest_on_stderr tests/test_cli.py::test_providers_test_reports_provider_failure tests/test_cli.py::test_providers_test_reports_unexpected_exception -v
```

Expected: PASS.

- [ ] **Step 5: Confirm MCP remains blocked**

Run:

```bash
uv run pytest tests/test_cli.py::test_mcp_remains_outside_contract_foundation_gate -v
```

Expected: PASS.

- [ ] **Step 6: Commit provider CLI**

Run:

```bash
git add cheapy/cli.py tests/test_cli.py
git commit -m "feat: add provider inspection cli" -m "AI-Model: GPT-5 Codex"
```

---

### Task 5: Wheel Packaging Checks

**Files:**

- Modify: `tests/test_package_data.py`

- [ ] **Step 1: Add failing installed-wheel provider checks**

Replace `tests/test_package_data.py` with:

```python
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_can_load_packaged_airport_and_provider_data(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--no-build-isolation",
            "--offline",
            "--no-index",
            "--out-dir",
            str(dist_dir),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "cheapy/data/airports.v1.json" in names
    assert "cheapy/data/hubs.v1.json" in names
    assert "cheapy/data/README.md" in names
    assert "cheapy/providers/manual_fixture/manifest.toml" in names

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--offline", str(wheel)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    resource_script = """
from importlib.resources import files
import json

base = files("cheapy").joinpath("data")
airports = json.loads(base.joinpath("airports.v1.json").read_text(encoding="utf-8"))
hubs = json.loads(base.joinpath("hubs.v1.json").read_text(encoding="utf-8"))
readme = base.joinpath("README.md").read_text(encoding="utf-8")
manifest = files("cheapy.providers").joinpath("manual_fixture", "manifest.toml").read_text(encoding="utf-8")

assert airports["version"] == 1
assert hubs["version"] == 1
assert "OurAirports" in readme
assert 'name = "manual_fixture"' in manifest
"""
    subprocess.run([str(python), "-c", resource_script], check=True, cwd=tmp_path)

    list_result = subprocess.run(
        [str(python), "-m", "cheapy", "providers", "list"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert list_result.stderr == ""
    assert json.loads(list_result.stdout)["providers"][0]["name"] == "manual_fixture"

    test_result = subprocess.run(
        [str(python), "-m", "cheapy", "providers", "test"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert test_result.stderr == ""
    assert json.loads(test_result.stdout)["providers_tested"] == 1
```

Run:

```bash
uv run pytest tests/test_package_data.py -v
```

Expected before Tasks 1-4 are complete: FAIL. Expected after Tasks 1-4: PASS.

- [ ] **Step 2: Run package data test after provider files exist**

Run:

```bash
uv run pytest tests/test_package_data.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit package checks**

Run:

```bash
git add tests/test_package_data.py
git commit -m "test: verify provider manifest in wheel" -m "AI-Model: GPT-5 Codex"
```

---

### Task 6: Final Verification

**Files:**

- No planned file edits.

- [ ] **Step 1: Run focused test lanes**

Run:

```bash
uv run pytest tests/test_providers.py tests/test_cli.py tests/test_package_data.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 3: Run manual CLI checks**

Run:

```bash
uv run cheapy providers list
uv run cheapy providers test
uv run cheapy mcp
```

Expected:

- `providers list` exits `0` and prints JSON with `manual_fixture`.
- `providers test` exits `0` and prints JSON with one successful provider check.
- `cheapy mcp` exits `2`, prints nothing to stdout, and keeps the existing `MCP_OUTSIDE_CONTRACT_GATE` JSON error on stderr.

- [ ] **Step 4: Check git status**

Run:

```bash
git status --short
```

Expected: only unrelated pre-existing untracked files remain.

## Plan Self-Review

Spec coverage:

- Provider package, base contract, registry, manifest, fixture provider, CLI commands, provider tests, CLI tests, and wheel checks each have a task.
- Scope exclusions are preserved: no MCP server, no orchestrator, no live provider, no `google_fli`, no storage.
- `exact_one_way`, provider result shape, unsupported input error, CLI JSON shapes, exit behavior, and wheel acceptance are covered.

Type consistency:

- `ProviderExactOneWayRequest` and `ProviderResult` are defined before use.
- `ProviderResult.status` uses `ProviderStatusCode`.
- Provider errors reuse `ErrorV1`, `ErrorCode`, and `Severity`.
- CLI checks use the same fixture request as `manual_fixture`.

Execution handoff:

Plan complete and saved to `docs/superpowers/plans/2026-05-09-cheapy-provider-foundation.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.
