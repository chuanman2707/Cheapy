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
from cheapy.providers.base import (
    FlightProvider,
    ProviderExactOneWayRequest,
    ProviderResult,
)
from cheapy.providers.registry import (
    ProviderLoadError,
    ProviderManifestError,
    load_search_providers,
)


_EXACT_CAPABILITY = "exact_one_way"
_MIXED_CURRENCY_NOTE = (
    "Currency conversion was not applied; compare mixed-currency offers separately."
)


def search_exact(request: SearchRequestV1) -> SearchResponseV1:
    """Run Gate 4 exact one-way search and return a Contract V1 response.

    This Gate 4 API is intentionally sync-only. It crosses into provider async
    code with ``asyncio.run()``, so callers must invoke it outside an active
    event loop; future async hosts should dispatch it from a worker thread.
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

    unsupported_reason = _unsupported_reason(request)
    if unsupported_reason is not None:
        return _failed_response(
            request_id=request_id,
            errors=[
                _error(
                    code=ErrorCode.NO_PROVIDER_AVAILABLE,
                    message_en=(
                        "No provider is available for the requested Gate 4 "
                        "search scope."
                    ),
                    details={"unsupported_reason": unsupported_reason},
                )
            ],
            search_plan=_empty_plan(request.search_mode),
        )

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

    if not providers:
        return _failed_response(
            request_id=request_id,
            errors=[
                _error(
                    code=ErrorCode.NO_PROVIDER_AVAILABLE,
                    message_en=(
                        "No enabled provider is available for exact one-way "
                        "search."
                    ),
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
            raw_result = await provider.search_exact_one_way(request)
            results.append(_normalize_provider_result(provider, raw_result))
        except Exception as exc:
            results.append(_provider_exception_result(provider, exc))
    return results


def _normalize_provider_result(
    provider: FlightProvider,
    raw_result: object,
) -> ProviderResult:
    try:
        result = ProviderResult.model_validate(raw_result)
    except Exception as exc:
        return _provider_malformed_result(provider, exc)

    result = result.model_copy(update={"capability": _EXACT_CAPABILITY})
    if result.status != ProviderStatusCode.SUCCESS and not result.errors:
        error = _provider_status_error(result)
        return result.model_copy(update={"errors": [error]})

    return result


def _provider_malformed_result(
    provider: FlightProvider,
    exc: Exception,
) -> ProviderResult:
    return _provider_failed_result(
        provider_name=provider.name,
        message_en="Provider returned an invalid exact one-way result.",
        details={
            "provider": provider.name,
            "capability": _EXACT_CAPABILITY,
            "exception_type": type(exc).__name__,
        },
    )


def _provider_exception_result(
    provider: FlightProvider,
    exc: Exception,
) -> ProviderResult:
    return _provider_failed_result(
        provider_name=provider.name,
        message_en="Provider raised an unexpected exception.",
        details={
            "provider": provider.name,
            "capability": _EXACT_CAPABILITY,
            "exception_type": type(exc).__name__,
        },
    )


def _provider_failed_result(
    *,
    provider_name: str,
    message_en: str,
    details: dict[str, object],
) -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        capability=_EXACT_CAPABILITY,
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
) -> SearchResponseV1:
    offers = [offer for result in provider_results for offer in result.offers]
    returned_offers = _rank_offers(_sort_offers(offers)[: request.max_results])
    warnings = [warning for result in provider_results for warning in result.warnings]
    errors = [error for result in provider_results for error in result.errors]
    mixed_currency = len({offer.currency for offer in returned_offers}) > 1

    return SearchResponseV1(
        schema_version="1",
        status=_response_status(returned_offers, errors),
        request_id=request_id,
        offers=returned_offers,
        warnings=warnings,
        errors=errors,
        provider_statuses=[
            _provider_status_from_result(result) for result in provider_results
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


def _sort_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers}
    if len(currencies) <= 1:
        return sorted(offers, key=lambda offer: (offer.price_amount, offer.offer_id))
    return sorted(
        offers,
        key=lambda offer: (offer.currency, offer.price_amount, offer.offer_id),
    )


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
