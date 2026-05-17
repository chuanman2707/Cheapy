from __future__ import annotations

from cheapy.providers.traveloka.normalization.entrypoints import (
    normalize_payload,
    normalize_selected_round_trip,
)


def test_normalization_entrypoints_are_importable() -> None:
    assert callable(normalize_payload)
    assert callable(normalize_selected_round_trip)
