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
