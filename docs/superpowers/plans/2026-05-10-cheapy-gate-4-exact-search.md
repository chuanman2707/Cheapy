# Cheapy Gate 4 Exact Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Gate 4: an internal exact one-way search API that converts `SearchRequestV1` into deterministic `SearchResponseV1` results through `manual_fixture`.

**Architecture:** Add one focused orchestrator module, `cheapy/search.py`, that resolves IATA airports, filters exact-capable providers, calls `search_exact_one_way`, and assembles Contract V1 responses. Keep CLI, MCP, storage, expanded search, retries, timeouts, and live providers out of scope.

**Tech Stack:** Python 3.12+, Pydantic v2, stdlib `asyncio`, uv, pytest.

---

## Current Baseline

Run this before starting:

```bash
uv run pytest -q
git status --short
```

Expected test baseline:

```text
69 passed
```

The working tree can contain unrelated untracked project docs. Do not stage or edit them unless this plan explicitly names them.

Before editing tests, read the project-local testing skill:

```bash
sed -n '1,220p' .codex/skills/python-testing-patterns/SKILL.md
```

Gate 4 must not add:

- `cheapy search`
- MCP server behavior
- live provider calls
- storage
- expanded search
- round-trip search
- flexible-date, nearby-airport, or split-ticket planners

## File Structure

Create:

- `cheapy/search.py`: internal exact search orchestration and private response-builder helpers.
- `tests/test_search.py`: focused tests for success, runtime failures, provider edge cases, request IDs, search-plan accounting, and currency grouping.

Do not modify:

- `cheapy/cli.py`
- `cheapy/__main__.py`
- `cheapy/models/contracts.py`
- `cheapy/providers/manual_fixture/provider.py`
- `cheapy/providers/registry.py`

---

### Task 1: Add Search API Tests

**Files:**

- Create: `tests/test_search.py`

- [ ] **Step 1: Write the failing search tests**

Create `tests/test_search.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from cheapy.models import (
    CandidateFamily,
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    ProviderStatusCode,
    SearchMode,
    SearchRequestV1,
    SearchStatus,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers.registry import ProviderLoadError, ProviderManifestError
from cheapy.search import search_exact


def _request(**overrides: Any) -> SearchRequestV1:
    data: dict[str, Any] = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
    }
    data.update(overrides)
    return SearchRequestV1(**data)


def _offer(
    *,
    offer_id: str,
    provider: str,
    currency: str,
    price_amount: float,
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
                departure_time=departure_time,
                arrival_time=arrival_time,
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=70,
            )
        ],
        total_duration_minutes=70,
        stops=0,
        flags=OfferFlagsV1(),
        fare_details_status="not_collected",
    )


class _ProviderFromResult:
    capabilities = ("exact_one_way",)

    def __init__(self, result: ProviderResult) -> None:
        self.name = result.provider_name
        self._result = result

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        assert request.origin == "CXR"
        assert request.destination == "SGN"
        assert request.departure_date == "2026-07-10"
        return self._result


def test_search_exact_returns_manual_fixture_success_response() -> None:
    response = search_exact(_request())

    assert response.schema_version == "1"
    assert response.status == SearchStatus.SUCCESS
    assert response.request_id == "exact:CXR:SGN:2026-07-10:exact:1:0:0:0:5"
    assert response.errors == []
    assert response.warnings == []
    assert [offer.offer_id for offer in response.offers] == [
        "manual_fixture:cxr-sgn-20260710-1",
        "manual_fixture:cxr-sgn-20260710-2",
    ]
    assert [offer.price_amount for offer in response.offers] == [1280000.0, 1490000.0]
    assert response.mixed_currency is False
    assert [(group.currency, group.offer_ids) for group in response.currency_groups] == [
        (
            "VND",
            [
                "manual_fixture:cxr-sgn-20260710-1",
                "manual_fixture:cxr-sgn-20260710-2",
            ],
        )
    ]
    assert response.currency_notes == []
    assert response.candidates is None

    assert response.search_plan.search_mode == SearchMode.EXACT
    assert response.search_plan.planned_candidate_count == 1
    assert response.search_plan.executed_candidate_count == 1
    assert response.search_plan.planned_provider_call_count == 1
    assert response.search_plan.executed_provider_call_count == 1
    assert response.search_plan.candidate_count_by_family == {CandidateFamily.EXACT: 1}
    assert response.search_plan.provider_call_count_by_family == {
        CandidateFamily.EXACT: 1
    }
    assert response.search_plan.truncated is False
    assert response.search_plan.truncated_families == []
    assert response.search_plan.candidate_families == [CandidateFamily.EXACT]

    assert len(response.provider_statuses) == 1
    provider_status = response.provider_statuses[0]
    assert provider_status.provider_name == "manual_fixture"
    assert provider_status.capability == "exact_one_way"
    assert provider_status.status == ProviderStatusCode.SUCCESS
    assert provider_status.planned_call_count == 1
    assert provider_status.executed_call_count == 1
    assert provider_status.succeeded_call_count == 1
    assert provider_status.failed_call_count == 0


def test_search_exact_respects_max_results_and_uses_resolved_iata() -> None:
    response = search_exact(
        _request(
            origin=" cxr ",
            destination="sgn",
            max_results=1,
        )
    )

    assert response.status == SearchStatus.SUCCESS
    assert response.request_id == "exact:CXR:SGN:2026-07-10:exact:1:0:0:0:1"
    assert [offer.offer_id for offer in response.offers] == [
        "manual_fixture:cxr-sgn-20260710-1"
    ]
    assert [(group.currency, group.offer_ids) for group in response.currency_groups] == [
        ("VND", ["manual_fixture:cxr-sgn-20260710-1"])
    ]


def test_search_exact_preserves_provider_failure_for_unsupported_fixture() -> None:
    response = search_exact(_request(origin="HAN"))

    assert response.status == SearchStatus.FAILED
    assert response.offers == []
    assert len(response.errors) == 1
    assert response.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert response.errors[0].message_en == "No manual fixture exists for the requested route/date."
    assert response.provider_statuses[0].provider_name == "manual_fixture"
    assert response.provider_statuses[0].status == ProviderStatusCode.FAILED
    assert response.search_plan.executed_provider_call_count == 1


def test_search_exact_unknown_airport_returns_failed_response_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called() -> list[object]:
        raise AssertionError("airport failures must not load providers")

    monkeypatch.setattr("cheapy.search.load_enabled_providers", fail_if_called)

    response = search_exact(_request(origin="ZZZ"))

    assert response.status == SearchStatus.FAILED
    assert response.request_id == "exact:ZZZ:SGN:2026-07-10:exact:1:0:0:0:5"
    assert response.offers == []
    assert response.provider_statuses == []
    assert len(response.errors) == 1
    assert response.errors[0].code == ErrorCode.AIRPORT_NOT_FOUND
    assert response.errors[0].details == {"field": "origin", "value": "ZZZ"}
    assert response.search_plan.planned_candidate_count == 0
    assert response.search_plan.executed_provider_call_count == 0


@pytest.mark.parametrize(
    ("overrides", "unsupported_reason"),
    [
        (
            {"search_mode": SearchMode.EXPANDED},
            "Gate 4 does not support expanded search.",
        ),
        (
            {"return_date": "2026-07-15"},
            "Gate 4 does not support round-trip search.",
        ),
    ],
)
def test_search_exact_unsupported_scope_returns_failed_response(
    overrides: dict[str, Any],
    unsupported_reason: str,
) -> None:
    response = search_exact(_request(**overrides))

    assert response.status == SearchStatus.FAILED
    assert response.offers == []
    assert response.provider_statuses == []
    assert response.errors[0].code == ErrorCode.NO_PROVIDER_AVAILABLE
    assert response.errors[0].details["unsupported_reason"] == unsupported_reason
    assert response.search_plan.planned_candidate_count == 0
    assert response.search_plan.executed_candidate_count == 0


def test_search_exact_no_enabled_providers_reports_planned_unexecuted_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cheapy.search.load_enabled_providers", lambda: [])

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert response.errors[0].code == ErrorCode.NO_PROVIDER_AVAILABLE
    assert response.errors[0].details["reason"] == "no_enabled_provider"
    assert response.search_plan.planned_candidate_count == 1
    assert response.search_plan.executed_candidate_count == 0
    assert response.search_plan.planned_provider_call_count == 0
    assert response.search_plan.executed_provider_call_count == 0
    assert response.search_plan.candidate_count_by_family == {CandidateFamily.EXACT: 1}
    assert response.search_plan.provider_call_count_by_family == {
        CandidateFamily.EXACT: 0
    }
    assert response.search_plan.candidate_families == [CandidateFamily.EXACT]


def test_search_exact_no_exact_capable_provider_returns_no_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlexibleOnlyProvider:
        name = "flexible_only"
        capabilities = ("flexible_dates",)

    monkeypatch.setattr(
        "cheapy.search.load_enabled_providers",
        lambda: [FlexibleOnlyProvider()],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert response.errors[0].code == ErrorCode.NO_PROVIDER_AVAILABLE
    assert response.errors[0].details["reason"] == "no_exact_one_way_provider"
    assert response.search_plan.planned_candidate_count == 1
    assert response.search_plan.executed_candidate_count == 0


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (ProviderManifestError("bad manifest"), "ProviderManifestError"),
        (ProviderLoadError("bad provider"), "ProviderLoadError"),
    ],
)
def test_search_exact_registry_errors_return_failed_response(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    error_type: str,
) -> None:
    def raise_registry_error() -> list[object]:
        raise error

    monkeypatch.setattr("cheapy.search.load_enabled_providers", raise_registry_error)

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert response.errors[0].code == ErrorCode.NO_PROVIDER_AVAILABLE
    assert response.errors[0].details == {"registry_error_type": error_type}
    assert response.search_plan.planned_candidate_count == 1
    assert response.search_plan.executed_candidate_count == 0


def test_search_exact_provider_exception_becomes_provider_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingProvider:
        name = "raising_provider"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            raise RuntimeError("secret token must not leak")

    monkeypatch.setattr(
        "cheapy.search.load_enabled_providers",
        lambda: [RaisingProvider()],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert response.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert response.errors[0].details == {
        "provider": "raising_provider",
        "capability": "exact_one_way",
        "exception_type": "RuntimeError",
    }
    assert "secret token" not in response.model_dump_json()
    assert response.provider_statuses[0].status == ProviderStatusCode.FAILED


def test_search_exact_returns_partial_when_offers_and_provider_errors_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = ProviderResult(
        provider_name="success_provider",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="success:offer-1",
                provider="success_provider",
                currency="USD",
                price_amount=100.0,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=3,
        retryable=False,
    )
    failure_error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Provider fixture failed.",
        details={"provider": "failing_provider"},
        retryable=False,
    )
    failure = ProviderResult(
        provider_name="failing_provider",
        capability="exact_one_way",
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[failure_error],
        duration_ms=4,
        retryable=False,
    )

    monkeypatch.setattr(
        "cheapy.search.load_enabled_providers",
        lambda: [_ProviderFromResult(success), _ProviderFromResult(failure)],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.PARTIAL
    assert [offer.offer_id for offer in response.offers] == ["success:offer-1"]
    assert response.errors == [failure_error]
    assert [status.status for status in response.provider_statuses] == [
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.FAILED,
    ]
    assert response.search_plan.planned_provider_call_count == 2
    assert response.search_plan.executed_provider_call_count == 2


def test_search_exact_groups_mixed_currency_offers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ProviderResult(
        provider_name="mixed_provider",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="mixed:vnd",
                provider="mixed_provider",
                currency="VND",
                price_amount=1500000.0,
            ),
            _offer(
                offer_id="mixed:usd",
                provider="mixed_provider",
                currency="USD",
                price_amount=50.0,
            ),
        ],
        warnings=[],
        errors=[],
        duration_ms=5,
        retryable=False,
    )
    monkeypatch.setattr(
        "cheapy.search.load_enabled_providers",
        lambda: [_ProviderFromResult(result)],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.SUCCESS
    assert response.mixed_currency is True
    assert [offer.offer_id for offer in response.offers] == ["mixed:usd", "mixed:vnd"]
    assert [(group.currency, group.offer_ids) for group in response.currency_groups] == [
        ("USD", ["mixed:usd"]),
        ("VND", ["mixed:vnd"]),
    ]
    assert response.currency_notes == [
        "Currency conversion was not applied; compare mixed-currency offers separately."
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: FAIL during collection with:

```text
ModuleNotFoundError: No module named 'cheapy.search'
```

---

### Task 2: Implement Internal Exact Search Orchestrator

**Files:**

- Create: `cheapy/search.py`
- Test: `tests/test_search.py`

- [ ] **Step 1: Add `cheapy/search.py`**

Create `cheapy/search.py`:

```python
"""Internal exact search orchestration."""

from __future__ import annotations

import asyncio

from cheapy.airports import AirportNotFound, resolve_airport
from cheapy.models import (
    CandidateFamily,
    CurrencyGroupV1,
    ErrorCode,
    ErrorV1,
    FlightOfferV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
    Severity,
)
from cheapy.providers.base import FlightProvider, ProviderExactOneWayRequest, ProviderResult
from cheapy.providers.registry import (
    ProviderLoadError,
    ProviderManifestError,
    load_enabled_providers,
)


_EXACT_CAPABILITY = "exact_one_way"
_MIXED_CURRENCY_NOTE = (
    "Currency conversion was not applied; compare mixed-currency offers separately."
)


def search_exact(request: SearchRequestV1) -> SearchResponseV1:
    """Run Gate 4 exact one-way search and return a Contract V1 response."""
    fallback_origin = _normalize_airport_value(request.origin)
    fallback_destination = _normalize_airport_value(request.destination)
    fallback_request_id = _request_id(request, fallback_origin, fallback_destination)

    try:
        origin = resolve_airport(request.origin)
    except AirportNotFound:
        return _failed_response(
            request=request,
            request_id=fallback_request_id,
            errors=[_airport_not_found_error("origin", fallback_origin)],
            search_plan=_empty_plan(request.search_mode),
        )

    try:
        destination = resolve_airport(request.destination)
    except AirportNotFound:
        return _failed_response(
            request=request,
            request_id=fallback_request_id,
            errors=[_airport_not_found_error("destination", fallback_destination)],
            search_plan=_empty_plan(request.search_mode),
        )

    request_id = _request_id(request, origin.iata, destination.iata)

    unsupported_reason = _unsupported_reason(request)
    if unsupported_reason is not None:
        return _failed_response(
            request=request,
            request_id=request_id,
            errors=[
                _error(
                    code=ErrorCode.NO_PROVIDER_AVAILABLE,
                    message_en="No provider is available for the requested Gate 4 search scope.",
                    details={"unsupported_reason": unsupported_reason},
                )
            ],
            search_plan=_empty_plan(request.search_mode),
        )

    try:
        providers = load_enabled_providers()
    except (ProviderManifestError, ProviderLoadError) as exc:
        return _failed_response(
            request=request,
            request_id=request_id,
            errors=[
                _error(
                    code=ErrorCode.NO_PROVIDER_AVAILABLE,
                    message_en="No enabled provider could be loaded.",
                    details={"registry_error_type": type(exc).__name__},
                )
            ],
            search_plan=_planned_unexecuted_exact_plan(request.search_mode),
        )

    if not providers:
        return _failed_response(
            request=request,
            request_id=request_id,
            errors=[
                _error(
                    code=ErrorCode.NO_PROVIDER_AVAILABLE,
                    message_en="No enabled provider is available for exact one-way search.",
                    details={"reason": "no_enabled_provider"},
                )
            ],
            search_plan=_planned_unexecuted_exact_plan(request.search_mode),
        )

    exact_providers = [
        provider for provider in providers if _EXACT_CAPABILITY in provider.capabilities
    ]
    if not exact_providers:
        return _failed_response(
            request=request,
            request_id=request_id,
            errors=[
                _error(
                    code=ErrorCode.NO_PROVIDER_AVAILABLE,
                    message_en="No enabled provider supports exact one-way search.",
                    details={"reason": "no_exact_one_way_provider"},
                )
            ],
            search_plan=_planned_unexecuted_exact_plan(request.search_mode),
        )

    provider_request = ProviderExactOneWayRequest(
        origin=origin.iata,
        destination=destination.iata,
        departure_date=request.departure_date,
        passengers=request.passengers,
    )
    provider_results = asyncio.run(_call_providers(exact_providers, provider_request))

    return _response_from_provider_results(
        request=request,
        request_id=request_id,
        provider_results=provider_results,
    )


def _normalize_airport_value(value: str) -> str:
    return value.strip().upper()


def _request_id(request: SearchRequestV1, origin: str, destination: str) -> str:
    passengers = request.passengers
    return (
        f"exact:{origin}:{destination}:{request.departure_date}:"
        f"{request.search_mode.value}:{passengers.adults}:{passengers.children}:"
        f"{passengers.infants_on_lap}:{passengers.infants_in_seat}:"
        f"{request.max_results}"
    )


def _unsupported_reason(request: SearchRequestV1) -> str | None:
    if request.search_mode != SearchMode.EXACT:
        return "Gate 4 does not support expanded search."
    if request.return_date is not None:
        return "Gate 4 does not support round-trip search."
    return None


async def _call_providers(
    providers: list[FlightProvider],
    request: ProviderExactOneWayRequest,
) -> list[ProviderResult]:
    results: list[ProviderResult] = []
    for provider in providers:
        try:
            results.append(await provider.search_exact_one_way(request))
        except Exception as exc:
            results.append(_provider_exception_result(provider, exc))
    return results


def _provider_exception_result(
    provider: FlightProvider,
    exc: Exception,
) -> ProviderResult:
    return ProviderResult(
        provider_name=provider.name,
        capability=_EXACT_CAPABILITY,
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[
            _error(
                code=ErrorCode.PROVIDER_FAILED,
                message_en="Provider raised an unexpected exception.",
                details={
                    "provider": provider.name,
                    "capability": _EXACT_CAPABILITY,
                    "exception_type": type(exc).__name__,
                },
            )
        ],
        duration_ms=0,
        retryable=False,
    )


def _response_from_provider_results(
    *,
    request: SearchRequestV1,
    request_id: str,
    provider_results: list[ProviderResult],
) -> SearchResponseV1:
    offers = [
        offer
        for result in provider_results
        for offer in result.offers
    ]
    returned_offers = _sort_offers(offers)[: request.max_results]
    warnings = [
        warning
        for result in provider_results
        for warning in result.warnings
    ]
    errors = [
        error
        for result in provider_results
        for error in result.errors
    ]
    mixed_currency = len({offer.currency for offer in returned_offers}) > 1

    return SearchResponseV1(
        schema_version="1",
        status=_response_status(returned_offers, errors),
        request_id=request_id,
        offers=returned_offers,
        warnings=warnings,
        errors=errors,
        provider_statuses=[
            _provider_status_from_result(result)
            for result in provider_results
        ],
        search_plan=_executed_exact_plan(
            request.search_mode,
            provider_call_count=len(provider_results),
        ),
        mixed_currency=mixed_currency,
        currency_groups=_currency_groups(returned_offers),
        currency_notes=[_MIXED_CURRENCY_NOTE] if mixed_currency else [],
        candidates=None,
    )


def _failed_response(
    *,
    request: SearchRequestV1,
    request_id: str,
    errors: list[ErrorV1],
    search_plan: SearchPlanV1,
) -> SearchResponseV1:
    return SearchResponseV1(
        schema_version="1",
        status=SearchStatus.FAILED,
        request_id=request_id,
        offers=[],
        warnings=[],
        errors=errors,
        provider_statuses=[],
        search_plan=search_plan,
        mixed_currency=False,
        currency_groups=[],
        currency_notes=[],
        candidates=None,
    )


def _provider_status_from_result(result: ProviderResult) -> ProviderStatusV1:
    succeeded = 1 if result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
    } else 0
    failed = 1 if result.status == ProviderStatusCode.FAILED else 0

    return ProviderStatusV1(
        provider_name=result.provider_name,
        capability=result.capability,
        status=result.status,
        planned_call_count=1,
        executed_call_count=1,
        succeeded_call_count=succeeded,
        failed_call_count=failed,
        duration_ms=result.duration_ms,
        warnings=result.warnings,
        errors=result.errors,
        retryable=result.retryable,
    )


def _response_status(
    offers: list[FlightOfferV1],
    errors: list[ErrorV1],
) -> SearchStatus:
    if offers and errors:
        return SearchStatus.PARTIAL
    if offers:
        return SearchStatus.SUCCESS
    return SearchStatus.FAILED


def _sort_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers}
    if len(currencies) <= 1:
        return sorted(offers, key=lambda offer: (offer.price_amount, offer.offer_id))
    return sorted(
        offers,
        key=lambda offer: (offer.currency, offer.price_amount, offer.offer_id),
    )


def _currency_groups(offers: list[FlightOfferV1]) -> list[CurrencyGroupV1]:
    return [
        CurrencyGroupV1(
            currency=currency,
            offer_ids=[
                offer.offer_id
                for offer in offers
                if offer.currency == currency
            ],
        )
        for currency in sorted({offer.currency for offer in offers})
    ]


def _airport_not_found_error(field: str, value: str) -> ErrorV1:
    return _error(
        code=ErrorCode.AIRPORT_NOT_FOUND,
        message_en="Airport was not found in the packaged airport catalog.",
        details={"field": field, "value": value},
    )


def _error(
    *,
    code: ErrorCode,
    message_en: str,
    details: dict[str, object],
) -> ErrorV1:
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=False,
    )


def _empty_plan(search_mode: SearchMode) -> SearchPlanV1:
    return SearchPlanV1(
        search_mode=search_mode,
        planned_candidate_count=0,
        executed_candidate_count=0,
        planned_provider_call_count=0,
        executed_provider_call_count=0,
        candidate_count_by_family={},
        provider_call_count_by_family={},
        truncated=False,
        truncated_families=[],
        candidate_families=[],
    )


def _planned_unexecuted_exact_plan(search_mode: SearchMode) -> SearchPlanV1:
    return SearchPlanV1(
        search_mode=search_mode,
        planned_candidate_count=1,
        executed_candidate_count=0,
        planned_provider_call_count=0,
        executed_provider_call_count=0,
        candidate_count_by_family={CandidateFamily.EXACT: 1},
        provider_call_count_by_family={CandidateFamily.EXACT: 0},
        truncated=False,
        truncated_families=[],
        candidate_families=[CandidateFamily.EXACT],
    )


def _executed_exact_plan(
    search_mode: SearchMode,
    *,
    provider_call_count: int,
) -> SearchPlanV1:
    return SearchPlanV1(
        search_mode=search_mode,
        planned_candidate_count=1,
        executed_candidate_count=1,
        planned_provider_call_count=provider_call_count,
        executed_provider_call_count=provider_call_count,
        candidate_count_by_family={CandidateFamily.EXACT: 1},
        provider_call_count_by_family={CandidateFamily.EXACT: provider_call_count},
        truncated=False,
        truncated_families=[],
        candidate_families=[CandidateFamily.EXACT],
    )
```

- [ ] **Step 2: Run search tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS with all `tests/test_search.py` tests passing.

- [ ] **Step 3: Run related existing tests**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_providers.py tests/test_airports.py -v
```

Expected: PASS. These suites guard the model contract, provider-local behavior, and airport resolver behavior that the new orchestrator depends on.

- [ ] **Step 4: Commit search API and tests**

Run:

```bash
git status --short
git add cheapy/search.py tests/test_search.py
git commit -m "feat: add internal exact search orchestration" -m "AI-Model: GPT-5 Codex"
```

Expected: commit succeeds and stages only `cheapy/search.py` and `tests/test_search.py`.

---

### Task 3: Full Verification

**Files:**

- Verify: full repository test suite
- Verify: manual provider CLI remains unchanged

- [ ] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 2: Verify provider CLI still works**

Run:

```bash
uv run cheapy providers list
uv run cheapy providers test
```

Expected:

- `providers list` exits `0` and prints JSON containing `manual_fixture`.
- `providers test` exits `0` and prints JSON with `"providers_tested": 1`.

- [ ] **Step 3: Verify no search CLI was added**

Run:

```bash
uv run cheapy search
```

Expected: exit `2`, stdout empty, stderr JSON with:

```json
{
  "error": true,
  "code": "USAGE_ERROR",
  "suggestion": "Run 'cheapy --help' for valid usage."
}
```

Check that the `message` field contains `No such command`.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
git diff HEAD -- cheapy/search.py tests/test_search.py
```

Expected:

- no unstaged changes to files outside `cheapy/search.py` and `tests/test_search.py`
- no edits to CLI, MCP, contracts, provider fixture, provider registry, or package metadata

---

## Self-Review Checklist

After completing implementation, verify the final code against the spec:

- `search_exact(SearchRequestV1(...))` returns `SearchResponseV1`.
- Fixture request `CXR` to `SGN` on `2026-07-10` returns the two manual fixture offers.
- Runtime failures return `SearchResponseV1(status="failed")`.
- Provider failures and warnings are preserved.
- Exact search-plan accounting is deterministic.
- Currency groups are deterministic and include one group per returned currency.
- Provider requests use resolved IATA values.
- Request IDs use resolved IATA values when resolution succeeds.
- Request IDs use normalized raw airport values when resolution fails.
- No CLI search command is added.
- MCP remains outside scope.
- No network calls are introduced.
- `uv run pytest -v` passes.
