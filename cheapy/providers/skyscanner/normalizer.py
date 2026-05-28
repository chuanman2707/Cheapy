"""Normalize Skyscanner itinerary candidates into Cheapy Contract V1 offers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import hashlib
import re

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest
from cheapy.providers.skyscanner.adapter import (
    SkyscannerItineraryCandidate,
    SkyscannerLegCandidate,
)


PROVIDER_NAME = "skyscanner"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
SAFE_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
SENSITIVE_ITEM_ID_TOKENS = (
    "/transport_deeplink/",
    "transport_deeplink",
    "__secure-anon_token",
    "secret-cookie",
    "cookie",
    "header",
    "request_body",
    "requestbody",
    "raw_payload",
    "raw",
    "challenge",
    "sessionid",
    "session",
)


def normalize_candidates(
    candidates: Sequence[SkyscannerItineraryCandidate],
    request: ProviderRequest,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    """Convert sanitized Skyscanner candidates into Contract V1 offers."""
    offers: list[FlightOfferV1] = []
    errors: list[ErrorV1] = []
    for candidate in candidates:
        try:
            offers.append(_normalize_candidate(candidate, request))
        except Exception as exc:
            errors.append(_parse_error(candidate, exc))
    return _rank_offers(offers), errors


def _normalize_candidate(
    candidate: SkyscannerItineraryCandidate,
    request: ProviderRequest,
) -> FlightOfferV1:
    if not candidate.legs:
        raise ValueError("itinerary candidate has no legs")

    legs = [_normalize_leg(leg) for leg in candidate.legs]
    actual_departure_date = legs[0].departure_time[:10]
    actual_return_date = _actual_return_date(legs, request)
    if isinstance(request, ProviderExactRoundTripRequest) and actual_return_date is None:
        raise ValueError("round-trip itinerary candidate has no return leg")

    departure_offset_days = _date_offset(
        actual_departure_date,
        request.requested_departure_date,
    )
    return_offset_days = (
        _date_offset(actual_return_date, request.requested_return_date)
        if isinstance(request, ProviderExactRoundTripRequest)
        and actual_return_date is not None
        else None
    )

    return FlightOfferV1(
        offer_id=_offer_id(candidate, request),
        price_amount=candidate.price_amount,
        currency=candidate.currency,
        comparable=True,
        rank_within_currency=None,
        global_rank=None,
        provider=PROVIDER_NAME,
        requested_origin=request.requested_origin,
        requested_destination=request.requested_destination,
        actual_origin=request.origin,
        actual_destination=request.destination,
        nearby_origin_distance_km=None,
        nearby_destination_distance_km=None,
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
        legs=legs,
        total_duration_minutes=candidate.total_duration_minutes,
        stops=candidate.stops,
        flags=OfferFlagsV1(
            uses_flexible_departure_date=departure_offset_days != 0,
            uses_flexible_return_date=return_offset_days not in (None, 0),
        ),
        fare_details_status="not_collected",
        public_search_url=None,
    )


def _normalize_leg(leg: SkyscannerLegCandidate) -> FlightLegV1:
    return FlightLegV1(
        origin=leg.origin,
        destination=leg.destination,
        departure_time=leg.departure_time,
        arrival_time=leg.arrival_time,
        airline_code=leg.airline_code,
        flight_number=leg.flight_number,
        duration_minutes=leg.duration_minutes,
    )


def _actual_return_date(
    legs: list[FlightLegV1],
    request: ProviderRequest,
) -> str | None:
    if not isinstance(request, ProviderExactRoundTripRequest):
        return None
    for leg in legs:
        if leg.origin == request.destination and leg.destination == request.origin:
            return leg.departure_time[:10]
    return None


def _offer_id(
    candidate: SkyscannerItineraryCandidate,
    request: ProviderRequest,
) -> str:
    prefix = f"{PROVIDER_NAME}:{request.origin}-{request.destination}:{request.departure_date}"
    item_id = _safe_item_id_component(candidate.item_id)
    if isinstance(request, ProviderExactRoundTripRequest):
        return f"{prefix}:{request.return_date}:{item_id}"
    return f"{prefix}:{item_id}"


def _safe_item_id_component(item_id: str) -> str:
    if SAFE_ITEM_ID_RE.fullmatch(item_id) and not _contains_sensitive_token(item_id):
        return item_id
    digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]
    return f"opaque-{digest}"


def _contains_sensitive_token(value: str) -> bool:
    text = value.lower()
    return any(token in text for token in SENSITIVE_ITEM_ID_TOKENS)


def _date_offset(actual: str, requested: str | None) -> int:
    if requested is None:
        raise ValueError("requested date is required")
    return (date.fromisoformat(actual) - date.fromisoformat(requested)).days


def _rank_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    currencies = {offer.currency for offer in offers}
    if len(currencies) <= 1:
        return [
            offer.model_copy(
                update={
                    "comparable": True,
                    "rank_within_currency": rank,
                    "global_rank": rank,
                }
            )
            for rank, offer in enumerate(offers, start=1)
        ]

    currency_ranks: dict[str, int] = {}
    ranked: list[FlightOfferV1] = []
    for offer in offers:
        rank = currency_ranks.get(offer.currency, 0) + 1
        currency_ranks[offer.currency] = rank
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


def _parse_error(candidate: SkyscannerItineraryCandidate, exc: Exception) -> ErrorV1:
    return ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Skyscanner itinerary could not be normalized.",
        details={
            "provider": PROVIDER_NAME,
            "failure_type": "parse_error",
            "item_id": _safe_item_id_component(candidate.item_id),
            "exception_type": type(exc).__name__,
        },
        retryable=False,
    )
