from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest
from cheapy.providers.traveloka.normalization.errors import parse_error


def test_parse_error_uses_safe_exception_type_only() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )

    error = parse_error(3, request, ValueError("contains raw payload"))

    assert error.details["provider"] == "traveloka"
    assert error.details["failure_type"] == "parse_error"
    assert error.details["item_index"] == 3
    assert error.details["exception_type"] == "ValueError"
    assert "raw payload" not in error.message_en
