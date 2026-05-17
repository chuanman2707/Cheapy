"""Traveloka URL helpers."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)


DEFAULT_BASE_URL = "https://www.traveloka.com/en-en/flight/fulltwosearch"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


def build_full_search_url(
    request: ProviderRequest,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    date_part = traveloka_date(request.departure_date)
    if isinstance(request, ProviderExactRoundTripRequest):
        date_part = f"{date_part}.{traveloka_date(request.return_date)}"
    params = {
        "ap": f"{request.origin}.{request.destination}",
        "dt": date_part,
        "ps": passenger_spec(request),
        "sc": "ECONOMY",
        "funnelSource": "SEO-Homepage-SearchForm",
    }
    return f"{base_url}?{urlencode(params)}"


def traveloka_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.day}-{parsed.month}-{parsed.year}"


def passenger_spec(request: ProviderRequest) -> str:
    passengers = request.passengers
    return (
        f"{passengers.adults}."
        f"{passengers.children}."
        f"{passengers.infants_on_lap + passengers.infants_in_seat}"
    )
