from __future__ import annotations

from cheapy.models import CandidateFamily, PassengersV1, SearchMode, SearchRequestV1
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
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

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        raise NotImplementedError

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        raise NotImplementedError


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
    first_flexible = planned.selected_calls[1].candidate

    assert [
        (call.candidate.departure_offset_days, call.provider.name)
        for call in planned.selected_calls
    ] == [
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
    assert first_flexible.departure_date == "2026-07-09"
    assert first_flexible.return_date is None
    assert first_flexible.requested_departure_date == "2026-07-10"
    assert first_flexible.requested_return_date is None


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
        (0, -1, "exact_round_trip"),
        (0, 1, "exact_round_trip"),
        (-1, 0, "exact_round_trip"),
        (1, 0, "exact_round_trip"),
    ]
    assert len(planned.selected_calls) == GATE_8_PROVIDER_CALL_BUDGET
    assert planned.search_plan.planned_candidate_count == 49
    assert planned.search_plan.executed_candidate_count == 10
    assert planned.search_plan.planned_provider_call_count == 49
    assert planned.search_plan.executed_provider_call_count == 10
    assert planned.search_plan.truncated is True
    assert planned.search_plan.truncated_families == [CandidateFamily.FLEXIBLE_DATES]
    assert planned.selected_calls[1].candidate.departure_date == "2026-07-10"
    assert planned.selected_calls[1].candidate.return_date == "2026-07-16"
    assert planned.selected_calls[1].candidate.requested_departure_date == "2026-07-10"
    assert planned.selected_calls[1].candidate.requested_return_date == "2026-07-17"


def test_plan_skips_invalid_round_trip_flexible_pairs() -> None:
    provider = _Provider("round", (EXACT_ROUND_TRIP_CAPABILITY,))

    planned = plan_search(
        _request(departure_date="2026-07-10", return_date="2026-07-10"),
        "SGN",
        "BKK",
        [provider],
    )

    assert all(
        candidate.return_date is None
        or candidate.return_date >= candidate.departure_date
        for candidate in planned.planned_candidates
    )
    assert isinstance(planned.planned_candidates, tuple)
    assert not hasattr(planned, "candidates")
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
