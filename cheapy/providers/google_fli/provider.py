"""Google Fli live provider."""

from __future__ import annotations

from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult


class GoogleFliProvider:
    """Live provider backed by upstream fli."""

    name = "google_fli"
    capabilities = ("exact_one_way",)

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        raise NotImplementedError("google_fli provider implementation is added in Task 4")


def create_provider() -> GoogleFliProvider:
    return GoogleFliProvider()
