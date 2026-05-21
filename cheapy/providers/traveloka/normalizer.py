"""Compatibility imports for Traveloka normalization entrypoints."""

from __future__ import annotations

from cheapy.providers.traveloka.normalization.entrypoints import normalize_payload
from cheapy.providers.traveloka.normalization.selected import (
    normalize_selected_round_trip,
)

__all__ = ["normalize_payload", "normalize_selected_round_trip"]
