"""Search candidate planning and provider-call budget accounting."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from cheapy.models import CandidateFamily, SearchMode, SearchPlanV1, SearchRequestV1


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


class SearchProvider(Protocol):
    """Provider shape required for search planning."""

    name: str
    capabilities: Sequence[str]


@dataclass(frozen=True)
class SearchCandidate:
    family: CandidateFamily
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    departure_offset_days: int
    return_offset_days: int | None
    capability: str


@dataclass(frozen=True)
class PlannedProviderCall:
    candidate: SearchCandidate
    provider: SearchProvider


@dataclass(frozen=True)
class PlannedSearch:
    search_plan: SearchPlanV1
    candidates: tuple[SearchCandidate, ...]
    planned_calls: tuple[PlannedProviderCall, ...]
    selected_calls: tuple[PlannedProviderCall, ...]


def plan_search(
    request: SearchRequestV1,
    origin: str,
    destination: str,
    providers: Sequence[SearchProvider],
) -> PlannedSearch:
    """Build candidate/provider calls and select calls within the Gate 8 budget."""

    candidates = _build_candidates(request, origin, destination)
    planned_calls = tuple(_planned_calls(candidates, providers))
    selected_calls = planned_calls[:GATE_8_PROVIDER_CALL_BUDGET]
    search_plan = _build_search_plan(request, candidates, planned_calls, selected_calls)
    return PlannedSearch(
        search_plan=search_plan,
        candidates=candidates,
        planned_calls=planned_calls,
        selected_calls=selected_calls,
    )


def _build_candidates(
    request: SearchRequestV1,
    origin: str,
    destination: str,
) -> tuple[SearchCandidate, ...]:
    departure_date = date.fromisoformat(request.departure_date)
    return_date = (
        date.fromisoformat(request.return_date) if request.return_date is not None else None
    )

    if return_date is None:
        offsets = (0,) if request.search_mode == SearchMode.EXACT else _ONE_WAY_ORDER
        return tuple(
            _candidate(
                family=_family_for_offsets(offset, None, request.search_mode),
                origin=origin,
                destination=destination,
                departure_date=departure_date + timedelta(days=offset),
                return_date=None,
                departure_offset_days=offset,
                return_offset_days=None,
                capability=EXACT_ONE_WAY_CAPABILITY,
            )
            for offset in offsets
        )

    offset_pairs = (
        ((0, 0),)
        if request.search_mode == SearchMode.EXACT
        else _round_trip_expanded_offset_pairs()
    )
    candidates: list[SearchCandidate] = []
    for departure_offset, return_offset in offset_pairs:
        candidate_departure = departure_date + timedelta(days=departure_offset)
        candidate_return = return_date + timedelta(days=return_offset)
        if candidate_return < candidate_departure:
            continue

        candidates.append(
            _candidate(
                family=_family_for_offsets(
                    departure_offset, return_offset, request.search_mode
                ),
                origin=origin,
                destination=destination,
                departure_date=candidate_departure,
                return_date=candidate_return,
                departure_offset_days=departure_offset,
                return_offset_days=return_offset,
                capability=EXACT_ROUND_TRIP_CAPABILITY,
            )
        )
    return tuple(candidates)


def _candidate(
    *,
    family: CandidateFamily,
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date | None,
    departure_offset_days: int,
    return_offset_days: int | None,
    capability: str,
) -> SearchCandidate:
    return SearchCandidate(
        family=family,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        departure_offset_days=departure_offset_days,
        return_offset_days=return_offset_days,
        capability=capability,
    )


def _family_for_offsets(
    departure_offset: int,
    return_offset: int | None,
    search_mode: SearchMode,
) -> CandidateFamily:
    if search_mode == SearchMode.EXACT:
        return CandidateFamily.EXACT
    if departure_offset == 0 and return_offset in {None, 0}:
        return CandidateFamily.EXACT
    return CandidateFamily.FLEXIBLE_DATES


def _round_trip_expanded_offset_pairs() -> tuple[tuple[int, int], ...]:
    return tuple(
        sorted(
            (
                (departure_offset, return_offset)
                for departure_offset in _FLEXIBLE_OFFSETS
                for return_offset in _FLEXIBLE_OFFSETS
            ),
            key=lambda pair: (
                abs(pair[0]) + abs(pair[1]),
                _round_trip_axis_rank(pair),
                abs(pair[0]),
                abs(pair[1]),
                pair[0],
                pair[1],
            ),
        )
    )


def _round_trip_axis_rank(pair: tuple[int, int]) -> int:
    departure_offset, return_offset = pair
    if departure_offset < 0 and return_offset == 0:
        return 0
    if departure_offset == 0 and return_offset < 0:
        return 1
    if departure_offset == 0 and return_offset > 0:
        return 2
    if departure_offset > 0 and return_offset == 0:
        return 3
    return 4


def _planned_calls(
    candidates: Sequence[SearchCandidate],
    providers: Sequence[SearchProvider],
) -> tuple[PlannedProviderCall, ...]:
    return tuple(
        PlannedProviderCall(candidate=candidate, provider=provider)
        for candidate in candidates
        for provider in providers
        if candidate.capability in provider.capabilities
    )


def _build_search_plan(
    request: SearchRequestV1,
    candidates: Sequence[SearchCandidate],
    planned_calls: Sequence[PlannedProviderCall],
    selected_calls: Sequence[PlannedProviderCall],
) -> SearchPlanV1:
    planned_candidate_ids = {id(candidate) for candidate in candidates}
    selected_candidate_ids = {id(call.candidate) for call in selected_calls}
    selected_call_ids = {id(call) for call in selected_calls}

    candidate_count_by_family = {
        family: sum(1 for candidate in candidates if candidate.family == family)
        for family in _FAMILY_ORDER
    }
    candidate_count_by_family = {
        family: count for family, count in candidate_count_by_family.items() if count
    }

    provider_call_count_by_family = {
        family: sum(1 for call in selected_calls if call.candidate.family == family)
        for family in _FAMILY_ORDER
    }
    provider_call_count_by_family = {
        family: count for family, count in provider_call_count_by_family.items() if count
    }

    skipped_calls = [call for call in planned_calls if id(call) not in selected_call_ids]
    truncated_families = [
        family
        for family in _FAMILY_ORDER
        if any(call.candidate.family == family for call in skipped_calls)
    ]

    candidate_families = [
        family
        for family in _FAMILY_ORDER
        if any(candidate.family == family for candidate in candidates)
    ]

    return SearchPlanV1(
        search_mode=request.search_mode,
        planned_candidate_count=len(planned_candidate_ids),
        executed_candidate_count=len(selected_candidate_ids),
        planned_provider_call_count=len(planned_calls),
        executed_provider_call_count=len(selected_calls),
        candidate_count_by_family=candidate_count_by_family,
        provider_call_count_by_family=provider_call_count_by_family,
        truncated=bool(skipped_calls),
        truncated_families=truncated_families,
        candidate_families=candidate_families,
    )
