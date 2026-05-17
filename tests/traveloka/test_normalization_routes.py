from __future__ import annotations

from cheapy.models import FlightLegV1
from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka.normalization.routes import validate_route


def _leg(origin: str, destination: str) -> FlightLegV1:
    return FlightLegV1(
        origin=origin,
        destination=destination,
        departure_time="2026-07-10T09:00:00",
        arrival_time="2026-07-10T10:35:00",
        airline_code="VJ",
        flight_number="VJ801",
        duration_minutes=95,
    )


def test_validate_route_accepts_one_way_chain() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )

    route = validate_route(request, [_leg("SGN", "BKK")])

    assert route.outbound_end_index == 0
    assert route.return_start_index is None
