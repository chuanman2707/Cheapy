"""Skyscanner live provider."""

from __future__ import annotations


class SkyscannerProvider:
    name = "skyscanner"
    capabilities = ("exact_one_way", "exact_round_trip")

    async def search_exact_one_way(self, request):
        raise NotImplementedError("Skyscanner provider is not implemented yet.")

    async def search_exact_round_trip(self, request):
        raise NotImplementedError("Skyscanner provider is not implemented yet.")


def create_provider() -> SkyscannerProvider:
    return SkyscannerProvider()
