# Cheapy Contract V1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the phase-1 foundation: Python package skeleton, strict Contract V1 models, provider-call budget semantics, and baseline CLI doctor behavior.

**Architecture:** This plan implements the stable contract layer before provider/orchestrator work. The package exposes typed Pydantic models that later MCP, planner, provider, and CLI layers must use as their source of truth. Runtime behavior is intentionally small: validation, schema export, and a protocol-safe CLI/MCP entry foundation.

**Tech Stack:** Python 3.12+, uv, Pydantic v2, Typer, pytest.

---

## Scope Check

The master spec covers multiple subsystems: MCP install, agent skill hooks, airport data, search planners, provider registry, `google_fli`, logging, and tests. Implementing all of that in one plan would create a large high-risk batch.

This plan covers the first gate only:

- project/package skeleton
- `SearchRequestV1`
- `SearchResponseV1`
- warning/error/provider status codes
- search-plan and provider-call budget fields
- CLI baseline with `cheapy doctor`
- contract and CLI tests

Separate follow-up plans should cover:

- airport snapshot and resolver
- provider registry and packaged resources
- planner/orchestrator execution
- `google_fli` provider
- MCP install hooks for Codex/Claude

## File Structure

Create these files:

- `pyproject.toml`: package metadata, dependencies, CLI entrypoint, pytest config.
- `cheapy/__init__.py`: package version export.
- `cheapy/__main__.py`: `python -m cheapy` entrypoint.
- `cheapy/cli.py`: Typer CLI with `doctor` command and MCP command that exits protocol-cleanly outside this gate.
- `cheapy/models/__init__.py`: public model exports.
- `cheapy/models/contracts.py`: strict Contract V1 Pydantic models and enums.
- `tests/test_contracts.py`: request/response validation and schema tests.
- `tests/test_cli.py`: CLI doctor baseline tests.

Do not create provider, airport, planner, or MCP install hook code in this plan.

## Task 0: Repository Baseline

**Files:**
- Create: `pyproject.toml`
- Create: `cheapy/__init__.py`
- Create: `cheapy/__main__.py`
- Create: `cheapy/cli.py`
- Create: `cheapy/models/__init__.py`

- [ ] **Step 1: Initialize git if needed**

Run:

```bash
git rev-parse --is-inside-work-tree || git init
```

Expected:

```text
true
```

or:

```text
Initialized empty Git repository
```

- [ ] **Step 2: Create package metadata**

Create `pyproject.toml`:

```toml
[project]
name = "cheapy-flights"
version = "0.1.0"
description = "Agent-first MCP server and Python package for cheap flight search."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.10",
    "typer>=0.15",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
]

[project.scripts]
cheapy = "cheapy.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["cheapy"]

[tool.pytest.ini_options]
markers = [
    "contract: Contract V1 schema and validation tests",
    "integration: Local integration tests without live provider calls",
    "packaging: Wheel and package-data behavior tests",
    "protocol: MCP stdio protocol-cleanliness tests",
    "security: Redaction and secret-handling tests",
    "live: Opt-in live provider tests",
]
addopts = "--strict-markers"
testpaths = ["tests"]
```

- [ ] **Step 3: Create package entry files**

Create `cheapy/__init__.py`:

```python
"""Cheapy package."""

__version__ = "0.1.0"
```

Create `cheapy/__main__.py`:

```python
"""Module entrypoint for `python -m cheapy`."""

from cheapy.cli import app


if __name__ == "__main__":
    app()
```

Create `cheapy/models/__init__.py`:

```python
"""Public model exports for Cheapy."""

from cheapy.models.contracts import (
    AirportCandidateV1,
    CandidateFamily,
    CurrencyGroupV1,
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    PassengersV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
    Severity,
    WarningCode,
    WarningV1,
)

__all__ = [
    "AirportCandidateV1",
    "CandidateFamily",
    "CurrencyGroupV1",
    "ErrorCode",
    "ErrorV1",
    "FlightLegV1",
    "FlightOfferV1",
    "OfferFlagsV1",
    "PassengersV1",
    "ProviderStatusCode",
    "ProviderStatusV1",
    "SearchMode",
    "SearchPlanV1",
    "SearchRequestV1",
    "SearchResponseV1",
    "SearchStatus",
    "Severity",
    "WarningCode",
    "WarningV1",
]
```

Create `cheapy/cli.py`:

```python
"""Cheapy command line interface."""

from __future__ import annotations

import shutil
import sys

import typer

from cheapy import __version__

app = typer.Typer(help="Cheapy flight-search MCP utilities.", no_args_is_help=True)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print Cheapy version and exit.",
    ),
) -> None:
    """Run Cheapy CLI."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def doctor() -> None:
    """Check local Cheapy installation health."""
    executable = shutil.which("cheapy")
    if executable is None:
        typer.echo("ERROR: cheapy executable was not found on PATH.", err=True)
        raise typer.Exit(code=1)

    typer.echo("Cheapy doctor")
    typer.echo(f"version: {__version__}")
    typer.echo(f"executable: {executable}")
    typer.echo("status: ok")


@app.command()
def mcp() -> None:
    """Run the stdio MCP server."""
    print("ERROR: MCP server is outside this contract foundation gate.", file=sys.stderr)
    raise typer.Exit(code=2)
```

- [ ] **Step 4: Install dependencies**

Run:

```bash
uv sync --extra dev
```

Expected:

```text
Resolved
Prepared
Installed
```

- [ ] **Step 5: Run CLI smoke check**

Run:

```bash
uv run cheapy --version
```

Expected:

```text
0.1.0
```

- [ ] **Step 6: Commit baseline**

Run:

```bash
git add pyproject.toml cheapy
git commit -m "chore: scaffold cheapy package"
```

Expected:

```text
[main
```

## Task 1: Contract V1 Models

**Files:**
- Create: `cheapy/models/contracts.py`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Write failing contract tests**

Create `tests/test_contracts.py`:

```python
"""Contract V1 tests."""

import pytest
from pydantic import ValidationError

from cheapy.models import (
    CandidateFamily,
    ErrorCode,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    PassengersV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
    Severity,
    WarningCode,
    WarningV1,
)


def test_search_request_defaults_to_exact_mode_and_one_adult() -> None:
    request = SearchRequestV1(
        schema_version="1",
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
        return_date=None,
    )

    assert request.search_mode == SearchMode.EXACT
    assert request.passengers == PassengersV1()
    assert request.max_results == 5


def test_search_request_rejects_non_iso_dates() -> None:
    with pytest.raises(ValidationError):
        SearchRequestV1(
            schema_version="1",
            origin="CXR",
            destination="SGN",
            departure_date="10/07/2026",
            return_date=None,
        )


def test_search_request_rejects_string_passenger_counts() -> None:
    with pytest.raises(ValidationError):
        SearchRequestV1(
            schema_version="1",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            return_date=None,
            passengers={"adults": "1"},
        )


def test_response_uses_offers_as_canonical_source() -> None:
    offer = FlightOfferV1(
        offer_id="google_fli:offer-1",
        price_amount=120.5,
        currency="USD",
        comparable=True,
        rank_within_currency=1,
        global_rank=1,
        provider="google_fli",
        requested_origin="SGN",
        requested_destination="BKK",
        actual_origin="SGN",
        actual_destination="BKK",
        requested_departure_date="2026-07-10",
        actual_departure_date="2026-07-10",
        departure_offset_days=0,
        requested_return_date=None,
        actual_return_date=None,
        return_offset_days=None,
        legs=[
            FlightLegV1(
                origin="SGN",
                destination="BKK",
                departure_time="2026-07-10T09:00:00",
                arrival_time="2026-07-10T10:30:00",
                airline_code="VN",
                flight_number="VN601",
                duration_minutes=90,
            )
        ],
        total_duration_minutes=90,
        stops=0,
        flags=OfferFlagsV1(),
        fare_details_status="not_collected",
    )
    plan = SearchPlanV1(
        search_mode=SearchMode.EXACT,
        planned_candidate_count=1,
        executed_candidate_count=1,
        planned_provider_call_count=1,
        executed_provider_call_count=1,
        candidate_count_by_family={CandidateFamily.EXACT: 1},
        provider_call_count_by_family={CandidateFamily.EXACT: 1},
        truncated=False,
        truncated_families=[],
        candidate_families=[CandidateFamily.EXACT],
    )
    status = ProviderStatusV1(
        provider_name="google_fli",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        planned_call_count=1,
        executed_call_count=1,
        succeeded_call_count=1,
        failed_call_count=0,
        duration_ms=120,
        warnings=[],
        errors=[],
        retryable=False,
    )
    warning = WarningV1(
        code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
        severity=Severity.WARNING,
        message_en="Fare details were not collected.",
        details={"offer_id": "google_fli:offer-1"},
        retryable=False,
    )

    response = SearchResponseV1(
        schema_version="1",
        status=SearchStatus.SUCCESS,
        request_id="req_123",
        offers=[offer],
        warnings=[warning],
        errors=[],
        provider_statuses=[status],
        search_plan=plan,
        mixed_currency=False,
        currency_groups=[],
        currency_notes=[],
        candidates=None,
    )

    assert response.offers[0].offer_id == "google_fli:offer-1"
    assert response.search_plan.truncated is False
    assert response.candidates is None


def test_search_response_rejects_top_level_search_mode() -> None:
    with pytest.raises(ValidationError):
        SearchResponseV1(
            schema_version="1",
            status="success",
            request_id="req_123",
            offers=[],
            warnings=[],
            errors=[],
            provider_statuses=[],
            search_plan={
                "search_mode": "exact",
                "planned_candidate_count": 0,
                "executed_candidate_count": 0,
                "planned_provider_call_count": 0,
                "executed_provider_call_count": 0,
                "candidate_count_by_family": {},
                "provider_call_count_by_family": {},
                "truncated": False,
                "truncated_families": [],
                "candidate_families": [],
            },
            mixed_currency=False,
            currency_groups=[],
            currency_notes=[],
            candidates=None,
            search_mode="exact",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_contracts.py -v
```

Expected:

```text
ModuleNotFoundError
```

or:

```text
ImportError
```

- [ ] **Step 3: Implement Contract V1 models**

Create `cheapy/models/contracts.py`:

```python
"""Contract V1 models for Cheapy MCP tool inputs and outputs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_yyyy_mm_dd(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format") from exc
    return value


def _validate_iso_like_datetime(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date-time must be ISO-like, for example 2026-07-10T09:00:00") from exc
    return value


class SearchMode(StrEnum):
    """Supported search modes."""

    EXACT = "exact"
    EXPANDED = "expanded"


class SearchStatus(StrEnum):
    """Top-level search response status."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"


class Severity(StrEnum):
    """Warning and error severity."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WarningCode(StrEnum):
    """Stable phase-1 warning codes."""

    MIXED_CURRENCY = "mixed_currency"
    SEARCH_TRUNCATED = "search_truncated"
    CANDIDATE_FAMILY_TRUNCATED = "candidate_family_truncated"
    FARE_DETAILS_NOT_COLLECTED = "fare_details_not_collected"
    SPLIT_TICKET = "split_ticket"
    SELF_TRANSFER = "self_transfer"
    NEARBY_AIRPORT_USED = "nearby_airport_used"
    FLEXIBLE_DATE_USED = "flexible_date_used"


class ErrorCode(StrEnum):
    """Stable phase-1 error codes."""

    PROVIDER_FAILED = "provider_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_BLOCKED = "provider_blocked"
    NO_PROVIDER_AVAILABLE = "no_provider_available"
    AIRPORT_AMBIGUOUS = "airport_ambiguous"
    AIRPORT_NOT_FOUND = "airport_not_found"


class ProviderStatusCode(StrEnum):
    """Provider execution status."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class CandidateFamily(StrEnum):
    """Search candidate family names."""

    EXACT = "exact"
    FLEXIBLE_DATES = "flexible_dates"
    NEARBY_ORIGIN = "nearby_origin"
    NEARBY_DESTINATION = "nearby_destination"
    SPLIT_TICKET = "split_ticket"


class StrictModel(BaseModel):
    """Base model for strict Contract V1 validation."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PassengersV1(StrictModel):
    """Passenger counts for phase 1."""

    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    infants_on_lap: int = Field(default=0, ge=0)
    infants_in_seat: int = Field(default=0, ge=0)


class SearchRequestV1(StrictModel):
    """Input contract for `search_cheapest_flights`."""

    schema_version: Literal["1"]
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    departure_date: str
    return_date: str | None = None
    search_mode: SearchMode = SearchMode.EXACT
    passengers: PassengersV1 = Field(default_factory=PassengersV1)
    max_results: int = Field(default=5, ge=1, le=20)

    @field_validator("departure_date", "return_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_yyyy_mm_dd(value)


class WarningV1(StrictModel):
    """Machine-readable warning."""

    code: WarningCode
    severity: Severity
    message_en: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class ErrorV1(StrictModel):
    """Machine-readable error."""

    code: ErrorCode
    severity: Severity
    message_en: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class SearchPlanV1(StrictModel):
    """Executed search plan and budget accounting."""

    search_mode: SearchMode
    planned_candidate_count: int = Field(ge=0)
    executed_candidate_count: int = Field(ge=0)
    planned_provider_call_count: int = Field(ge=0)
    executed_provider_call_count: int = Field(ge=0)
    candidate_count_by_family: dict[CandidateFamily, int]
    provider_call_count_by_family: dict[CandidateFamily, int]
    truncated: bool
    truncated_families: list[CandidateFamily]
    candidate_families: list[CandidateFamily]


class ProviderStatusV1(StrictModel):
    """Provider execution status."""

    provider_name: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    status: ProviderStatusCode
    planned_call_count: int = Field(ge=0)
    executed_call_count: int = Field(ge=0)
    succeeded_call_count: int = Field(ge=0)
    failed_call_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    warnings: list[WarningV1] = Field(default_factory=list)
    errors: list[ErrorV1] = Field(default_factory=list)
    retryable: bool = False


class OfferFlagsV1(StrictModel):
    """Simple phase-1 offer flags."""

    is_split_ticket: bool = False
    is_self_transfer: bool = False
    uses_nearby_origin: bool = False
    uses_nearby_destination: bool = False
    uses_flexible_departure_date: bool = False
    uses_flexible_return_date: bool = False
    has_long_connection: bool = False
    has_overnight_connection: bool = False
    has_many_stops: bool = False
    baggage_unknown: bool = True


class FlightLegV1(StrictModel):
    """A single flight leg."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_time: str
    arrival_time: str
    airline_code: str = Field(min_length=1)
    flight_number: str = Field(min_length=1)
    duration_minutes: int = Field(ge=0)

    @field_validator("departure_time", "arrival_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _validate_iso_like_datetime(value)


class FlightOfferV1(StrictModel):
    """Canonical flight offer returned by Cheapy."""

    offer_id: str = Field(min_length=1)
    price_amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    comparable: bool
    rank_within_currency: int | None = Field(default=None, ge=1)
    global_rank: int | None = Field(default=None, ge=1)
    provider: str = Field(min_length=1)
    requested_origin: str = Field(min_length=1)
    requested_destination: str = Field(min_length=1)
    actual_origin: str = Field(min_length=3, max_length=3)
    actual_destination: str = Field(min_length=3, max_length=3)
    nearby_origin_distance_km: float | None = Field(default=None, ge=0)
    nearby_destination_distance_km: float | None = Field(default=None, ge=0)
    requested_departure_date: str
    actual_departure_date: str
    departure_offset_days: int
    requested_return_date: str | None = None
    actual_return_date: str | None = None
    return_offset_days: int | None = None
    legs: list[FlightLegV1]
    total_duration_minutes: int = Field(ge=0)
    stops: int = Field(ge=0)
    flags: OfferFlagsV1
    fare_details_status: Literal["not_collected"]

    @field_validator(
        "requested_departure_date",
        "actual_departure_date",
        "requested_return_date",
        "actual_return_date",
    )
    @classmethod
    def validate_offer_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_yyyy_mm_dd(value)


class CurrencyGroupV1(StrictModel):
    """Derived view over offers for a single currency."""

    currency: str = Field(min_length=3, max_length=3)
    offer_ids: list[str]


class AirportCandidateV1(StrictModel):
    """Airport clarification candidate."""

    iata: str = Field(min_length=3, max_length=3)
    name: str = Field(min_length=1)
    city: str | None = None
    country: str | None = None
    confidence: float = Field(ge=0, le=1)


class SearchResponseV1(StrictModel):
    """Output contract for `search_cheapest_flights`."""

    schema_version: Literal["1"]
    status: SearchStatus
    request_id: str = Field(min_length=1)
    offers: list[FlightOfferV1]
    warnings: list[WarningV1] = Field(default_factory=list)
    errors: list[ErrorV1] = Field(default_factory=list)
    provider_statuses: list[ProviderStatusV1] = Field(default_factory=list)
    search_plan: SearchPlanV1
    mixed_currency: bool
    currency_groups: list[CurrencyGroupV1] = Field(default_factory=list)
    currency_notes: list[str] = Field(default_factory=list)
    candidates: list[AirportCandidateV1] | None = None
```

- [ ] **Step 4: Run contract tests**

Run:

```bash
uv run pytest tests/test_contracts.py -v
```

Expected:

```text
5 passed
```

- [ ] **Step 5: Commit contract models**

Run:

```bash
git add cheapy/models tests/test_contracts.py
git commit -m "feat: add contract v1 models"
```

Expected:

```text
[main
```

## Task 2: CLI Doctor Baseline

**Files:**
- Modify: `cheapy/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_cli.py`:

```python
"""CLI tests."""

from typer.testing import CliRunner

from cheapy.cli import app


def test_version_option() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_doctor_reports_installation_health() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code in {0, 1}
    assert "Cheapy doctor" in result.output or "cheapy executable was not found" in result.output


def test_mcp_command_writes_error_to_stderr_only() -> None:
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "MCP server is outside this contract foundation gate" in result.stderr
```

- [ ] **Step 2: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 3: Tighten CLI implementation if tests fail**

If `test_mcp_command_writes_error_to_stderr_only` fails because Typer writes command text to stdout, replace `cheapy/cli.py` with:

```python
"""Cheapy command line interface."""

from __future__ import annotations

import shutil
import sys

import typer

from cheapy import __version__

app = typer.Typer(help="Cheapy flight-search MCP utilities.", no_args_is_help=True)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print Cheapy version and exit.",
    ),
) -> None:
    """Run Cheapy CLI."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def doctor() -> None:
    """Check local Cheapy installation health."""
    executable = shutil.which("cheapy")
    typer.echo("Cheapy doctor")
    typer.echo(f"version: {__version__}")
    if executable is None:
        typer.echo("status: error")
        typer.echo("error: cheapy executable was not found on PATH.")
        raise typer.Exit(code=1)
    typer.echo(f"executable: {executable}")
    typer.echo("status: ok")


@app.command(context_settings={"allow_extra_args": False})
def mcp() -> None:
    """Run the stdio MCP server."""
    print("ERROR: MCP server is outside this contract foundation gate.", file=sys.stderr)
    raise typer.Exit(code=2)
```

- [ ] **Step 4: Re-run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit CLI tests**

Run:

```bash
git add cheapy/cli.py tests/test_cli.py
git commit -m "test: cover cli doctor baseline"
```

Expected:

```text
[main
```

## Task 3: Schema Export And Contract Lane

**Files:**
- Modify: `cheapy/cli.py`
- Create: `tests/test_schema_export.py`

- [ ] **Step 1: Write schema export tests**

Create `tests/test_schema_export.py`:

```python
"""Schema export tests."""

import json

from typer.testing import CliRunner

from cheapy.cli import app


def test_schema_command_outputs_contract_models() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "SearchRequestV1" in payload
    assert "SearchResponseV1" in payload
    assert payload["SearchRequestV1"]["type"] == "object"
    assert payload["SearchResponseV1"]["type"] == "object"
```

- [ ] **Step 2: Run schema test to verify it fails**

Run:

```bash
uv run pytest tests/test_schema_export.py -v
```

Expected:

```text
No such command 'schema'
```

- [ ] **Step 3: Add schema command**

Replace `cheapy/cli.py` with:

```python
"""Cheapy command line interface."""

from __future__ import annotations

import json
import shutil
import sys

import typer

from cheapy import __version__
from cheapy.models import SearchRequestV1, SearchResponseV1

app = typer.Typer(help="Cheapy flight-search MCP utilities.", no_args_is_help=True)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print Cheapy version and exit.",
    ),
) -> None:
    """Run Cheapy CLI."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def doctor() -> None:
    """Check local Cheapy installation health."""
    executable = shutil.which("cheapy")
    typer.echo("Cheapy doctor")
    typer.echo(f"version: {__version__}")
    if executable is None:
        typer.echo("status: error")
        typer.echo("error: cheapy executable was not found on PATH.")
        raise typer.Exit(code=1)
    typer.echo(f"executable: {executable}")
    typer.echo("status: ok")


@app.command()
def schema() -> None:
    """Print Contract V1 JSON schemas."""
    payload = {
        "SearchRequestV1": SearchRequestV1.model_json_schema(),
        "SearchResponseV1": SearchResponseV1.model_json_schema(),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command(context_settings={"allow_extra_args": False})
def mcp() -> None:
    """Run the stdio MCP server."""
    print("ERROR: MCP server is outside this contract foundation gate.", file=sys.stderr)
    raise typer.Exit(code=2)
```

- [ ] **Step 4: Run schema and CLI tests**

Run:

```bash
uv run pytest tests/test_schema_export.py tests/test_cli.py -v
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit schema export**

Run:

```bash
git add cheapy/cli.py tests/test_schema_export.py
git commit -m "feat: export contract schemas"
```

Expected:

```text
[main
```

## Task 4: Full Foundation Verification

**Files:**
- Read: `docs/superpowers/specs/2026-05-08-cheapy-master-spec-design.md`
- Test: all tests created by this plan

- [ ] **Step 1: Run all default tests**

Run:

```bash
uv run pytest -v
```

Expected:

```text
passed
```

No tests marked `live` should run in this foundation gate.

- [ ] **Step 2: Verify package entrypoint**

Run:

```bash
uv run python -m cheapy --version
```

Expected:

```text
0.1.0
```

- [ ] **Step 3: Verify schema output is JSON**

Run:

```bash
uv run cheapy schema > /tmp/cheapy-schema.json
python -m json.tool /tmp/cheapy-schema.json >/tmp/cheapy-schema.pretty.json
```

Expected: command exits with status 0 and writes `/tmp/cheapy-schema.pretty.json`.

- [ ] **Step 4: Review spec coverage for this gate**

Confirm these master-spec items are covered:

```text
SearchRequestV1
SearchResponseV1
strict validation
warning/error codes
ProviderStatusV1
search_plan budget fields
cheapy doctor baseline
schema export for contract review
```

- [ ] **Step 5: Commit final verification notes if any files changed**

Run:

```bash
git status --short
```

If no files changed, expected output is empty.

If files changed, run:

```bash
git add .
git commit -m "chore: finalize contract foundation"
```

Expected:

```text
[main
```

## Handoff Notes

After this plan is implemented, the next implementation plan should cover airport data and resolver:

- bundled airport snapshot schema
- offline generation script
- airport candidate model use
- city ambiguity handling
- nearby distance calculation
- hub source provenance

Do not start the `google_fli` provider until Contract V1 and airport resolver tests pass.
