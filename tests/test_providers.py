from __future__ import annotations

import asyncio
import socket

import pytest
from pydantic import ValidationError

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    PassengersV1,
    ProviderStatusCode,
    Severity,
)
from cheapy.providers.manual_fixture.provider import create_provider
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers import registry
from cheapy.providers.registry import (
    ProviderManifest,
    ProviderManifestError,
    ProviderLoadError,
    discover_provider_manifests,
)


def _manifest_by_name(name: str) -> ProviderManifest:
    return next(
        manifest
        for manifest in discover_provider_manifests()
        if manifest.name == name
    )


def test_provider_exact_one_way_request_defaults_to_one_adult() -> None:
    request = ProviderExactOneWayRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
    )

    assert request.origin == "CXR"
    assert request.destination == "SGN"
    assert request.departure_date == "2026-07-10"
    assert request.passengers == PassengersV1()


def test_provider_exact_one_way_request_rejects_non_iso_date_shape() -> None:
    with pytest.raises(ValidationError):
        ProviderExactOneWayRequest(
            origin="CXR",
            destination="SGN",
            departure_date="2026-7-10",
        )


def test_provider_result_reuses_contract_error_models() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="No manual fixture exists for the requested route/date.",
        details={
            "provider": "manual_fixture",
            "capability": "exact_one_way",
            "origin": "HAN",
            "destination": "SGN",
            "departure_date": "2026-07-10",
        },
        retryable=False,
    )

    result = ProviderResult(
        provider_name="manual_fixture",
        capability="exact_one_way",
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[error],
        duration_ms=0,
        retryable=False,
    )

    assert result.provider_name == "manual_fixture"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors == [error]


def test_provider_result_accepts_status_string_from_parsed_dict() -> None:
    result = ProviderResult.model_validate(
        {
            "provider_name": "manual_fixture",
            "capability": "exact_one_way",
            "status": "failed",
            "offers": [],
            "warnings": [],
            "errors": [],
            "duration_ms": 0,
            "retryable": False,
        }
    )

    assert result.status == ProviderStatusCode.FAILED


def test_manual_fixture_manifest_is_discovered_from_package_resources() -> None:
    manifests = discover_provider_manifests()

    assert "manual_fixture" in [manifest.name for manifest in manifests]
    manifest = _manifest_by_name("manual_fixture")
    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="manual_fixture",
        display_name="Manual fixture provider",
        default_enabled=True,
        provider_kind="fixture",
        module="cheapy.providers.manual_fixture.provider",
        capabilities=["exact_one_way"],
    )


def test_provider_manifests_include_provider_kind() -> None:
    manifests = discover_provider_manifests()
    kinds_by_name = {manifest.name: manifest.provider_kind for manifest in manifests}

    assert kinds_by_name["manual_fixture"] == "fixture"
    assert kinds_by_name["google_fli"] == "live"


def test_google_fli_manifest_is_discovered_from_package_resources() -> None:
    manifest = _manifest_by_name("google_fli")

    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="google_fli",
        display_name="Google Fli live provider",
        default_enabled=True,
        provider_kind="live",
        module="cheapy.providers.google_fli.provider",
        capabilities=["exact_one_way"],
    )


def test_registry_exposes_exact_one_way_as_stable_capability() -> None:
    manifest = _manifest_by_name("manual_fixture")

    assert manifest.capabilities == ["exact_one_way"]


def test_discover_provider_manifests_wraps_malformed_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeManifestResource:
        def is_file(self) -> bool:
            return True

        def read_text(self, encoding: str) -> str:
            return "[manifest"

    class FakeProviderResource:
        name = "broken_provider"

        def is_dir(self) -> bool:
            return True

        def joinpath(self, name: str) -> FakeManifestResource:
            assert name == "manifest.toml"
            return FakeManifestResource()

    class FakeRootResource:
        def iterdir(self) -> list[FakeProviderResource]:
            return [FakeProviderResource()]

    monkeypatch.setattr(registry, "_provider_resource_root", FakeRootResource)

    with pytest.raises(
        ProviderManifestError,
        match="Invalid provider manifest for 'broken_provider'",
    ):
        discover_provider_manifests()


def test_discover_provider_manifests_requires_provider_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeManifestResource:
        def is_file(self) -> bool:
            return True

        def read_text(self, encoding: str) -> str:
            return (
                'manifest_schema_version = "1"\n'
                'name = "broken_provider"\n'
                'display_name = "Broken provider"\n'
                "default_enabled = true\n"
                'module = "broken.provider"\n'
                'capabilities = ["exact_one_way"]\n'
            )

    class FakeProviderResource:
        name = "broken_provider"

        def is_dir(self) -> bool:
            return True

        def joinpath(self, name: str) -> FakeManifestResource:
            assert name == "manifest.toml"
            return FakeManifestResource()

    class FakeRootResource:
        def iterdir(self) -> list[FakeProviderResource]:
            return [FakeProviderResource()]

    monkeypatch.setattr(registry, "_provider_resource_root", FakeRootResource)

    with pytest.raises(
        ProviderManifestError,
        match="Invalid provider manifest for 'broken_provider'",
    ):
        discover_provider_manifests()


def test_manual_fixture_returns_two_valid_offers_for_fixture_route() -> None:
    provider = create_provider()
    request = ProviderExactOneWayRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
    )

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.provider_name == "manual_fixture"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert len(result.offers) == 2
    assert [offer.provider for offer in result.offers] == [
        "manual_fixture",
        "manual_fixture",
    ]
    assert [offer.global_rank for offer in result.offers] == [1, 2]
    assert all(offer.fare_details_status == "not_collected" for offer in result.offers)
    assert all(offer.flags.baggage_unknown is True for offer in result.offers)


def test_manual_fixture_returns_controlled_failure_for_unsupported_input() -> None:
    provider = create_provider()
    request = ProviderExactOneWayRequest(
        origin="HAN",
        destination="SGN",
        departure_date="2026-07-10",
    )

    result = asyncio.run(provider.search_exact_one_way(request))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.code == ErrorCode.PROVIDER_FAILED
    assert error.severity == Severity.ERROR
    assert error.message_en == "No manual fixture exists for the requested route/date."
    assert error.details == {
        "provider": "manual_fixture",
        "capability": "exact_one_way",
        "origin": "HAN",
        "destination": "SGN",
        "departure_date": "2026-07-10",
    }
    assert error.retryable is False


def test_manual_fixture_does_not_open_network_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_on_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("manual_fixture provider must not open network sockets")

    provider = create_provider()
    success_request = ProviderExactOneWayRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
    )
    unsupported_request = ProviderExactOneWayRequest(
        origin="HAN",
        destination="SGN",
        departure_date="2026-07-10",
    )

    loop = asyncio.new_event_loop()
    try:
        monkeypatch.setattr(socket, "socket", raise_on_socket)
        success_result = loop.run_until_complete(
            provider.search_exact_one_way(success_request)
        )
        unsupported_result = loop.run_until_complete(
            provider.search_exact_one_way(unsupported_request)
        )
    finally:
        loop.close()

    assert success_result.status == ProviderStatusCode.SUCCESS
    assert unsupported_result.status == ProviderStatusCode.FAILED


def test_load_provider_wraps_missing_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = ProviderManifest(
        manifest_schema_version="1",
        name="broken_provider",
        display_name="Broken provider",
        default_enabled=True,
        provider_kind="live",
        module="broken.provider",
        capabilities=["exact_one_way"],
    )

    monkeypatch.setattr(
        registry,
        "import_module",
        lambda module_name: object(),
    )

    with pytest.raises(
        ProviderLoadError,
        match="Unable to load provider 'broken_provider'",
    ):
        registry.load_provider(manifest)


def test_load_provider_rejects_bad_provider_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProviderModule:
        @staticmethod
        def create_provider() -> object:
            return object()

    manifest = ProviderManifest(
        manifest_schema_version="1",
        name="bad_shape_provider",
        display_name="Bad shape provider",
        default_enabled=True,
        provider_kind="live",
        module="bad_shape.provider",
        capabilities=["exact_one_way"],
    )

    monkeypatch.setattr(
        registry,
        "import_module",
        lambda module_name: FakeProviderModule,
    )

    with pytest.raises(
        ProviderLoadError,
        match="Unable to load provider 'bad_shape_provider'",
    ):
        registry.load_provider(manifest)


def test_load_enabled_providers_loads_all_default_enabled_providers() -> None:
    from cheapy.providers.registry import load_enabled_providers

    providers = load_enabled_providers()

    assert [provider.name for provider in providers] == [
        "google_fli",
        "manual_fixture",
    ]
    assert providers[0].capabilities == ("exact_one_way",)
    assert providers[1].capabilities == ("exact_one_way",)


def test_load_search_providers_excludes_fixture_providers() -> None:
    providers = registry.load_search_providers()

    assert [provider.name for provider in providers] == ["google_fli"]
    assert all(provider.name != "manual_fixture" for provider in providers)
