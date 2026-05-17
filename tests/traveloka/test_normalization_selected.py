from __future__ import annotations

from decimal import Decimal

from cheapy.providers.base import ProviderExactRoundTripRequest
from cheapy.providers.traveloka.normalization.selected import (
    normalize_selected_round_trip,
)
from cheapy.providers.traveloka.results import TravelokaSelectedRoundTripResult


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def _traveloka_search_result(
    *,
    item_id: str = "tv-search-1",
    amount: str = "29890",
    decimal_points: str = "2",
    flight_number: str = "VJ-801",
    origin: str = "SGN",
    destination: str = "BKK",
    departure_day: str = "10",
    departure_hour: str = "9",
    arrival_hour: str = "10",
    arrival_minute: str = "35",
) -> dict[str, object]:
    def route(
        *,
        origin: str,
        destination: str,
        departure_day: str,
        flight_number: str,
        departure_hour: str = "9",
        arrival_hour: str = "10",
        arrival_minute: str = "35",
    ) -> dict[str, object]:
        return {
            "departureAirport": origin,
            "arrivalAirport": destination,
            "totalNumStop": "0",
            "durationInMinutes": "95",
            "segments": [
                {
                    "departureAirport": origin,
                    "arrivalAirport": destination,
                    "flightNumber": flight_number,
                    "airlineCode": "VJ",
                    "durationMinutes": "95",
                    "departureDate": {
                        "year": "2026",
                        "month": "7",
                        "day": departure_day,
                    },
                    "departureTime": {"hour": departure_hour, "minute": "0"},
                    "arrivalDate": {
                        "year": "2026",
                        "month": "7",
                        "day": departure_day,
                    },
                    "arrivalTime": {
                        "hour": arrival_hour,
                        "minute": arrival_minute,
                    },
                }
            ],
        }

    price = {
        "currencyValue": {"currency": "USD", "amount": amount},
        "numOfDecimalPoint": decimal_points,
    }
    return {
        "id": item_id,
        "flightMetadata": {
            "totalNumStop": "0",
            "tripDuration": "95",
            "airlineIds": ["VJ"],
            "totalCombinedPrice": price,
        },
        "fare": {"display": price},
        "connectingFlightRoutes": [
            route(
                origin=origin,
                destination=destination,
                departure_day=departure_day,
                flight_number=flight_number,
                departure_hour=departure_hour,
                arrival_hour=arrival_hour,
                arrival_minute=arrival_minute,
            )
        ],
    }


def _selected_result(
    *,
    selected_outbound_key: str | None = "out-1",
    selected_return_key: str | None = "ret-1",
    final_total_amount: Decimal = Decimal("321.09"),
    final_total_currency: str = "USD",
    return_departure_day: str = "17",
) -> TravelokaSelectedRoundTripResult:
    return TravelokaSelectedRoundTripResult(
        outbound_payload={
            "data": {
                "meta": {"searchCompleted": True},
                "searchResults": [
                    _traveloka_search_result(
                        item_id="out-1",
                        amount="11100",
                        flight_number="VJ-801",
                    )
                ],
            }
        },
        return_payload={
            "data": {
                "meta": {"searchCompleted": True},
                "searchResults": [
                    _traveloka_search_result(
                        item_id="ret-1",
                        amount="22200",
                        flight_number="VJ-802",
                        origin="BKK",
                        destination="SGN",
                        departure_day=return_departure_day,
                        departure_hour="11",
                        arrival_hour="12",
                    )
                ],
            }
        },
        selected_outbound_key=selected_outbound_key,
        selected_return_key=selected_return_key,
        final_total_amount=final_total_amount,
        final_total_currency=final_total_currency,
        source_paths=(
            "/api/v2/flight/search/initial",
            "/api/v2/flight/search/poll",
        ),
    )


def test_normalize_selected_round_trip_uses_final_total_and_marks_comparable() -> None:
    offers, errors = normalize_selected_round_trip(
        _selected_result(),
        _round_trip_request(),
    )

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:selected:out-1:ret-1"
    assert offer.price_amount == 321.09
    assert offer.currency == "USD"
    assert offer.comparable is True
    assert offer.rank_within_currency == 1
    assert offer.global_rank == 1
    assert offer.actual_return_date == "2026-07-17"
    assert offer.stops == 0
    assert [leg.flight_number for leg in offer.legs] == ["VJ-801", "VJ-802"]
