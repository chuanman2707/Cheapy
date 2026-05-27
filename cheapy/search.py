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
from cheapy.providers.base import (
    FlightProvider,
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.registry import (
    ProviderLoadError,
    ProviderManifestError,
    load_search_providers,
)
from cheapy.public_links import attach_public_search_urls
from cheapy.search_planner import (
    EXACT_ONE_WAY_CAPABILITY,
    EXACT_ROUND_TRIP_CAPABILITY,
    PlannedProviderCall,
    SearchCandidate,
    plan_search,
)


_MIXED_CURRENCY_NOTE = (
    "Currency conversion was not applied; compare mixed-currency offers separately."
)


def search_exact(request: SearchRequestV1) -> SearchResponseV1:
    """Run planner-backed one-way or round-trip search and return Contract V1.

    This API is intentionally sync-only. It crosses into provider async
    code with ``asyncio.run()``, so callers must invoke it outside an active
    event loop. Future async hosts should dispatch it from a worker thread.
    """
    fallback_origin = _normalize_airport_value(request.origin)
    fallback_destination = _normalize_airport_value(request.destination)
    fallback_request_id = _request_id(request, fallback_origin, fallback_destination)

    try:
        origin = resolve_airport(request.origin)
    except AirportNotFound:
        return _failed_response(
            request_id=fallback_request_id,
            errors=[_airport_not_found_error("origin", fallback_origin)],
            search_plan=_empty_plan(request.search_mode),
        )

    try:
        destination = resolve_airport(request.destination)
    except AirportNotFound:
        return _failed_response(
            request_id=fallback_request_id,
            errors=[_airport_not_found_error("destination", fallback_destination)],
            search_plan=_empty_plan(request.search_mode),
        )

    request_id = _request_id(request, origin.iata, destination.iata)

    try:
        providers = load_search_providers()
    except (ProviderManifestError, ProviderLoadError) as exc:
        return _failed_response(
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

    required_capability = _required_capability(request)
    capable_providers = [
        provider
        for provider in providers
        if required_capability in provider.capabilities
    ]
    planned = plan_search(request, origin.iata, destination.iata, providers)

    if not capable_providers:
        reason = (
            "no_exact_one_way_provider"
            if required_capability == EXACT_ONE_WAY_CAPABILITY
            else "no_exact_round_trip_provider"
        )
        return _failed_response(
            request_id=request_id,
            errors=[
                _error(
                    code=ErrorCode.NO_PROVIDER_AVAILABLE,
                    message_en="No enabled provider supports the requested search.",
                    details={"reason": reason},
                )
            ],
            search_plan=planned.search_plan,
        )

    provider_results = asyncio.run(
        _call_planned_providers(planned.selected_calls, request.passengers)
    )

    return _response_from_provider_results(
        request=request,
        request_id=request_id,
        provider_results=provider_results,
        search_plan=planned.search_plan,
    )


def _normalize_airport_value(value: str) -> str:
    return value.strip().upper()


def _request_id(request: SearchRequestV1, origin: str, destination: str) -> str:
    passengers = request.passengers
    trip_shape = "one_way" if request.return_date is None else "round_trip"
    return_date = request.return_date if request.return_date is not None else "none"
    return (
        f"search:{trip_shape}:{origin}:{destination}:{request.departure_date}:"
        f"{return_date}:{request.search_mode.value}:{passengers.adults}:"
        f"{passengers.children}:"
        f"{passengers.infants_on_lap}:{passengers.infants_in_seat}:"
        f"{request.max_results}"
    )


def _required_capability(request: SearchRequestV1) -> str:
    if request.return_date is None:
        return EXACT_ONE_WAY_CAPABILITY
    return EXACT_ROUND_TRIP_CAPABILITY


async def _call_planned_providers(
    planned_calls: tuple[PlannedProviderCall, ...],
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
            results.append(
                _normalize_provider_result(provider, candidate.capability, raw_result)
            )
        except Exception as exc:
            results.append(
                _provider_exception_result(provider, candidate.capability, exc)
            )
    return results


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


def _provider_malformed_result(
    provider: FlightProvider,
    capability: str,
    exc: Exception,
) -> ProviderResult:
    return _provider_failed_result(
        provider_name=provider.name,
        capability=capability,
        message_en="Provider returned an invalid result.",
        details={
            "provider": provider.name,
            "capability": capability,
            "exception_type": type(exc).__name__,
        },
    )


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


def _provider_failed_result(
    *,
    provider_name: str,
    capability: str,
    message_en: str,
    details: dict[str, object],
) -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        capability=capability,
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[
            _error(
                code=ErrorCode.PROVIDER_FAILED,
                message_en=message_en,
                details=details,
            )
        ],
        duration_ms=0,
        retryable=False,
    )


def _provider_status_error(result: ProviderResult) -> ErrorV1:
    return _error(
        code=ErrorCode.PROVIDER_FAILED,
        message_en="Provider returned a non-success status without an error.",
        details={
            "provider": result.provider_name,
            "capability": result.capability,
            "provider_status": result.status.value,
        },
    )


def _response_from_provider_results(
    *,
    request: SearchRequestV1,
    request_id: str,
    provider_results: list[ProviderResult],
    search_plan: SearchPlanV1,
) -> SearchResponseV1:
    offers = _deduplicate_offers(
        [offer for result in provider_results for offer in result.offers]
    )
    returned_offers = _rank_offers(_select_returned_offers(offers, request.max_results))
    warnings = _response_warnings(
        provider_results=provider_results,
        returned_offers=returned_offers,
        search_plan=search_plan,
    )
    errors = [error for result in provider_results for error in result.errors]
    mixed_currency = len({offer.currency for offer in returned_offers}) > 1

    response = SearchResponseV1(
        schema_version="1",
        status=_response_status(returned_offers, errors),
        request_id=request_id,
        offers=returned_offers,
        warnings=warnings,
        errors=errors,
        provider_statuses=[
            _provider_status_from_result(result) for result in provider_results
        ],
        search_plan=search_plan,
        mixed_currency=mixed_currency,
        currency_groups=_currency_groups(returned_offers),
        currency_notes=[_MIXED_CURRENCY_NOTE] if mixed_currency else [],
        candidates=None,
    )
    try:
        return attach_public_search_urls(request, response)
    except Exception:
        return response


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


def _failed_response(
    *,
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
    succeeded = 1 if result.status in _SUCCEEDED_PROVIDER_STATUSES else 0
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


_SUCCEEDED_PROVIDER_STATUSES = {
    ProviderStatusCode.SUCCESS,
    ProviderStatusCode.PARTIAL,
}


def _response_status(
    offers: list[FlightOfferV1],
    errors: list[ErrorV1],
) -> SearchStatus:
    if offers and errors:
        return SearchStatus.PARTIAL
    if offers:
        return SearchStatus.SUCCESS
    return SearchStatus.FAILED


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


def _same_provider_itinerary_signature(offer: FlightOfferV1) -> tuple[object, ...]:
    return (
        offer.provider,
        offer.actual_origin,
        offer.actual_destination,
        offer.actual_departure_date,
        offer.actual_return_date,
        offer.currency,
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


def _sort_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers}
    if len(currencies) <= 1:
        return sorted(
            offers,
            key=lambda offer: (
                not offer.comparable,
                offer.price_amount,
                offer.offer_id,
            ),
        )
    return sorted(
        offers,
        key=lambda offer: (
            not offer.comparable,
            offer.currency,
            offer.price_amount,
            offer.offer_id,
        ),
    )


def _select_returned_offers(
    offers: list[FlightOfferV1],
    max_results: int,
) -> list[FlightOfferV1]:
    sorted_offers = _sort_offers(offers)
    selected = sorted_offers[:max_results]
    if max_results <= 1 or len(selected) < max_results:
        return selected
    if any(not offer.comparable for offer in selected):
        return selected

    selected_comparable_currencies = {
        offer.currency for offer in selected if offer.comparable
    }
    if len(selected_comparable_currencies) > 1:
        return selected

    selected_offer_ids = {offer.offer_id for offer in selected}
    missing_non_comparable: FlightOfferV1 | None = None
    for offer in sorted_offers[max_results:]:
        if offer.comparable or offer.offer_id in selected_offer_ids:
            continue
        missing_non_comparable = offer
        break

    if missing_non_comparable is None:
        return selected
    replace_index = _last_comparable_index(selected)
    if replace_index is None or _comparable_count(selected) <= 1:
        return selected
    selected[replace_index] = missing_non_comparable
    return _sort_offers(selected)


def _comparable_count(offers: list[FlightOfferV1]) -> int:
    return sum(1 for offer in offers if offer.comparable)


def _last_comparable_index(offers: list[FlightOfferV1]) -> int | None:
    for index in range(len(offers) - 1, -1, -1):
        if offers[index].comparable:
            return index
    return None


def _rank_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers if offer.comparable}
    if len(currencies) <= 1:
        ranked: list[FlightOfferV1] = []
        comparable_rank = 0
        for offer in offers:
            if not offer.comparable:
                ranked.append(
                    offer.model_copy(
                        update={
                            "comparable": False,
                            "rank_within_currency": None,
                            "global_rank": None,
                        }
                    )
                )
                continue

            comparable_rank += 1
            ranked.append(
                offer.model_copy(
                    update={
                        "comparable": True,
                        "rank_within_currency": comparable_rank,
                        "global_rank": comparable_rank,
                    }
                )
            )
        return ranked

    currency_rank_counts: dict[str, int] = {}
    ranked: list[FlightOfferV1] = []
    for offer in offers:
        if not offer.comparable:
            ranked.append(
                offer.model_copy(
                    update={
                        "comparable": False,
                        "rank_within_currency": None,
                        "global_rank": None,
                    }
                )
            )
            continue

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


def _currency_groups(offers: list[FlightOfferV1]) -> list[CurrencyGroupV1]:
    return [
        CurrencyGroupV1(
            currency=currency,
            offer_ids=[offer.offer_id for offer in offers if offer.currency == currency],
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
