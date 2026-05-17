from __future__ import annotations

import importlib


def test_direct_normalizer_import_has_normalize_payload() -> None:
    normalizer = importlib.import_module("cheapy.providers.traveloka.normalizer")

    assert hasattr(normalizer, "normalize_payload")


def test_normalization_entrypoints_are_importable() -> None:
    from cheapy.providers.traveloka.normalization.entrypoints import (
        normalize_payload,
        normalize_selected_round_trip,
    )

    assert callable(normalize_payload)
    assert callable(normalize_selected_round_trip)
