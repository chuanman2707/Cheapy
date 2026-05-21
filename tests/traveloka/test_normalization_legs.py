from __future__ import annotations

from cheapy.providers.traveloka.normalization.legs import normalize_leg


def test_normalize_leg_maps_required_segment_fields() -> None:
    leg = normalize_leg(
        {
            "origin": "SGN",
            "destination": "BKK",
            "departureTime": "2026-07-10T09:00:00",
            "arrivalTime": "2026-07-10T10:35:00",
            "airlineCode": "VJ",
            "flightNumber": "VJ801",
            "durationMinutes": 95,
        }
    )

    assert leg.origin == "SGN"
    assert leg.destination == "BKK"
    assert leg.duration_minutes == 95
