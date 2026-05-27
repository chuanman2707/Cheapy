"""Public provider search URL builders."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from pydantic import ValidationError

from cheapy.models import FlightOfferV1, SearchRequestV1, SearchResponseV1
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka.urls import build_full_search_url
from cheapy.public_url_safety import validate_public_search_url


def build_public_search_url(
    provider: str,
    request: SearchRequestV1,
    offer: FlightOfferV1,
) -> str | None:
    """Build a safe public search URL for a provider offer."""
    if provider != offer.provider:
        return None
    if provider == "traveloka":
        return _validated(provider, _build_traveloka_url(request, offer))
    if provider == "google_fli":
        return _validated(provider, _build_google_fli_url(request, offer))
    if provider == "skyscanner":
        return _validated(provider, _build_skyscanner_url(request, offer))
    return None


def attach_public_search_urls(
    request: SearchRequestV1,
    response: SearchResponseV1,
) -> SearchResponseV1:
    """Return a response with public search URLs attached to supported offers."""
    offers = [
        offer.model_copy(
            update={
                "public_search_url": build_public_search_url(
                    offer.provider,
                    request,
                    offer,
                )
            }
        )
        for offer in response.offers
    ]
    return response.model_copy(update={"offers": offers})


def _build_traveloka_url(request: SearchRequestV1, offer: FlightOfferV1) -> str | None:
    try:
        provider_request = _provider_request_from_offer(request, offer)
    except (ValueError, ValidationError):
        return None
    return build_full_search_url(provider_request)


def _provider_request_from_offer(
    request: SearchRequestV1,
    offer: FlightOfferV1,
) -> ProviderExactOneWayRequest | ProviderExactRoundTripRequest:
    kwargs = {
        "origin": offer.actual_origin,
        "destination": offer.actual_destination,
        "departure_date": offer.actual_departure_date,
        "requested_origin": request.origin,
        "requested_destination": request.destination,
        "requested_departure_date": request.departure_date,
        "passengers": request.passengers,
    }
    if offer.actual_return_date is None:
        return ProviderExactOneWayRequest(**kwargs)
    return ProviderExactRoundTripRequest(
        **kwargs,
        return_date=offer.actual_return_date,
        requested_return_date=request.return_date or offer.actual_return_date,
    )


def _build_google_fli_url(
    request: SearchRequestV1,
    offer: FlightOfferV1,
) -> str | None:
    try:
        departure_date = date.fromisoformat(offer.actual_departure_date).isoformat()
        return_date = (
            date.fromisoformat(offer.actual_return_date).isoformat()
            if offer.actual_return_date is not None
            else None
        )
    except ValueError:
        return None

    passengers = request.passengers
    infant_count = passengers.infants_on_lap + passengers.infants_in_seat
    trip_text = (
        f"Flights from {offer.actual_origin} to {offer.actual_destination} "
        f"on {departure_date}"
    )
    if return_date is not None:
        trip_text = f"{trip_text} returning {return_date}"
    query_text = (
        f"{trip_text} for "
        f"{_count_label(passengers.adults, 'adult')}, "
        f"{_count_label(passengers.children, 'child', 'children')}, "
        f"{_count_label(infant_count, 'infant')}"
    )
    return f"https://www.google.com/travel/flights?{urlencode({'q': query_text})}"


def _build_skyscanner_url(
    request: SearchRequestV1,
    offer: FlightOfferV1,
) -> str | None:
    passengers = request.passengers
    if (
        passengers.children
        or passengers.infants_on_lap
        or passengers.infants_in_seat
    ):
        return None

    try:
        departure_date = _skyscanner_date(offer.actual_departure_date)
        return_date = (
            _skyscanner_date(offer.actual_return_date)
            if offer.actual_return_date is not None
            else None
        )
    except ValueError:
        return None

    route_path = (
        "https://www.skyscanner.com.sg/transport/flights/"
        f"{offer.actual_origin.lower()}/{offer.actual_destination.lower()}/"
        f"{departure_date}/"
    )
    rtn = "0"
    if return_date is not None:
        route_path = f"{route_path}{return_date}/"
        rtn = "1"

    query = urlencode(
        [
            ("adultsv2", str(passengers.adults)),
            ("cabinclass", "economy"),
            ("childrenv2", ""),
            ("ref", "home"),
            ("rtn", rtn),
        ]
    )
    return f"{route_path}?{query}"


def _skyscanner_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return parsed.strftime("%y%m%d")


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def _validated(provider: str, url: str | None) -> str | None:
    if url is None:
        return None
    return validate_public_search_url(provider, url)
