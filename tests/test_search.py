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
    WarningCode,
)
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.manual_fixture.provider import create_provider as create_manual_fixture
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

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        raise AssertionError("one-way provider must not receive round-trip calls")


class _RoundTripProviderFromResult:
    capabilities = ("exact_round_trip",)

    def __init__(self, result: ProviderResult) -> None:
        self.name = result.provider_name
        self._result = result

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        raise AssertionError("round-trip provider must not receive one-way calls")

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        assert request.origin == "CXR"
        assert request.destination == "SGN"
        assert request.departure_date == "2026-07-10"
        assert request.return_date == "2026-07-17"
        return self._result


class _FailingTravelokaProvider:
    name = "traveloka"
    capabilities = ("exact_one_way", "exact_round_trip")

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
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    severity=Severity.ERROR,
                    message_en="Traveloka provider timed out.",
                    details={
                        "provider": "traveloka",
                        "capability": "exact_one_way",
                        "failure_type": "timeout",
                    },
                    retryable=True,
                )
            ],
            duration_ms=20_000,
            retryable=True,
        )

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        raise AssertionError("round-trip should not be called")


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

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        raise AssertionError("one-way provider must not receive round-trip calls")


class _RoundTripProvider:
    name = "round_provider"
    capabilities = ("exact_round_trip",)

    def __init__(self) -> None:
        self.seen_requests: list[ProviderExactRoundTripRequest] = []

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        raise AssertionError("round-trip provider must not receive one-way calls")

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


def _manual_fixture_providers() -> list[object]:
    return [create_manual_fixture()]


def test_search_exact_returns_manual_fixture_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        _manual_fixture_providers,
    )

    response = search_exact(_request())

    assert response.schema_version == "1"
    assert response.status == SearchStatus.SUCCESS
    assert response.request_id == (
        "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5"
    )
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


def test_search_exact_round_trip_routes_to_round_trip_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RoundTripProvider()
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(_request(return_date="2026-07-15"))

    assert response.status == SearchStatus.SUCCESS
    assert response.request_id == (
        "search:round_trip:CXR:SGN:2026-07-10:2026-07-15:exact:1:0:0:0:5"
    )
    assert len(provider.seen_requests) == 1
    assert provider.seen_requests[0].return_date == "2026-07-15"
    assert all(
        warning.code != WarningCode.SEARCH_TRUNCATED
        for warning in response.warnings
    )
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
    assert any(
        warning.code == WarningCode.FLEXIBLE_DATE_USED
        for warning in response.warnings
    )


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


def test_search_exact_respects_max_results_and_uses_resolved_iata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        _manual_fixture_providers,
    )

    response = search_exact(
        _request(
            origin=" cxr ",
            destination="sgn",
            max_results=1,
        )
    )

    assert response.status == SearchStatus.SUCCESS
    assert response.request_id == (
        "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:1"
    )
    assert [offer.offer_id for offer in response.offers] == [
        "manual_fixture:cxr-sgn-20260710-1"
    ]
    assert [(group.currency, group.offer_ids) for group in response.currency_groups] == [
        ("VND", ["manual_fixture:cxr-sgn-20260710-1"])
    ]


def test_search_exact_preserves_provider_failure_for_unsupported_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        _manual_fixture_providers,
    )

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

    monkeypatch.setattr("cheapy.search.load_search_providers", fail_if_called)

    response = search_exact(_request(origin="ZZZ"))

    assert response.status == SearchStatus.FAILED
    assert response.request_id == (
        "search:one_way:ZZZ:SGN:2026-07-10:none:exact:1:0:0:0:5"
    )
    assert response.offers == []
    assert response.provider_statuses == []
    assert len(response.errors) == 1
    assert response.errors[0].code == ErrorCode.AIRPORT_NOT_FOUND
    assert response.errors[0].details == {"field": "origin", "value": "ZZZ"}
    assert response.search_plan.planned_candidate_count == 0
    assert response.search_plan.executed_provider_call_count == 0


def test_search_exact_no_enabled_providers_reports_planned_unexecuted_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [])

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert response.errors[0].code == ErrorCode.NO_PROVIDER_AVAILABLE
    assert response.errors[0].details["reason"] == "no_exact_one_way_provider"
    assert response.search_plan.planned_candidate_count == 1
    assert response.search_plan.executed_candidate_count == 0
    assert response.search_plan.planned_provider_call_count == 0
    assert response.search_plan.executed_provider_call_count == 0
    assert response.search_plan.candidate_count_by_family == {CandidateFamily.EXACT: 1}
    assert response.search_plan.provider_call_count_by_family == {}
    assert response.search_plan.candidate_families == [CandidateFamily.EXACT]


def test_search_exact_no_exact_capable_provider_returns_no_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlexibleOnlyProvider:
        name = "flexible_only"
        capabilities = ("flexible_dates",)

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
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

    monkeypatch.setattr("cheapy.search.load_search_providers", raise_registry_error)

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
        "cheapy.search.load_search_providers",
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


def test_search_exact_malformed_provider_return_becomes_provider_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedProvider:
        name = "malformed_provider"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> dict[str, str]:
            return {"raw_provider_payload": "secret payload must not leak"}

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [MalformedProvider()],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert response.offers == []
    assert response.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert response.errors[0].details == {
        "provider": "malformed_provider",
        "capability": "exact_one_way",
        "exception_type": "ValidationError",
    }
    assert response.provider_statuses[0].status == ProviderStatusCode.FAILED
    assert response.provider_statuses[0].errors == response.errors
    assert "secret payload" not in response.model_dump_json()
    assert "raw_provider_payload" not in response.model_dump_json()


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
        "cheapy.search.load_search_providers",
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


def test_search_returns_other_provider_offers_when_traveloka_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    google_result = ProviderResult(
        provider_name="google_fli",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="google:1",
                provider="google_fli",
                currency="USD",
                price_amount=120.0,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=5,
        retryable=False,
    )
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(google_result), _FailingTravelokaProvider()],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.PARTIAL
    assert [offer.provider for offer in response.offers] == ["google_fli"]
    statuses = {status.provider_name: status for status in response.provider_statuses}
    assert statuses["google_fli"].status == ProviderStatusCode.SUCCESS
    assert statuses["traveloka"].status == ProviderStatusCode.FAILED
    assert statuses["traveloka"].errors[0].code == ErrorCode.PROVIDER_TIMEOUT


def test_search_exact_synthesizes_error_for_failed_provider_without_errors(
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
    silent_failure = ProviderResult(
        provider_name="silent_failure",
        capability="exact_one_way",
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[],
        duration_ms=4,
        retryable=False,
    )

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(success), _ProviderFromResult(silent_failure)],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.PARTIAL
    assert [offer.offer_id for offer in response.offers] == ["success:offer-1"]
    assert len(response.errors) == 1
    assert response.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert response.errors[0].details == {
        "provider": "silent_failure",
        "capability": "exact_one_way",
        "provider_status": "failed",
    }
    assert response.provider_statuses[1].errors == response.errors


def test_search_exact_normalizes_provider_status_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ProviderResult(
        provider_name="wrong_capability_provider",
        capability="wrong_capability",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="wrong-capability:offer-1",
                provider="wrong_capability_provider",
                currency="USD",
                price_amount=100.0,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=3,
        retryable=False,
    )

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(result)],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.SUCCESS
    assert response.provider_statuses[0].capability == "exact_one_way"


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
        "cheapy.search.load_search_providers",
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


def test_search_exact_keeps_non_comparable_offers_out_of_global_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_comparable_offer = _offer(
        offer_id="traveloka:partial",
        provider="traveloka",
        currency="USD",
        price_amount=50.0,
    ).model_copy(
        update={
            "comparable": False,
            "rank_within_currency": None,
            "global_rank": None,
        }
    )
    comparable_offer = _offer(
        offer_id="google_fli:complete",
        provider="google_fli",
        currency="USD",
        price_amount=200.0,
    )
    result = ProviderResult(
        provider_name="traveloka",
        capability="exact_one_way",
        status=ProviderStatusCode.PARTIAL,
        offers=[non_comparable_offer, comparable_offer],
        warnings=[],
        errors=[],
        duration_ms=1,
        retryable=False,
    )

    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(result)],
    )

    response = search_exact(_request(max_results=2))

    assert [offer.offer_id for offer in response.offers] == [
        "google_fli:complete",
        "traveloka:partial",
    ]
    assert response.offers[0].comparable is True
    assert response.offers[0].rank_within_currency == 1
    assert response.offers[0].global_rank == 1
    assert response.offers[1].comparable is False
    assert response.offers[1].rank_within_currency is None
    assert response.offers[1].global_rank is None


def test_search_exact_round_trip_ranks_selected_traveloka_and_keeps_partial_unranked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_traveloka = _offer(
        offer_id="traveloka:selected",
        provider="traveloka",
        currency="USD",
        price_amount=150.0,
        requested_return_date="2026-07-17",
        actual_return_date="2026-07-17",
        return_offset_days=0,
    )
    partial_traveloka = _offer(
        offer_id="traveloka:partial",
        provider="traveloka",
        currency="USD",
        price_amount=50.0,
        requested_return_date="2026-07-17",
        actual_return_date=None,
        return_offset_days=None,
    ).model_copy(
        update={
            "comparable": False,
            "rank_within_currency": None,
            "global_rank": None,
        }
    )
    google_complete = _offer(
        offer_id="google_fli:complete",
        provider="google_fli",
        currency="USD",
        price_amount=200.0,
        requested_return_date="2026-07-17",
        actual_return_date="2026-07-17",
        return_offset_days=0,
        departure_time="2026-07-10T10:15:00",
        arrival_time="2026-07-10T11:25:00",
    )
    result = ProviderResult(
        provider_name="traveloka",
        capability="exact_round_trip",
        status=ProviderStatusCode.PARTIAL,
        offers=[partial_traveloka, selected_traveloka, google_complete],
        warnings=[],
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Traveloka final selected round-trip total was unavailable.",
                details={
                    "provider": "traveloka",
                    "capability": "exact_round_trip",
                    "failure_type": "final_round_trip_total_unavailable",
                },
                retryable=True,
            )
        ],
        duration_ms=1,
        retryable=True,
    )
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_RoundTripProviderFromResult(result)],
    )

    response = search_exact(_request(return_date="2026-07-17", max_results=3))

    assert response.status == SearchStatus.PARTIAL
    assert [offer.offer_id for offer in response.offers] == [
        "traveloka:selected",
        "google_fli:complete",
        "traveloka:partial",
    ]
    assert [offer.global_rank for offer in response.offers] == [1, 2, None]
    assert [offer.rank_within_currency for offer in response.offers] == [1, 2, None]
    assert [offer.comparable for offer in response.offers] == [True, True, False]


def test_search_exact_keeps_visible_non_comparable_offer_when_results_are_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_comparable_offer = _offer(
        offer_id="traveloka:partial",
        provider="traveloka",
        currency="USD",
        price_amount=50.0,
    ).model_copy(
        update={
            "comparable": False,
            "rank_within_currency": None,
            "global_rank": None,
        }
    )
    result = ProviderResult(
        provider_name="traveloka",
        capability="exact_one_way",
        status=ProviderStatusCode.PARTIAL,
        offers=[
            _offer(
                offer_id="google_fli:first",
                provider="google_fli",
                currency="USD",
                price_amount=100.0,
            ),
            _offer(
                offer_id="google_fli:second",
                provider="google_fli",
                currency="USD",
                price_amount=200.0,
                departure_time="2026-07-10T10:15:00",
                arrival_time="2026-07-10T11:25:00",
            ),
            non_comparable_offer,
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

    response = search_exact(_request(max_results=2))

    assert [offer.offer_id for offer in response.offers] == [
        "google_fli:first",
        "traveloka:partial",
    ]
    assert response.offers[0].global_rank == 1
    assert response.offers[1].comparable is False
    assert response.offers[1].global_rank is None


def test_search_exact_preserves_comparable_offer_when_many_non_comparable_are_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ProviderResult(
        provider_name="traveloka",
        capability="exact_one_way",
        status=ProviderStatusCode.PARTIAL,
        offers=[
            _offer(
                offer_id="google_fli:first",
                provider="google_fli",
                currency="USD",
                price_amount=100.0,
            ),
            _offer(
                offer_id="google_fli:second",
                provider="google_fli",
                currency="USD",
                price_amount=200.0,
                departure_time="2026-07-10T10:15:00",
                arrival_time="2026-07-10T11:25:00",
            ),
            _offer(
                offer_id="traveloka:partial",
                provider="traveloka",
                currency="USD",
                price_amount=50.0,
            ).model_copy(
                update={
                    "comparable": False,
                    "rank_within_currency": None,
                    "global_rank": None,
                }
            ),
            _offer(
                offer_id="other_provider:partial",
                provider="other_provider",
                currency="USD",
                price_amount=40.0,
                departure_time="2026-07-10T12:15:00",
                arrival_time="2026-07-10T13:25:00",
            ).model_copy(
                update={
                    "comparable": False,
                    "rank_within_currency": None,
                    "global_rank": None,
                }
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

    response = search_exact(_request(max_results=2))

    assert sum(offer.comparable for offer in response.offers) == 1
    assert response.offers[0].global_rank == 1
    assert response.offers[1].global_rank is None


def test_search_exact_does_not_hide_mixed_currency_comparable_offer_for_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ProviderResult(
        provider_name="traveloka",
        capability="exact_one_way",
        status=ProviderStatusCode.PARTIAL,
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
                departure_time="2026-07-10T10:15:00",
                arrival_time="2026-07-10T11:25:00",
            ),
            _offer(
                offer_id="traveloka:partial",
                provider="traveloka",
                currency="USD",
                price_amount=50.0,
                departure_time="2026-07-10T12:15:00",
                arrival_time="2026-07-10T13:25:00",
            ).model_copy(
                update={
                    "comparable": False,
                    "rank_within_currency": None,
                    "global_rank": None,
                }
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

    response = search_exact(_request(max_results=2))

    assert [offer.offer_id for offer in response.offers] == [
        "google_fli:usd",
        "google_fli:vnd",
    ]
    assert response.mixed_currency is True
    assert all(offer.comparable is False for offer in response.offers)
    assert all(offer.global_rank is None for offer in response.offers)


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
