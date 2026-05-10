from __future__ import annotations

import pytest
from pydantic import ValidationError

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    PassengersV1,
    ProviderStatusCode,
    Severity,
)
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderResult
from cheapy.providers import registry
from cheapy.providers.registry import (
    ProviderManifest,
    ProviderManifestError,
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
        module="cheapy.providers.manual_fixture.provider",
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


def test_registry_does_not_expose_provider_loaders_before_task_3() -> None:
    assert not hasattr(registry, "load_provider")
    assert not hasattr(registry, "load_enabled_providers")
