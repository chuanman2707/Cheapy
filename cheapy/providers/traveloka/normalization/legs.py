"""Normalize Traveloka flight segments into Contract V1 legs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from cheapy.models import FlightLegV1


def normalize_leg(segment: object) -> FlightLegV1:
    if not isinstance(segment, Mapping):
        raise ValueError("segment must be a mapping")
    departure_time = _iso_datetime(
        _required_value(
            segment,
            "departureTime",
            "departureDateTime",
            "departure_datetime",
        )
    )
    arrival_time = _iso_datetime(
        _required_value(
            segment,
            "arrivalTime",
            "arrivalDateTime",
            "arrival_datetime",
        )
    )
    return FlightLegV1(
        origin=_string_value(
            _required_value(
                segment,
                "origin",
                "originCode",
                "departureAirport",
                "departureAirportCode",
            )
        ),
        destination=_string_value(
            _required_value(
                segment,
                "destination",
                "destinationCode",
                "arrivalAirport",
                "arrivalAirportCode",
            )
        ),
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=_string_value(
            _required_value(segment, "airlineCode", "carrierCode", "airline")
        ),
        flight_number=_string_value(
            _required_value(segment, "flightNumber", "flightNo", "number")
        ),
        duration_minutes=_duration_minutes(segment, departure_time, arrival_time),
    )


def _duration_minutes(
    segment: Mapping[str, object],
    departure_time: str,
    arrival_time: str,
) -> int:
    for key in ("durationMinutes", "durationInMinutes", "duration"):
        if key in segment:
            return int(segment[key])
    departure = datetime.fromisoformat(departure_time)
    arrival = datetime.fromisoformat(arrival_time)
    return int((arrival - departure).total_seconds() // 60)


def _required_value(segment: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in segment:
            value = segment[key]
            if value is not None:
                return value
    raise ValueError("segment field is missing")


def _string_value(value: object) -> str:
    enum_name = getattr(value, "name", None)
    if isinstance(enum_name, str):
        if len(enum_name) > 1 and enum_name.startswith("_") and enum_name[1].isdigit():
            return enum_name[1:]
        return enum_name
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _iso_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    return str(value)
