# Cheapy Gate 8 Flexible Dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Gate 8 flexible-date search with true round-trip provider support while preserving the existing Contract V1 MCP tool shape.

**Architecture:** Keep `cheapy/search.py` as the public orchestration entrypoint, but move candidate ordering and budget accounting into `cheapy/search_planner.py`. Providers keep exact-date capabilities; expanded search is implemented by issuing multiple exact one-way or exact round-trip provider calls under a 10-call Gate 8 budget.

**Tech Stack:** Python 3.11+, Pydantic v2 strict models, pytest, uv, FastMCP, upstream `fli` package for the `google_fli` live provider.

---

## Working Notes

- Read `AGENTS.md` before editing.
- Read `.codex/skills/cheapy/SKILL.md` before touching MCP, CLI, contracts, packaging, or tests.
- The current worktree may contain user edits in:
  - `cheapy/providers/google_fli/adapter.py`
  - `cheapy/providers/google_fli/normalizer.py`
  - `tests/test_google_fli_normalizer.py`
  - `tests/test_google_fli_provider.py`
- Do not revert or overwrite those edits. Read the files before editing and layer Gate 8 changes on top.
- Keep default tests offline. Live smoke tests must remain opt-in.
- Keep `search_cheapest_flights` as the only MCP search tool.
- Keep the public Contract V1 request/response shape unchanged, except adding validation that `return_date >= departure_date`.

## File Structure

- Modify `cheapy/models/contracts.py`
  - Add request-level validation that `return_date` cannot be earlier than `departure_date`.

- Modify `cheapy/providers/base.py`
  - Add requested-date fields to `ProviderExactOneWayRequest` without breaking current callers.
  - Add `ProviderExactRoundTripRequest`.
  - Extend `FlightProvider` with optional round-trip search method shape.

- Modify `cheapy/providers/registry.py`
  - Validate provider shape based on advertised capabilities.

- Modify `cheapy/providers/google_fli/manifest.toml`
  - Add `exact_round_trip` to capabilities.

- Create `cheapy/search_planner.py`
  - Generate exact and flexible candidates.
  - Expand candidates into provider-call selections.
  - Apply the Gate 8 10-call budget.
  - Produce `SearchPlanV1` and truncation metadata.

- Modify `cheapy/search.py`
  - Route exact and expanded one-way/round-trip requests.
  - Execute planned provider calls.
  - Add request IDs with trip shape and return date.
  - Add flexible-date warnings and truncation warnings.
  - Deduplicate before sorting, `max_results`, and ranking.

- Modify `cheapy/mcp.py`
  - Keep the tool schema unchanged.
  - Keep `search_cheapest_flights` delegating to the updated search entrypoint.

- Modify `cheapy/providers/google_fli/adapter.py`
  - Build one-way and round-trip `fli` filters from provider-local requests.

- Modify `cheapy/providers/google_fli/provider.py`
  - Add `search_exact_round_trip`.

- Modify `cheapy/providers/google_fli/normalizer.py`
  - Normalize one-way and round-trip provider requests into Contract V1 offers.
  - Populate requested/actual departure and return fields, offsets, and flags.

- Modify `cheapy/agent_hooks.py`
  - Update managed instructions so expanded flexible dates and round-trip search are no longer described as deferred.

- Modify `.codex/skills/cheapy/SKILL.md`
  - Update the project-local skill text to allow `search_mode="expanded"` and `return_date`.

- Tests:
  - `tests/test_contracts.py`
  - `tests/test_providers.py`
  - `tests/test_search_planner.py`
  - `tests/test_search.py`
  - `tests/test_google_fli_provider.py`
  - `tests/test_google_fli_normalizer.py`
  - `tests/test_mcp.py`
  - `tests/test_agent_hooks.py`
  - `tests/test_schema_export.py`

---

### Task 1: Contract And Provider Request Models

**Files:**
- Modify: `cheapy/models/contracts.py`
- Modify: `cheapy/providers/base.py`
- Modify: `cheapy/providers/registry.py`
- Modify: `cheapy/providers/google_fli/manifest.toml`
- Test: `tests/test_contracts.py`
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write failing contract/provider model tests**

Add these tests to `tests/test_contracts.py`:

```python
import pytest
from pydantic import ValidationError


def test_search_request_rejects_return_date_before_departure_date() -> None:
    with pytest.raises(ValidationError, match="return_date must not be earlier"):
        SearchRequestV1(
            schema_version="1",
            origin="SGN",
            destination="BKK",
            departure_date="2026-07-10",
            return_date="2026-07-09",
        )


def test_search_request_accepts_same_day_round_trip() -> None:
    request = SearchRequestV1(
        schema_version="1",
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-10",
    )

    assert request.return_date == "2026-07-10"
```

Add these imports and tests to `tests/test_providers.py`:

```python
from cheapy.providers.base import ProviderExactRoundTripRequest
```

```python
def test_provider_exact_one_way_request_defaults_requested_fields() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-11",
    )

    assert request.requested_origin == "SGN"
    assert request.requested_destination == "BKK"
    assert request.requested_departure_date == "2026-07-11"


def test_provider_exact_one_way_request_accepts_flexible_actual_date() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-12",
        requested_origin="SGN",
        requested_destination="BKK",
        requested_departure_date="2026-07-10",
    )

    assert request.departure_date == "2026-07-12"
    assert request.requested_departure_date == "2026-07-10"


def test_provider_exact_round_trip_request_defaults_requested_fields() -> None:
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )

    assert request.origin == "SGN"
    assert request.destination == "BKK"
    assert request.departure_date == "2026-07-10"
    assert request.return_date == "2026-07-17"
    assert request.requested_origin == "SGN"
    assert request.requested_destination == "BKK"
    assert request.requested_departure_date == "2026-07-10"
    assert request.requested_return_date == "2026-07-17"
    assert request.passengers == PassengersV1()


def test_provider_exact_round_trip_request_rejects_return_before_departure() -> None:
    with pytest.raises(ValidationError, match="return_date must not be earlier"):
        ProviderExactRoundTripRequest(
            origin="SGN",
            destination="BKK",
            departure_date="2026-07-10",
            return_date="2026-07-09",
        )
```

Update the manifest tests in `tests/test_providers.py` so `google_fli` advertises both capabilities:

```python
assert manifest == ProviderManifest(
    manifest_schema_version="1",
    name="google_fli",
    display_name="Google Fli live provider",
    default_enabled=True,
    provider_kind="live",
    module="cheapy.providers.google_fli.provider",
    capabilities=["exact_one_way", "exact_round_trip"],
)
```

```python
assert [provider.capabilities for provider in providers] == [
    ("exact_one_way", "exact_round_trip"),
    ("exact_one_way",),
]
```

```python
assert providers[0].capabilities == ("exact_one_way", "exact_round_trip")
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```bash
uv run pytest tests/test_contracts.py::test_search_request_rejects_return_date_before_departure_date tests/test_contracts.py::test_search_request_accepts_same_day_round_trip tests/test_providers.py -v
```

Expected: new tests fail because `ProviderExactRoundTripRequest`, requested fields, round-trip manifest capability, and return-date ordering validation are not implemented.

- [ ] **Step 3: Implement request validation and provider-local models**

In `cheapy/models/contracts.py`, add a model validator to `SearchRequestV1`:

```python
    @model_validator(mode="after")
    def validate_return_date_order(self) -> Self:
        if self.return_date is None:
            return self
        departure = datetime.strptime(self.departure_date, "%Y-%m-%d")
        return_date = datetime.strptime(self.return_date, "%Y-%m-%d")
        if return_date < departure:
            raise ValueError("return_date must not be earlier than departure_date")
        return self
```

In `cheapy/providers/base.py`, update imports:

```python
from typing import Any, Protocol, Self
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

Replace the current `ProviderExactOneWayRequest` with this shape:

```python
class ProviderExactOneWayRequest(_ProviderModel):
    """Provider-local request for an exact one-way flight search."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: str
    requested_origin: str = Field(min_length=3, max_length=3)
    requested_destination: str = Field(min_length=3, max_length=3)
    requested_departure_date: str
    passengers: PassengersV1 = Field(default_factory=PassengersV1)

    @model_validator(mode="before")
    @classmethod
    def default_requested_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        values.setdefault("requested_origin", values.get("origin"))
        values.setdefault("requested_destination", values.get("destination"))
        values.setdefault("requested_departure_date", values.get("departure_date"))
        return values

    @field_validator("departure_date", "requested_departure_date")
    @classmethod
    def validate_departure_date(cls, value: str) -> str:
        return _validate_yyyy_mm_dd(value)
```

Add the round-trip model below it:

```python
class ProviderExactRoundTripRequest(_ProviderModel):
    """Provider-local request for an exact round-trip flight search."""

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: str
    return_date: str
    requested_origin: str = Field(min_length=3, max_length=3)
    requested_destination: str = Field(min_length=3, max_length=3)
    requested_departure_date: str
    requested_return_date: str
    passengers: PassengersV1 = Field(default_factory=PassengersV1)

    @model_validator(mode="before")
    @classmethod
    def default_requested_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        values.setdefault("requested_origin", values.get("origin"))
        values.setdefault("requested_destination", values.get("destination"))
        values.setdefault("requested_departure_date", values.get("departure_date"))
        values.setdefault("requested_return_date", values.get("return_date"))
        return values

    @field_validator(
        "departure_date",
        "return_date",
        "requested_departure_date",
        "requested_return_date",
    )
    @classmethod
    def validate_date(cls, value: str) -> str:
        return _validate_yyyy_mm_dd(value)

    @model_validator(mode="after")
    def validate_return_date_order(self) -> Self:
        departure = datetime.strptime(self.departure_date, "%Y-%m-%d")
        return_date = datetime.strptime(self.return_date, "%Y-%m-%d")
        if return_date < departure:
            raise ValueError("return_date must not be earlier than departure_date")
        return self
```

Replace duplicated date validation logic with this helper in `cheapy/providers/base.py`:

```python
def _validate_yyyy_mm_dd(value: str) -> str:
    if not _YYYY_MM_DD_RE.fullmatch(value):
        raise ValueError("Date must use YYYY-MM-DD format")

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format") from exc
    return value
```

Extend the `FlightProvider` protocol:

```python
    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        """Return exact round-trip provider results."""
```

In `cheapy/providers/registry.py`, validate methods by advertised capability:

```python
    required_methods = {
        "exact_one_way": "search_exact_one_way",
        "exact_round_trip": "search_exact_round_trip",
    }
    for capability in manifest.capabilities:
        method_name = required_methods.get(capability)
        if method_name is not None and not callable(getattr(provider, method_name, None)):
            raise ProviderLoadError(_provider_load_error_message(manifest))
```

In `cheapy/providers/google_fli/manifest.toml`, set:

```toml
capabilities = ["exact_one_way", "exact_round_trip"]
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_providers.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cheapy/models/contracts.py cheapy/providers/base.py cheapy/providers/registry.py cheapy/providers/google_fli/manifest.toml tests/test_contracts.py tests/test_providers.py
git commit -m "feat: add round-trip provider request contracts"
```

---

### Task 2: Planner Candidate Generation And Budget Accounting

**Files:**
- Create: `cheapy/search_planner.py`
- Test: `tests/test_search_planner.py`

- [ ] **Step 1: Write failing planner tests**

Create `tests/test_search_planner.py`:

```python
from __future__ import annotations

from cheapy.models import CandidateFamily, PassengersV1, SearchMode, SearchRequestV1
from cheapy.search_planner import (
    EXACT_ONE_WAY_CAPABILITY,
    EXACT_ROUND_TRIP_CAPABILITY,
    GATE_8_PROVIDER_CALL_BUDGET,
    plan_search,
)


class _Provider:
    def __init__(self, name: str, capabilities: tuple[str, ...]) -> None:
        self.name = name
        self.capabilities = capabilities


def _request(**overrides: object) -> SearchRequestV1:
    data: dict[str, object] = {
        "schema_version": "1",
        "origin": "SGN",
        "destination": "BKK",
        "departure_date": "2026-07-10",
        "return_date": None,
        "search_mode": "expanded",
        "passengers": PassengersV1(),
        "max_results": 5,
    }
    data.update(overrides)
    return SearchRequestV1.model_validate(data)


def test_plan_expanded_one_way_orders_exact_then_nearest_dates() -> None:
    provider = _Provider("one", (EXACT_ONE_WAY_CAPABILITY,))

    planned = plan_search(_request(), "SGN", "BKK", [provider])

    assert [(call.candidate.departure_offset_days, call.provider.name) for call in planned.selected_calls] == [
        (0, "one"),
        (-1, "one"),
        (1, "one"),
        (-2, "one"),
        (2, "one"),
        (-3, "one"),
        (3, "one"),
    ]
    assert planned.search_plan.planned_candidate_count == 7
    assert planned.search_plan.executed_candidate_count == 7
    assert planned.search_plan.planned_provider_call_count == 7
    assert planned.search_plan.executed_provider_call_count == 7
    assert planned.search_plan.truncated is False


def test_plan_expanded_round_trip_uses_true_round_trip_capability() -> None:
    provider = _Provider("round", (EXACT_ROUND_TRIP_CAPABILITY,))

    planned = plan_search(
        _request(return_date="2026-07-17"),
        "SGN",
        "BKK",
        [provider],
    )

    selected = [
        (
            call.candidate.departure_offset_days,
            call.candidate.return_offset_days,
            call.candidate.capability,
        )
        for call in planned.selected_calls
    ]

    assert selected[:5] == [
        (0, 0, "exact_round_trip"),
        (-1, 0, "exact_round_trip"),
        (0, -1, "exact_round_trip"),
        (0, 1, "exact_round_trip"),
        (1, 0, "exact_round_trip"),
    ]
    assert len(planned.selected_calls) == GATE_8_PROVIDER_CALL_BUDGET
    assert planned.search_plan.planned_candidate_count == 49
    assert planned.search_plan.executed_candidate_count == 10
    assert planned.search_plan.planned_provider_call_count == 49
    assert planned.search_plan.executed_provider_call_count == 10
    assert planned.search_plan.truncated is True
    assert planned.search_plan.truncated_families == [CandidateFamily.FLEXIBLE_DATES]


def test_plan_skips_invalid_round_trip_flexible_pairs() -> None:
    provider = _Provider("round", (EXACT_ROUND_TRIP_CAPABILITY,))

    planned = plan_search(
        _request(departure_date="2026-07-10", return_date="2026-07-10"),
        "SGN",
        "BKK",
        [provider],
    )

    assert all(
        call.candidate.return_date is None
        or call.candidate.return_date >= call.candidate.departure_date
        for call in planned.selected_calls
    )
    assert planned.search_plan.planned_candidate_count < 49


def test_plan_budget_can_end_mid_candidate_provider_list() -> None:
    providers = [
        _Provider(f"provider_{index:02d}", (EXACT_ONE_WAY_CAPABILITY,))
        for index in range(12)
    ]

    planned = plan_search(_request(search_mode=SearchMode.EXACT), "SGN", "BKK", providers)

    assert [call.provider.name for call in planned.selected_calls] == [
        f"provider_{index:02d}" for index in range(10)
    ]
    assert planned.search_plan.planned_candidate_count == 1
    assert planned.search_plan.executed_candidate_count == 1
    assert planned.search_plan.planned_provider_call_count == 12
    assert planned.search_plan.executed_provider_call_count == 10
    assert planned.search_plan.truncated is True
    assert planned.search_plan.truncated_families == [CandidateFamily.EXACT]
```

- [ ] **Step 2: Run planner tests to verify failure**

Run:

```bash
uv run pytest tests/test_search_planner.py -v
```

Expected: FAIL because `cheapy/search_planner.py` does not exist.

- [ ] **Step 3: Implement the planner module**

Create `cheapy/search_planner.py` with these public constants and dataclasses:

```python
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from cheapy.models import (
    CandidateFamily,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
)
from cheapy.providers.base import FlightProvider


EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
GATE_8_PROVIDER_CALL_BUDGET = 10
_FLEXIBLE_OFFSETS = (-3, -2, -1, 0, 1, 2, 3)
_ONE_WAY_ORDER = (0, -1, 1, -2, 2, -3, 3)
_FAMILY_ORDER = (
    CandidateFamily.EXACT,
    CandidateFamily.FLEXIBLE_DATES,
    CandidateFamily.NEARBY_ORIGIN,
    CandidateFamily.NEARBY_DESTINATION,
    CandidateFamily.SPLIT_TICKET,
)


@dataclass(frozen=True)
class SearchCandidate:
    index: int
    family: CandidateFamily
    capability: str
    origin: str
    destination: str
    departure_date: str
    return_date: str | None
    requested_departure_date: str
    requested_return_date: str | None
    departure_offset_days: int
    return_offset_days: int | None


@dataclass(frozen=True)
class PlannedProviderCall:
    candidate: SearchCandidate
    provider: FlightProvider


@dataclass(frozen=True)
class PlannedSearch:
    selected_calls: list[PlannedProviderCall]
    search_plan: SearchPlanV1
```

Implement these helpers in the same file:

```python
def plan_search(
    request: SearchRequestV1,
    origin: str,
    destination: str,
    providers: list[FlightProvider],
) -> PlannedSearch:
    candidates = _candidate_list(request, origin, destination)
    planned_calls = _planned_calls(candidates, providers)
    selected_calls = planned_calls[:GATE_8_PROVIDER_CALL_BUDGET]
    truncated_families = _truncated_families(planned_calls, selected_calls)
    return PlannedSearch(
        selected_calls=selected_calls,
        search_plan=_search_plan(candidates, planned_calls, selected_calls, truncated_families, request.search_mode),
    )
```

```python
def _candidate_list(
    request: SearchRequestV1,
    origin: str,
    destination: str,
) -> list[SearchCandidate]:
    if request.return_date is None:
        offsets = (0,) if request.search_mode == SearchMode.EXACT else _ONE_WAY_ORDER
        return [
            _one_way_candidate(index, request, origin, destination, offset)
            for index, offset in enumerate(offsets)
        ]

    if request.search_mode == SearchMode.EXACT:
        return [_round_trip_candidate(0, request, origin, destination, 0, 0)]

    pairs = [(0, 0)] + sorted(
        [
            (departure_offset, return_offset)
            for departure_offset in _FLEXIBLE_OFFSETS
            for return_offset in _FLEXIBLE_OFFSETS
            if (departure_offset, return_offset) != (0, 0)
        ],
        key=lambda pair: (
            abs(pair[0]) + abs(pair[1]),
            abs(pair[0]),
            abs(pair[1]),
            pair[0],
            pair[1],
        ),
    )
    candidates: list[SearchCandidate] = []
    for pair in pairs:
        candidate = _round_trip_candidate(len(candidates), request, origin, destination, pair[0], pair[1])
        if candidate.return_date is not None and candidate.return_date >= candidate.departure_date:
            candidates.append(candidate)
    return candidates
```

```python
def _one_way_candidate(
    index: int,
    request: SearchRequestV1,
    origin: str,
    destination: str,
    departure_offset: int,
) -> SearchCandidate:
    family = CandidateFamily.EXACT if departure_offset == 0 else CandidateFamily.FLEXIBLE_DATES
    return SearchCandidate(
        index=index,
        family=family,
        capability=EXACT_ONE_WAY_CAPABILITY,
        origin=origin,
        destination=destination,
        departure_date=_offset_date(request.departure_date, departure_offset),
        return_date=None,
        requested_departure_date=request.departure_date,
        requested_return_date=None,
        departure_offset_days=departure_offset,
        return_offset_days=None,
    )
```

```python
def _round_trip_candidate(
    index: int,
    request: SearchRequestV1,
    origin: str,
    destination: str,
    departure_offset: int,
    return_offset: int,
) -> SearchCandidate:
    assert request.return_date is not None
    family = (
        CandidateFamily.EXACT
        if departure_offset == 0 and return_offset == 0
        else CandidateFamily.FLEXIBLE_DATES
    )
    return SearchCandidate(
        index=index,
        family=family,
        capability=EXACT_ROUND_TRIP_CAPABILITY,
        origin=origin,
        destination=destination,
        departure_date=_offset_date(request.departure_date, departure_offset),
        return_date=_offset_date(request.return_date, return_offset),
        requested_departure_date=request.departure_date,
        requested_return_date=request.return_date,
        departure_offset_days=departure_offset,
        return_offset_days=return_offset,
    )
```

```python
def _planned_calls(
    candidates: list[SearchCandidate],
    providers: list[FlightProvider],
) -> list[PlannedProviderCall]:
    calls: list[PlannedProviderCall] = []
    for candidate in candidates:
        for provider in providers:
            if candidate.capability in provider.capabilities:
                calls.append(PlannedProviderCall(candidate=candidate, provider=provider))
    return calls
```

```python
def _search_plan(
    candidates: list[SearchCandidate],
    planned_calls: list[PlannedProviderCall],
    selected_calls: list[PlannedProviderCall],
    truncated_families: list[CandidateFamily],
    search_mode: SearchMode,
) -> SearchPlanV1:
    planned_candidate_counts = Counter(candidate.family for candidate in candidates)
    executed_candidate_indexes = {call.candidate.index for call in selected_calls}
    executed_provider_counts = Counter(call.candidate.family for call in selected_calls)
    families = [
        family
        for family in _FAMILY_ORDER
        if planned_candidate_counts.get(family, 0) > 0
    ]
    return SearchPlanV1(
        search_mode=search_mode,
        planned_candidate_count=len(candidates),
        executed_candidate_count=len(executed_candidate_indexes),
        planned_provider_call_count=len(planned_calls),
        executed_provider_call_count=len(selected_calls),
        candidate_count_by_family={
            family: planned_candidate_counts[family] for family in families
        },
        provider_call_count_by_family={
            family: executed_provider_counts[family]
            for family in families
            if executed_provider_counts.get(family, 0) > 0
        },
        truncated=bool(truncated_families),
        truncated_families=truncated_families,
        candidate_families=families,
    )
```

```python
def _truncated_families(
    planned_calls: list[PlannedProviderCall],
    selected_calls: list[PlannedProviderCall],
) -> list[CandidateFamily]:
    selected_call_ids = {
        (call.candidate.index, call.provider.name)
        for call in selected_calls
    }
    truncated = {
        call.candidate.family
        for call in planned_calls
        if (call.candidate.index, call.provider.name) not in selected_call_ids
    }
    return [family for family in _FAMILY_ORDER if family in truncated]
```

```python
def _offset_date(value: str, offset_days: int) -> str:
    parsed = date.fromisoformat(value)
    return (parsed + timedelta(days=offset_days)).isoformat()
```

- [ ] **Step 4: Run planner tests**

Run:

```bash
uv run pytest tests/test_search_planner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cheapy/search_planner.py tests/test_search_planner.py
git commit -m "feat: add flexible date search planner"
```

---

### Task 3: Core Search Routing, Request IDs, And Provider Call Execution

**Files:**
- Modify: `cheapy/search.py`
- Test: `tests/test_search.py`

- [ ] **Step 1: Write failing search orchestration tests**

Add imports in `tests/test_search.py`:

```python
from cheapy.models import WarningCode
from cheapy.providers.base import ProviderExactRoundTripRequest
```

Update `_offer` to accept flexible and return-date fields:

```python
def _offer(
    *,
    offer_id: str,
    provider: str,
    currency: str,
    price_amount: float,
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
        requested_origin="CXR",
        requested_destination="SGN",
        actual_origin="CXR",
        actual_destination="SGN",
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
                origin="CXR",
                destination="SGN",
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

Add a round-trip fake provider:

```python
class _RoundTripProvider:
    name = "round_provider"
    capabilities = ("exact_round_trip",)

    def __init__(self) -> None:
        self.seen_requests: list[ProviderExactRoundTripRequest] = []

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        self.seen_requests.append(request)
        return ProviderResult(
            provider_name=self.name,
            capability="exact_round_trip",
            status=ProviderStatusCode.SUCCESS,
            offers=[
                _offer(
                    offer_id=f"round:{request.departure_date}:{request.return_date}",
                    provider=self.name,
                    currency="USD",
                    price_amount=100.0,
                    requested_return_date=request.requested_return_date,
                    actual_return_date=request.return_date,
                    return_offset_days=0,
                )
            ],
            warnings=[],
            errors=[],
            duration_ms=1,
            retryable=False,
        )
```

Add these tests:

```python
def test_search_exact_round_trip_routes_to_round_trip_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RoundTripProvider()
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(_request(return_date="2026-07-15"))

    assert response.status == SearchStatus.SUCCESS
    assert response.request_id == "search:round_trip:CXR:SGN:2026-07-10:2026-07-15:exact:1:0:0:0:5"
    assert len(provider.seen_requests) == 1
    assert provider.seen_requests[0].return_date == "2026-07-15"
    assert response.provider_statuses[0].capability == "exact_round_trip"
    assert response.search_plan.candidate_count_by_family == {CandidateFamily.EXACT: 1}


def test_search_exact_round_trip_requires_round_trip_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [create_manual_fixture()],
    )

    response = search_exact(_request(return_date="2026-07-15"))

    assert response.status == SearchStatus.FAILED
    assert response.errors[0].code == ErrorCode.NO_PROVIDER_AVAILABLE
    assert response.errors[0].details["reason"] == "no_exact_round_trip_provider"
    assert response.search_plan.planned_candidate_count == 1
    assert response.search_plan.executed_provider_call_count == 0
```

- [ ] **Step 2: Run failing search tests**

Run:

```bash
uv run pytest tests/test_search.py::test_search_exact_round_trip_routes_to_round_trip_capability tests/test_search.py::test_search_exact_round_trip_requires_round_trip_provider -v
```

Expected: FAIL because round-trip routing and request IDs are not implemented.

- [ ] **Step 3: Implement core routing helpers**

In `cheapy/search.py`, import planner symbols:

```python
from cheapy.search_planner import (
    EXACT_ONE_WAY_CAPABILITY,
    EXACT_ROUND_TRIP_CAPABILITY,
    PlannedProviderCall,
    SearchCandidate,
    plan_search,
)
```

Replace `_EXACT_CAPABILITY` usage with `EXACT_ONE_WAY_CAPABILITY` for one-way exact calls. Add:

```python
def _required_capability(request: SearchRequestV1) -> str:
    if request.return_date is None:
        return EXACT_ONE_WAY_CAPABILITY
    return EXACT_ROUND_TRIP_CAPABILITY
```

Update `_request_id`:

```python
def _request_id(request: SearchRequestV1, origin: str, destination: str) -> str:
    passengers = request.passengers
    trip_shape = "one_way" if request.return_date is None else "round_trip"
    return_date = request.return_date if request.return_date is not None else "none"
    return (
        f"search:{trip_shape}:{origin}:{destination}:{request.departure_date}:"
        f"{return_date}:{request.search_mode.value}:{passengers.adults}:"
        f"{passengers.children}:{passengers.infants_on_lap}:"
        f"{passengers.infants_in_seat}:{request.max_results}"
    )
```

Remove the unsupported-scope rejection for `search_mode != exact` and `return_date`. Keep airport failure behavior unchanged.

After provider loading, filter by `_required_capability(request)` and return a planned unexecuted response when no provider supports it:

```python
required_capability = _required_capability(request)
capable_providers = [
    provider for provider in providers if required_capability in provider.capabilities
]
if not capable_providers:
    reason = (
        "no_exact_one_way_provider"
        if required_capability == EXACT_ONE_WAY_CAPABILITY
        else "no_exact_round_trip_provider"
    )
    planned = plan_search(request, origin.iata, destination.iata, providers)
    return _failed_response(
        request_id=request_id,
        errors=[
            _error(
                code=ErrorCode.NO_PROVIDER_AVAILABLE,
                message_en="No enabled provider supports the requested search capability.",
                details={"reason": reason},
            )
        ],
        search_plan=planned.search_plan,
    )
```

Plan and execute calls:

```python
planned = plan_search(request, origin.iata, destination.iata, providers)
provider_results = asyncio.run(_call_planned_providers(planned.selected_calls, request.passengers))
return _response_from_provider_results(
    request=request,
    request_id=request_id,
    provider_results=provider_results,
    search_plan=planned.search_plan,
)
```

Add planned call execution:

```python
async def _call_planned_providers(
    planned_calls: list[PlannedProviderCall],
    passengers: PassengersV1,
) -> list[ProviderResult]:
    results: list[ProviderResult] = []
    for planned_call in planned_calls:
        provider = planned_call.provider
        candidate = planned_call.candidate
        try:
            if candidate.capability == EXACT_ONE_WAY_CAPABILITY:
                raw_result = await provider.search_exact_one_way(
                    _one_way_provider_request(candidate, passengers)
                )
            else:
                raw_result = await provider.search_exact_round_trip(
                    _round_trip_provider_request(candidate, passengers)
                )
            results.append(_normalize_provider_result(provider, candidate.capability, raw_result))
        except Exception as exc:
            results.append(_provider_exception_result(provider, candidate.capability, exc))
    return results
```

Add request builders:

```python
def _one_way_provider_request(
    candidate: SearchCandidate,
    passengers: PassengersV1,
) -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin=candidate.origin,
        destination=candidate.destination,
        departure_date=candidate.departure_date,
        requested_origin=candidate.origin,
        requested_destination=candidate.destination,
        requested_departure_date=candidate.requested_departure_date,
        passengers=passengers,
    )
```

```python
def _round_trip_provider_request(
    candidate: SearchCandidate,
    passengers: PassengersV1,
) -> ProviderExactRoundTripRequest:
    assert candidate.return_date is not None
    assert candidate.requested_return_date is not None
    return ProviderExactRoundTripRequest(
        origin=candidate.origin,
        destination=candidate.destination,
        departure_date=candidate.departure_date,
        return_date=candidate.return_date,
        requested_origin=candidate.origin,
        requested_destination=candidate.destination,
        requested_departure_date=candidate.requested_departure_date,
        requested_return_date=candidate.requested_return_date,
        passengers=passengers,
    )
```

Update provider result normalization helpers to accept `capability: str` instead of hard-coding one-way:

```python
def _normalize_provider_result(
    provider: FlightProvider,
    capability: str,
    raw_result: object,
) -> ProviderResult:
    try:
        result = ProviderResult.model_validate(raw_result)
    except Exception as exc:
        return _provider_malformed_result(provider, capability, exc)

    result = result.model_copy(update={"capability": capability})
    if result.status != ProviderStatusCode.SUCCESS and not result.errors:
        error = _provider_status_error(result)
        return result.model_copy(update={"errors": [error]})

    return result
```

```python
def _provider_exception_result(
    provider: FlightProvider,
    capability: str,
    exc: Exception,
) -> ProviderResult:
    return _provider_failed_result(
        provider_name=provider.name,
        capability=capability,
        message_en="Provider raised an unexpected exception.",
        details={
            "provider": provider.name,
            "capability": capability,
            "exception_type": type(exc).__name__,
        },
    )
```

Update `_provider_malformed_result` and `_provider_failed_result` to accept a
`capability: str` argument and write that capability into the returned
`ProviderResult` and error details. Update `_response_from_provider_results` to
receive `search_plan: SearchPlanV1` and assign that object directly to the
response `search_plan` field.

- [ ] **Step 4: Run focused search tests**

Run:

```bash
uv run pytest tests/test_search.py::test_search_exact_round_trip_routes_to_round_trip_capability tests/test_search.py::test_search_exact_round_trip_requires_round_trip_provider -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cheapy/search.py tests/test_search.py
git commit -m "feat: route search through planned provider calls"
```

---

### Task 4: Expanded Flexible Search, Truncation, Warnings, And Deduplication

**Files:**
- Modify: `cheapy/search.py`
- Test: `tests/test_search.py`

- [ ] **Step 1: Write failing expanded search tests**

Add these test helpers to `tests/test_search.py`:

```python
class _RecordingOneWayProvider:
    name = "recording_one_way"
    capabilities = ("exact_one_way",)

    def __init__(self) -> None:
        self.seen_requests: list[ProviderExactOneWayRequest] = []

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        self.seen_requests.append(request)
        return ProviderResult(
            provider_name=self.name,
            capability="exact_one_way",
            status=ProviderStatusCode.SUCCESS,
            offers=[
                _offer(
                    offer_id=f"one:{request.departure_date}",
                    provider=self.name,
                    currency="USD",
                    price_amount=100.0 + len(self.seen_requests),
                    requested_departure_date=request.requested_departure_date,
                    actual_departure_date=request.departure_date,
                    departure_offset_days=(
                        0
                        if request.departure_date == request.requested_departure_date
                        else 1
                    ),
                )
            ],
            warnings=[],
            errors=[],
            duration_ms=1,
            retryable=False,
        )
```

Add these tests:

```python
def test_search_expanded_one_way_executes_flexible_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingOneWayProvider()
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(_request(search_mode=SearchMode.EXPANDED))

    assert [request.departure_date for request in provider.seen_requests] == [
        "2026-07-10",
        "2026-07-09",
        "2026-07-11",
        "2026-07-08",
        "2026-07-12",
        "2026-07-07",
        "2026-07-13",
    ]
    assert response.search_plan.search_mode == SearchMode.EXPANDED
    assert response.search_plan.candidate_families == [
        CandidateFamily.EXACT,
        CandidateFamily.FLEXIBLE_DATES,
    ]
    assert response.search_plan.truncated is False
    assert any(warning.code == WarningCode.FLEXIBLE_DATE_USED for warning in response.warnings)


def test_search_expanded_round_trip_truncates_to_gate_8_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RoundTripProvider()
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(
        _request(search_mode=SearchMode.EXPANDED, return_date="2026-07-17")
    )

    assert len(provider.seen_requests) == 10
    assert response.search_plan.planned_candidate_count == 49
    assert response.search_plan.executed_provider_call_count == 10
    assert response.search_plan.truncated is True
    assert response.search_plan.truncated_families == [CandidateFamily.FLEXIBLE_DATES]
    assert any(
        warning.code == WarningCode.CANDIDATE_FAMILY_TRUNCATED
        for warning in response.warnings
    )


def test_search_deduplicates_same_provider_itineraries_before_max_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = _offer(
        offer_id="dup:expensive",
        provider="dup_provider",
        currency="USD",
        price_amount=200.0,
    )
    cheaper_duplicate = duplicate.model_copy(
        update={"offer_id": "dup:cheap", "price_amount": 100.0}
    )
    result = ProviderResult(
        provider_name="dup_provider",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[duplicate, cheaper_duplicate],
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

    assert [offer.offer_id for offer in response.offers] == ["dup:cheap"]
    assert response.offers[0].global_rank == 1
```

- [ ] **Step 2: Run failing expanded tests**

Run:

```bash
uv run pytest tests/test_search.py::test_search_expanded_one_way_executes_flexible_dates tests/test_search.py::test_search_expanded_round_trip_truncates_to_gate_8_budget tests/test_search.py::test_search_deduplicates_same_provider_itineraries_before_max_results -v
```

Expected: FAIL until warnings and deduplication are implemented.

- [ ] **Step 3: Implement warnings and deduplication**

In `cheapy/search.py`, import `WarningCode` and `WarningV1`.

Add response warning assembly:

```python
def _response_warnings(
    *,
    provider_results: list[ProviderResult],
    returned_offers: list[FlightOfferV1],
    search_plan: SearchPlanV1,
) -> list[WarningV1]:
    warnings = [warning for result in provider_results for warning in result.warnings]
    if any(
        offer.departure_offset_days != 0
        or (offer.return_offset_days is not None and offer.return_offset_days != 0)
        for offer in returned_offers
    ):
        warnings.append(
            _warning(
                code=WarningCode.FLEXIBLE_DATE_USED,
                message_en="Returned offers include dates outside the exact requested dates.",
                details={"candidate_family": CandidateFamily.FLEXIBLE_DATES.value},
            )
        )
    for family in search_plan.truncated_families:
        warnings.append(
            _warning(
                code=WarningCode.CANDIDATE_FAMILY_TRUNCATED,
                message_en="Some search candidates were skipped because of the provider-call budget.",
                details={"candidate_family": family.value},
            )
        )
    return warnings
```

Add `_warning`:

```python
def _warning(
    *,
    code: WarningCode,
    message_en: str,
    details: dict[str, object],
) -> WarningV1:
    return WarningV1(
        code=code,
        severity=Severity.WARNING,
        message_en=message_en,
        details=details,
        retryable=False,
    )
```

Deduplicate before sorting and truncation:

```python
def _deduplicate_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    by_signature: dict[tuple[object, ...], FlightOfferV1] = {}
    ordered_signatures: list[tuple[object, ...]] = []
    for offer in offers:
        signature = _same_provider_itinerary_signature(offer)
        current = by_signature.get(signature)
        if current is None:
            by_signature[signature] = offer
            ordered_signatures.append(signature)
        elif (offer.price_amount, offer.offer_id) < (
            current.price_amount,
            current.offer_id,
        ):
            by_signature[signature] = offer
    return [by_signature[signature] for signature in ordered_signatures]
```

```python
def _same_provider_itinerary_signature(offer: FlightOfferV1) -> tuple[object, ...]:
    return (
        offer.provider,
        offer.actual_origin,
        offer.actual_destination,
        offer.actual_departure_date,
        offer.actual_return_date,
        offer.fare_details_status,
        tuple(
            (
                leg.origin,
                leg.destination,
                leg.departure_time,
                leg.arrival_time,
                leg.airline_code,
                leg.flight_number,
            )
            for leg in offer.legs
        ),
    )
```

Update `_response_from_provider_results` order:

```python
offers = _deduplicate_offers(
    [offer for result in provider_results for offer in result.offers]
)
returned_offers = _rank_offers(_sort_offers(offers)[: request.max_results])
warnings = _response_warnings(
    provider_results=provider_results,
    returned_offers=returned_offers,
    search_plan=search_plan,
)
```

- [ ] **Step 4: Run expanded search tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS after updating older request ID expectations from
`exact:CXR:SGN:2026-07-10:exact:1:0:0:0:5` to
`search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5`.

- [ ] **Step 5: Commit**

```bash
git add cheapy/search.py tests/test_search.py
git commit -m "feat: execute expanded flexible searches"
```

---

### Task 5: Google Fli Round-Trip Adapter And Provider

**Files:**
- Modify: `cheapy/providers/google_fli/adapter.py`
- Modify: `cheapy/providers/google_fli/provider.py`
- Test: `tests/test_google_fli_provider.py`

- [ ] **Step 1: Write failing Google Fli provider tests**

In `tests/test_google_fli_provider.py`, import:

```python
from cheapy.providers.base import ProviderExactRoundTripRequest
```

Add a round-trip request helper:

```python
def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-11",
        return_date="2026-06-18",
    )
```

Update `FakeAdapter`:

```python
        self.seen_round_trip_request: ProviderExactRoundTripRequest | None = None

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> list[object]:
        self.seen_round_trip_request = request
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
```

Add tests:

```python
def test_build_search_filters_maps_round_trip_request_to_fli_filters() -> None:
    filters = build_search_filters(_round_trip_request())

    assert filters.trip_type.name == "ROUND_TRIP"
    assert len(filters.flight_segments) == 2
    assert filters.flight_segments[0].departure_airport[0][0].name == "SGN"
    assert filters.flight_segments[0].arrival_airport[0][0].name == "BKK"
    assert filters.flight_segments[0].travel_date == "2026-06-11"
    assert filters.flight_segments[1].departure_airport[0][0].name == "BKK"
    assert filters.flight_segments[1].arrival_airport[0][0].name == "SGN"
    assert filters.flight_segments[1].travel_date == "2026-06-18"
    assert filters.show_all_results is False


def test_google_fli_provider_returns_round_trip_success_result() -> None:
    adapter = FakeAdapter([_flight()])
    provider = GoogleFliProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.seen_round_trip_request == _round_trip_request()
    assert result.provider_name == "google_fli"
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.SUCCESS
```

- [ ] **Step 2: Run failing Google Fli provider tests**

Run:

```bash
uv run pytest tests/test_google_fli_provider.py::test_build_search_filters_maps_round_trip_request_to_fli_filters tests/test_google_fli_provider.py::test_google_fli_provider_returns_round_trip_success_result -v
```

Expected: FAIL because round-trip adapter/provider methods are absent.

- [ ] **Step 3: Implement round-trip adapter/provider support**

In `cheapy/providers/google_fli/adapter.py`, update imports:

```python
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
```

Add an adapter method:

```python
    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> list[object]:
        return self._search(request)
```

Refactor one-way search to use a shared private method:

```python
    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> list[object]:
        return self._search(request)

    def _search(
        self,
        request: ProviderExactOneWayRequest | ProviderExactRoundTripRequest,
    ) -> list[object]:
        try:
            search = _search_class()()
            filters = build_search_filters(request)
            results = search.search(filters)
        except GoogleFliProviderError:
            raise
        except TimeoutError:
            raise
        except Exception as exc:
            raise GoogleFliProviderError(
                failure_type="transport_error",
                message_en="Google Fli transport failed.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=True,
                exception_type=type(exc).__name__,
            ) from exc

        if results is None:
            return []
        if isinstance(results, list):
            return results
        return list(results)
```

Update `build_search_filters`:

```python
def build_search_filters(
    request: ProviderExactOneWayRequest | ProviderExactRoundTripRequest,
) -> Any:
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
    segments = [
        FlightSegment(
            departure_airport=[[origin, 0]],
            arrival_airport=[[destination, 0]],
            travel_date=request.departure_date,
        )
    ]
    trip_type = TripType.ONE_WAY
    if isinstance(request, ProviderExactRoundTripRequest):
        segments.append(
            FlightSegment(
                departure_airport=[[destination, 0]],
                arrival_airport=[[origin, 0]],
                travel_date=request.return_date,
            )
        )
        trip_type = TripType.ROUND_TRIP
    return FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(
            adults=request.passengers.adults,
            children=request.passengers.children,
            infants_in_seat=request.passengers.infants_in_seat,
            infants_on_lap=request.passengers.infants_on_lap,
        ),
        flight_segments=segments,
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
        show_all_results=False,
    )
```

In `cheapy/providers/google_fli/provider.py`, add `EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"` locally or import the planner constant if that does not create an import cycle. Keep provider code independent by defining the string locally.

Update capabilities:

```python
capabilities = (CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)
```

Add:

```python
    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        return await self._search(
            request,
            capability=EXACT_ROUND_TRIP_CAPABILITY,
            search_method_name="search_exact_round_trip",
        )
```

Refactor one-way into the same private method:

```python
    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return await self._search(
            request,
            capability=CAPABILITY,
            search_method_name="search_exact_one_way",
        )
```

Add this private method:

```python
    async def _search(
        self,
        request: ProviderExactOneWayRequest | ProviderExactRoundTripRequest,
        *,
        capability: str,
        search_method_name: str,
    ) -> ProviderResult:
        started = perf_counter()
        try:
            search_method = getattr(self._adapter, search_method_name)
            flights = await asyncio.wait_for(
                asyncio.to_thread(search_method, request),
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
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    message_en="Google Fli provider timed out.",
                    failure_type="timeout",
                    retryable=True,
                    capability=capability,
                ),
            )
        except GoogleFliProviderError as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=exc.error_code,
                    message_en=exc.message_en,
                    failure_type=exc.failure_type,
                    retryable=exc.retryable,
                    capability=capability,
                    exception_type=exc.exception_type,
                ),
            )
        except Exception as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_FAILED,
                    message_en="Google Fli provider raised an unexpected exception.",
                    failure_type="unexpected_error",
                    retryable=False,
                    capability=capability,
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
            capability=capability,
            status=status,
            offers=offers,
            warnings=[],
            errors=errors,
            duration_ms=_duration_ms(started),
            retryable=any(error.retryable for error in errors),
        )
```

Update `_failed_result` and `_provider_error` to accept `capability: str` and
write that capability into the returned `ProviderResult` and error details.

- [ ] **Step 4: Run provider tests**

Run:

```bash
uv run pytest tests/test_google_fli_provider.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cheapy/providers/google_fli/adapter.py cheapy/providers/google_fli/provider.py tests/test_google_fli_provider.py
git commit -m "feat: add google fli round-trip provider path"
```

---

### Task 6: Google Fli Normalizer Round-Trip Fields And Flexible Flags

**Files:**
- Modify: `cheapy/providers/google_fli/normalizer.py`
- Test: `tests/test_google_fli_normalizer.py`

- [ ] **Step 1: Write failing normalizer tests**

In `tests/test_google_fli_normalizer.py`, import:

```python
from cheapy.providers.base import ProviderExactRoundTripRequest
```

Add helpers:

```python
def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-06-12",
        return_date="2026-06-19",
        requested_origin="SGN",
        requested_destination="BKK",
        requested_departure_date="2026-06-11",
        requested_return_date="2026-06-18",
    )


def _return_leg() -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(value="VJ"),
        flight_number="VJ802",
        departure_airport=SimpleNamespace(value="BKK"),
        arrival_airport=SimpleNamespace(value="SGN"),
        departure_datetime=datetime(2026, 6, 19, 11, 15),
        arrival_datetime=datetime(2026, 6, 19, 12, 45),
        duration=90,
    )
```

Add test:

```python
def test_normalize_flights_maps_round_trip_dates_and_flags() -> None:
    offers, errors = normalize_flights(
        [_flight(legs=[_leg(), _return_leg()], duration=180)],
        _round_trip_request(),
    )

    assert errors == []
    offer = offers[0]
    assert offer.provider == "google_fli"
    assert offer.requested_departure_date == "2026-06-11"
    assert offer.actual_departure_date == "2026-06-12"
    assert offer.departure_offset_days == 1
    assert offer.requested_return_date == "2026-06-18"
    assert offer.actual_return_date == "2026-06-19"
    assert offer.return_offset_days == 1
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.flags.uses_flexible_departure_date is True
    assert offer.flags.uses_flexible_return_date is True
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]
```

- [ ] **Step 2: Run failing normalizer test**

Run:

```bash
uv run pytest tests/test_google_fli_normalizer.py::test_normalize_flights_maps_round_trip_dates_and_flags -v
```

Expected: FAIL because normalizer assumes one-way dates and offsets.

- [ ] **Step 3: Implement round-trip-aware normalization**

In `cheapy/providers/google_fli/normalizer.py`, update imports:

```python
from datetime import date, datetime
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
```

Update `normalize_flights` and `_normalize_flight` request type:

```python
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
```

```python
def normalize_flights(
    flights: list[object],
    request: ProviderRequest,
    *,
    configured_currency: str | None = None,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
```

Add helpers:

```python
def _date_offset(actual: str, requested: str) -> int:
    return (date.fromisoformat(actual) - date.fromisoformat(requested)).days
```

```python
def _round_trip_return_departure_date(
    request: ProviderRequest,
    legs: list[FlightLegV1],
) -> str | None:
    if not isinstance(request, ProviderExactRoundTripRequest):
        return None
    for leg in legs:
        if leg.origin == request.destination:
            return leg.departure_time[:10]
    return request.return_date
```

Set `actual_origin` and `actual_destination` as outbound fields:

```python
actual_origin = first_leg.origin
actual_destination = request.destination
actual_departure_date = first_leg.departure_time[:10]
actual_return_date = _round_trip_return_departure_date(request, legs)
return_offset_days = (
    None
    if actual_return_date is None or not isinstance(request, ProviderExactRoundTripRequest)
    else _date_offset(actual_return_date, request.requested_return_date)
)
departure_offset_days = _date_offset(
    actual_departure_date,
    request.requested_departure_date,
)
```

Set offer fields:

```python
requested_departure_date=request.requested_departure_date,
actual_departure_date=actual_departure_date,
departure_offset_days=departure_offset_days,
requested_return_date=(
    request.requested_return_date
    if isinstance(request, ProviderExactRoundTripRequest)
    else None
),
actual_return_date=actual_return_date,
return_offset_days=return_offset_days,
flags=OfferFlagsV1(
    uses_flexible_departure_date=departure_offset_days != 0,
    uses_flexible_return_date=return_offset_days not in (None, 0),
),
```

Use a round-trip-capable offer ID:

```python
return_suffix = (
    f":{request.return_date}"
    if isinstance(request, ProviderExactRoundTripRequest)
    else ""
)
offer_id=f"{PROVIDER_NAME}:{request.origin}-{request.destination}:{request.departure_date}{return_suffix}:{item_index}"
```

- [ ] **Step 4: Run normalizer tests**

Run:

```bash
uv run pytest tests/test_google_fli_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cheapy/providers/google_fli/normalizer.py tests/test_google_fli_normalizer.py
git commit -m "feat: normalize round-trip google fli offers"
```

---

### Task 7: MCP Adapter And Agent Instructions

**Files:**
- Modify: `cheapy/mcp.py`
- Modify: `cheapy/agent_hooks.py`
- Modify: `.codex/skills/cheapy/SKILL.md`
- Test: `tests/test_mcp.py`
- Test: `tests/test_agent_hooks.py`
- Test: `tests/test_schema_export.py`

- [ ] **Step 1: Write failing instruction and MCP tests**

In `tests/test_mcp.py`, update the model import:

```python
from cheapy.models import SearchMode, SearchResponseV1, SearchStatus
```

In `tests/test_mcp.py`, update the fake search test to assert expanded/round-trip values pass through:

```python
def fake_search_exact(request: Any) -> SearchResponseV1:
    assert request.origin == "CXR"
    assert request.destination == "SGN"
    assert request.departure_date == "2026-07-10"
    assert request.return_date == "2026-07-15"
    assert request.search_mode == SearchMode.EXPANDED
    return SearchResponseV1.model_validate(
        {
            "schema_version": "1",
            "status": "success",
            "request_id": "search:round_trip:CXR:SGN:2026-07-10:2026-07-15:expanded:1:0:0:0:5",
            "offers": [],
            "warnings": [],
            "errors": [],
            "provider_statuses": [],
            "search_plan": {
                "search_mode": "expanded",
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
```

Use arguments:

```python
"return_date": "2026-07-15",
"search_mode": "expanded",
```

In `tests/test_agent_hooks.py`, replace the deferred assertions with:

```python
def _assert_gate_8_instruction_text(text: str) -> None:
    for phrase in (
        "clarify ambiguous airports",
        "origin, destination, and departure date",
        "Contract V1 passenger defaults",
        "exact one-way",
        "exact round-trip",
        "expanded flexible-date",
        "nearby-airport and split-ticket search is deferred",
        "Do not ask the user to choose providers",
        "Use each offer's `provider` field when explaining where a fare came from.",
        "mixed currency",
    ):
        assert phrase in text
    assert "round-trip search is deferred" not in text
    assert "do not pass return_date" not in text
```

Replace calls to `_assert_gate_6_instruction_text` with `_assert_gate_8_instruction_text`.

- [ ] **Step 2: Run failing MCP/instruction tests**

Run:

```bash
uv run pytest tests/test_mcp.py tests/test_agent_hooks.py tests/test_schema_export.py -v
```

Expected: instruction tests fail until managed text is updated. MCP/schema tests should pass or reveal exact expectation updates.

- [ ] **Step 3: Keep MCP on the compatibility search entrypoint**

Leave `cheapy/mcp.py` importing `search_exact` from `cheapy.search`. The
function name is historical, but it remains the compatibility entrypoint used
by existing MCP tests and callers. Update only the test arguments and
expectations from Step 1.

- [ ] **Step 4: Update managed instruction body**

In `cheapy/agent_hooks.py`, update `INSTRUCTION_BODY` to:

```python
INSTRUCTION_BODY = """Use Cheapy for one-way and round-trip MVP flight searches.

- Call only `search_cheapest_flights`.
- Pass `schema_version="1"`.
- Before calls, require origin, destination, and departure date; ask a follow-up if any are missing.
- Normalize clear origin and destination airports to 3-letter IATA codes.
- If airport meaning is unclear, clarify ambiguous airports instead of guessing.
- Normalize dates to ISO `YYYY-MM-DD`.
- Use Contract V1 passenger defaults when unspecified: `adults=1`, `children=0`, `infants_on_lap=0`, `infants_in_seat=0`.
- Ask a follow-up for ambiguous non-default passenger counts.
- Use `search_mode="exact"` for fixed exact one-way or exact round-trip searches.
- Use `return_date` for round-trip searches when the user asks for a return.
- Use `search_mode="expanded"` when the user asks for flexible dates around the requested date.
- nearby-airport and split-ticket search is deferred.
- Do not ask the user to choose providers.
- Use each offer's `provider` field when explaining where a fare came from.
- Choose the cheapest result from the returned `offers` list when currencies are comparable.
- Explain mixed currency cautiously; preserve provider currency and do not overstate comparisons.
"""
```

Update `.codex/skills/cheapy/SKILL.md` managed body to match by running the installer path in tests or by applying the same managed block content in the repo file.

- [ ] **Step 5: Run MCP/instruction/schema tests**

Run:

```bash
uv run pytest tests/test_mcp.py tests/test_agent_hooks.py tests/test_schema_export.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cheapy/mcp.py cheapy/agent_hooks.py .codex/skills/cheapy/SKILL.md tests/test_mcp.py tests/test_agent_hooks.py tests/test_schema_export.py
git commit -m "docs: update agent guidance for flexible search"
```

---

### Task 8: Full Search Integration And Existing Test Updates

**Files:**
- Modify: `tests/test_search.py`
- Modify: `tests/test_google_fli_provider.py`
- Modify: `tests/test_google_fli_normalizer.py`
- Modify: `cheapy/search.py`
- Modify: `cheapy/search_planner.py`
- Modify: `cheapy/providers/google_fli/provider.py`
- Modify: `cheapy/providers/google_fli/adapter.py`
- Modify: `cheapy/providers/google_fli/normalizer.py`

- [ ] **Step 1: Run all focused tests together**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_providers.py tests/test_search_planner.py tests/test_search.py tests/test_google_fli_provider.py tests/test_google_fli_normalizer.py tests/test_mcp.py tests/test_agent_hooks.py tests/test_schema_export.py -v
```

Expected: failures, if present, are limited to old exact-only expectations for
request ID strings, unsupported expanded search, unsupported round-trip search,
or provider capability names. Fix those exact expectations in Step 2.

- [ ] **Step 2: Update old exact-only expectations**

Make these exact updates where failures indicate old behavior:

- Request IDs should use:

```text
search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5
```

instead of:

```text
exact:CXR:SGN:2026-07-10:exact:1:0:0:0:5
```

- Unsupported expanded search tests should be removed or replaced with expanded behavior tests.
- Unsupported round-trip tests should be replaced with exact round-trip support and no-provider capability tests.
- Provider status capability assertions should expect `exact_one_way` for one-way and `exact_round_trip` for round-trip.

- [ ] **Step 3: Add a no-live-network regression for default tests**

Add this test to `tests/test_search.py` if there is not already equivalent coverage:

```python
def test_expanded_search_with_fake_provider_does_not_require_live_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingOneWayProvider()
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(_request(search_mode=SearchMode.EXPANDED))

    assert response.status == SearchStatus.SUCCESS
    assert len(provider.seen_requests) == 7
```

- [ ] **Step 4: Re-run focused tests**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_providers.py tests/test_search_planner.py tests/test_search.py tests/test_google_fli_provider.py tests/test_google_fli_normalizer.py tests/test_mcp.py tests/test_agent_hooks.py tests/test_schema_export.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_search.py tests/test_google_fli_provider.py tests/test_google_fli_normalizer.py cheapy/search.py cheapy/search_planner.py cheapy/providers/google_fli/provider.py cheapy/providers/google_fli/adapter.py cheapy/providers/google_fli/normalizer.py
git commit -m "test: cover gate 8 flexible search integration"
```

---

### Task 9: Optional Live Smoke Coverage

**Files:**
- Modify: `tests/test_live_google_fli.py`

- [ ] **Step 1: Inspect existing live smoke gate**

Run:

```bash
sed -n '1,260p' tests/test_live_google_fli.py
```

Expected: Find the current opt-in marker/environment variable used to prevent live tests from running by default.

- [ ] **Step 2: Add opt-in exact round-trip smoke only behind the existing gate**

Add a test that follows the existing skip condition and uses a conservative route/date pair:

```python
def test_live_google_fli_exact_round_trip_smoke() -> None:
    provider = GoogleFliProvider(timeout_seconds=30)
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-15",
    )

    result = asyncio.run(provider.search_exact_round_trip(request))

    assert result.provider_name == "google_fli"
    assert result.capability == "exact_round_trip"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    assert all(offer.provider == "google_fli" for offer in result.offers)
```

Do not add an expanded live smoke in Gate 8 unless exact round-trip live smoke is stable locally. Expanded live smoke can issue up to 10 provider calls and should remain a separate future live lane.

- [ ] **Step 3: Run default live test file without opt-in**

Run:

```bash
uv run pytest tests/test_live_google_fli.py -v
```

Expected: live tests are skipped unless the existing opt-in flag is set.

- [ ] **Step 4: Commit**

```bash
git add tests/test_live_google_fli.py
git commit -m "test: add opt-in round-trip live smoke"
```

---

### Task 10: Final Verification

**Files:**
- Verify all modified files

- [ ] **Step 1: Run package sync**

Run:

```bash
uv sync --extra dev
```

Expected: dependencies are installed or already current.

- [ ] **Step 2: Run focused suites**

Run:

```bash
uv run pytest tests/test_search.py -v
uv run pytest tests/test_google_fli_provider.py tests/test_google_fli_normalizer.py -v
uv run pytest tests/test_mcp.py tests/test_agent_hooks.py tests/test_schema_export.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS, with live tests skipped unless explicitly opted in.

- [ ] **Step 4: Run package entrypoint smoke**

Run:

```bash
uv run cheapy --version
```

Expected: command exits successfully and prints the package version.

- [ ] **Step 5: Inspect git diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: only Gate 8 implementation/test/docs changes remain uncommitted. No unrelated user edits are reverted.

- [ ] **Step 6: Commit final verification fixups**

If Steps 1-5 changed files, commit them:

```bash
git add cheapy tests .codex/skills/cheapy/SKILL.md
git commit -m "fix: stabilize gate 8 flexible search"
```

Expected: branch history contains focused commits for contracts, planner, search orchestration, provider support, agent guidance, tests, and any final fixups.

---

## Plan Self-Review

- Spec coverage:
  - Fixed plus/minus 3-day flexible window: Task 2.
  - True round-trip provider search: Tasks 1, 3, 5, 6.
  - Gate 8 10-call budget and truncation: Tasks 2 and 4.
  - No synthetic round-trip pairing: Tasks 3 and 4 tests.
  - Request ID trip-shape/return-date collision fix: Task 3 and Task 4.
  - Dedup before sort/max/rank: Task 4.
  - Agent instructions: Task 7.
  - Offline deterministic tests and opt-in live smoke: Tasks 8 and 9.
- Placeholder scan: no `TBD`, `TODO`, or unspecified "add tests" steps are present.
- Type consistency:
  - `ProviderExactRoundTripRequest` is introduced before use.
  - Planner constants match provider capability strings.
  - `SearchPlanV1` accounting fields match Contract V1.
  - Warning codes use existing `WarningCode` enum values.
