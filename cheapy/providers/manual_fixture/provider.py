"""Deterministic manual fixture provider."""

from __future__ import annotations

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    PassengersV1,
    ProviderStatusCode,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult


class ManualFixtureProvider:
    """Static provider used for deterministic provider foundation tests."""

    name = "manual_fixture"
    capabilities = ("exact_one_way",)

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        if (
            request.origin == "CXR"
            and request.destination == "SGN"
            and request.departure_date == "2026-07-10"
            and request.passengers == PassengersV1()
        ):
            return ProviderResult(
                provider_name=self.name,
                capability="exact_one_way",
                status=ProviderStatusCode.SUCCESS,
                offers=[
                    _offer(
                        offer_id="manual_fixture:cxr-sgn-20260710-1",
                        price_amount=1280000.0,
                        global_rank=1,
                        airline_code="VJ",
                        flight_number="VJ601",
                        departure_time="2026-07-10T08:15:00",
                        arrival_time="2026-07-10T09:25:00",
                    ),
                    _offer(
                        offer_id="manual_fixture:cxr-sgn-20260710-2",
                        price_amount=1490000.0,
                        global_rank=2,
                        airline_code="VN",
                        flight_number="VN1341",
                        departure_time="2026-07-10T14:40:00",
                        arrival_time="2026-07-10T15:50:00",
                    ),
                ],
                warnings=[],
                errors=[],
                duration_ms=0,
                retryable=False,
            )

        return ProviderResult(
            provider_name=self.name,
            capability="exact_one_way",
            status=ProviderStatusCode.FAILED,
            offers=[],
            warnings=[],
            errors=[
                ErrorV1(
                    code=ErrorCode.PROVIDER_FAILED,
                    severity=Severity.ERROR,
                    message_en="No manual fixture exists for the requested route/date.",
                    details={
                        "provider": self.name,
                        "capability": "exact_one_way",
                        "origin": request.origin,
                        "destination": request.destination,
                        "departure_date": request.departure_date,
                    },
                    retryable=False,
                )
            ],
            duration_ms=0,
            retryable=False,
        )


def create_provider() -> ManualFixtureProvider:
    return ManualFixtureProvider()


def _offer(
    *,
    offer_id: str,
    price_amount: float,
    global_rank: int,
    airline_code: str,
    flight_number: str,
    departure_time: str,
    arrival_time: str,
) -> FlightOfferV1:
    return FlightOfferV1(
        offer_id=offer_id,
        price_amount=price_amount,
        currency="VND",
        comparable=True,
        rank_within_currency=global_rank,
        global_rank=global_rank,
        provider="manual_fixture",
        requested_origin="CXR",
        requested_destination="SGN",
        actual_origin="CXR",
        actual_destination="SGN",
        requested_departure_date="2026-07-10",
        actual_departure_date="2026-07-10",
        departure_offset_days=0,
        requested_return_date=None,
        actual_return_date=None,
        return_offset_days=None,
        legs=[
            FlightLegV1(
                origin="CXR",
                destination="SGN",
                departure_time=departure_time,
                arrival_time=arrival_time,
                airline_code=airline_code,
                flight_number=flight_number,
                duration_minutes=70,
            )
        ],
        total_duration_minutes=70,
        stops=0,
        flags=OfferFlagsV1(),
        fare_details_status="not_collected",
    )
