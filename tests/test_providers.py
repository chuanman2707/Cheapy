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
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers import registry
from cheapy.providers.registry import (
    ProviderManifest,
    ProviderManifestError,
    ProviderLoadError,
    discover_provider_manifests,
)
from cheapy.providers.skyscanner.adapter import SkyscannerProviderError
from cheapy.providers.skyscanner.provider import (
    SkyscannerProvider,
    create_provider as create_skyscanner_provider,
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


def test_provider_exact_one_way_request_defaults_requested_fields() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-11",
    )

    assert request.requested_origin == "SGN"
    assert request.requested_destination == "BKK"
    assert request.requested_departure_date == "2026-07-11"


def test_provider_exact_one_way_request_accepts_flexible_actual_date() -> None:
    request = ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-12",
        requested_origin="SGN",
        requested_destination="BKK",
        requested_departure_date="2026-07-10",
    )

    assert request.departure_date == "2026-07-12"
    assert request.requested_departure_date == "2026-07-10"


def test_provider_exact_round_trip_request_defaults_requested_fields() -> None:
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )

    assert request.origin == "SGN"
    assert request.destination == "BKK"
    assert request.departure_date == "2026-07-10"
    assert request.return_date == "2026-07-17"
    assert request.requested_origin == "SGN"
    assert request.requested_destination == "BKK"
    assert request.requested_departure_date == "2026-07-10"
    assert request.requested_return_date == "2026-07-17"
    assert request.passengers == PassengersV1()


def test_provider_exact_round_trip_request_rejects_return_before_departure() -> None:
    with pytest.raises(ValidationError, match="return_date must not be earlier"):
        ProviderExactRoundTripRequest(
            origin="SGN",
            destination="BKK",
            departure_date="2026-07-10",
            return_date="2026-07-09",
        )


def test_provider_exact_round_trip_request_rejects_requested_return_before_requested_departure() -> None:
    with pytest.raises(ValidationError, match="requested_return_date must not be earlier"):
        ProviderExactRoundTripRequest(
            origin="SGN",
            destination="BKK",
            departure_date="2026-07-10",
            return_date="2026-07-17",
            requested_departure_date="2026-07-10",
            requested_return_date="2026-07-09",
        )


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
    assert kinds_by_name["traveloka"] == "live"
    assert kinds_by_name["skyscanner"] == "live"


def test_google_fli_manifest_is_discovered_from_package_resources() -> None:
    manifest = _manifest_by_name("google_fli")

    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="google_fli",
        display_name="Google Fli live provider",
        default_enabled=True,
        provider_kind="live",
        module="cheapy.providers.google_fli.provider",
        capabilities=["exact_one_way", "exact_round_trip"],
    )


def test_traveloka_manifest_is_discovered_from_package_resources() -> None:
    manifest = _manifest_by_name("traveloka")

    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="traveloka",
        display_name="Traveloka research provider",
        default_enabled=True,
        provider_kind="live",
        module="cheapy.providers.traveloka.provider",
        capabilities=["exact_one_way", "exact_round_trip"],
    )


def test_skyscanner_manifest_is_discovered_from_package_resources() -> None:
    manifest = _manifest_by_name("skyscanner")

    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="skyscanner",
        display_name="Skyscanner live provider",
        default_enabled=True,
        provider_kind="live",
        module="cheapy.providers.skyscanner.provider",
        capabilities=["exact_one_way", "exact_round_trip"],
    )


def test_search_providers_include_skyscanner_live_provider() -> None:
    providers = {provider.name: provider for provider in registry.load_search_providers()}

    assert set(providers) >= {"google_fli", "traveloka", "skyscanner"}
    assert providers["skyscanner"].capabilities == (
        "exact_one_way",
        "exact_round_trip",
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


def test_manual_fixture_returns_controlled_failure_for_round_trip_request() -> None:
    provider = create_provider()
    request = ProviderExactRoundTripRequest(
        origin="CXR",
        destination="SGN",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )

    result = asyncio.run(provider.search_exact_round_trip(request))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.code == ErrorCode.PROVIDER_FAILED
    assert error.severity == Severity.ERROR
    assert error.message_en == "No manual fixture exists for round-trip requests."
    assert error.details == {
        "provider": "manual_fixture",
        "capability": "exact_round_trip",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": "2026-07-17",
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


def test_load_provider_rejects_provider_capability_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProvider:
        name = "mismatch_provider"
        capabilities = ("exact_one_way", "exact_round_trip")

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            raise NotImplementedError

        async def search_exact_round_trip(
            self,
            request: ProviderExactRoundTripRequest,
        ) -> ProviderResult:
            raise NotImplementedError

    class FakeProviderModule:
        @staticmethod
        def create_provider() -> FakeProvider:
            return FakeProvider()

    manifest = ProviderManifest(
        manifest_schema_version="1",
        name="mismatch_provider",
        display_name="Mismatch provider",
        default_enabled=True,
        provider_kind="live",
        module="mismatch.provider",
        capabilities=["exact_one_way"],
    )

    monkeypatch.setattr(
        registry,
        "import_module",
        lambda module_name: FakeProviderModule,
    )

    with pytest.raises(
        ProviderLoadError,
        match="Unable to load provider 'mismatch_provider'",
    ):
        registry.load_provider(manifest)


def test_load_provider_rejects_one_way_provider_missing_round_trip_protocol_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProvider:
        name = "one_way_missing_round_trip_method"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            raise NotImplementedError

    class FakeProviderModule:
        @staticmethod
        def create_provider() -> FakeProvider:
            return FakeProvider()

    manifest = ProviderManifest(
        manifest_schema_version="1",
        name="one_way_missing_round_trip_method",
        display_name="One-way missing round-trip method",
        default_enabled=True,
        provider_kind="live",
        module="oneway.missing.method",
        capabilities=["exact_one_way"],
    )

    monkeypatch.setattr(
        registry,
        "import_module",
        lambda module_name: FakeProviderModule,
    )

    with pytest.raises(
        ProviderLoadError,
        match="Unable to load provider 'one_way_missing_round_trip_method'",
    ):
        registry.load_provider(manifest)


def test_load_provider_rejects_round_trip_capability_without_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProvider:
        name = "missing_round_trip_method"
        capabilities = ("exact_one_way", "exact_round_trip")

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            raise NotImplementedError

    class FakeProviderModule:
        @staticmethod
        def create_provider() -> FakeProvider:
            return FakeProvider()

    manifest = ProviderManifest(
        manifest_schema_version="1",
        name="missing_round_trip_method",
        display_name="Missing round-trip method",
        default_enabled=True,
        provider_kind="live",
        module="missing.method",
        capabilities=["exact_one_way", "exact_round_trip"],
    )

    monkeypatch.setattr(
        registry,
        "import_module",
        lambda module_name: FakeProviderModule,
    )

    with pytest.raises(
        ProviderLoadError,
        match="Unable to load provider 'missing_round_trip_method'",
    ):
        registry.load_provider(manifest)


def test_google_fli_round_trip_missing_adapter_method_returns_controlled_failure() -> None:
    from cheapy.providers.google_fli.provider import GoogleFliProvider

    provider = GoogleFliProvider(adapter=object())
    request = ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )

    result = asyncio.run(provider.search_exact_round_trip(request))

    assert result.provider_name == "google_fli"
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details["provider"] == "google_fli"
    assert result.errors[0].details["capability"] == "exact_round_trip"
    assert result.errors[0].details["failure_type"] == "unexpected_error"


class _FailingSkyscannerSessionManager:
    def __init__(self, error: SkyscannerProviderError) -> None:
        self.error = error
        self.calls = 0

    def config_for_call(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise self.error

    def clear_cache(self) -> None:
        return None


def _skyscanner_provider_with_cookie_bootstrap_failure() -> tuple[
    SkyscannerProvider,
    _FailingSkyscannerSessionManager,
]:
    manager = _FailingSkyscannerSessionManager(
        SkyscannerProviderError(
            failure_type="browser_cookie_unavailable",
            message_en="Skyscanner browser bootstrap did not produce usable cookies.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
        )
    )
    return SkyscannerProvider(env={}, session_manager=manager), manager


def test_skyscanner_provider_cookie_bootstrap_failure_returns_failed_one_way_result() -> None:
    provider, manager = _skyscanner_provider_with_cookie_bootstrap_failure()

    result = asyncio.run(
        provider.search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="SGN",
                destination="BKK",
                departure_date="2026-07-10",
            )
        )
    )

    assert manager.calls == 1
    assert result.provider_name == "skyscanner"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_one_way",
        "failure_type": "browser_cookie_unavailable",
    }
    assert result.retryable is True


def test_skyscanner_provider_cookie_bootstrap_failure_returns_failed_round_trip_result() -> None:
    provider, manager = _skyscanner_provider_with_cookie_bootstrap_failure()

    result = asyncio.run(
        provider.search_exact_round_trip(
            ProviderExactRoundTripRequest(
                origin="SGN",
                destination="BKK",
                departure_date="2026-07-10",
                return_date="2026-07-17",
            )
        )
    )

    assert manager.calls == 1
    assert result.provider_name == "skyscanner"
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "skyscanner",
        "capability": "exact_round_trip",
        "failure_type": "browser_cookie_unavailable",
    }
    assert result.retryable is True


def test_load_enabled_providers_loads_all_default_enabled_providers() -> None:
    from cheapy.providers.registry import load_enabled_providers

    providers = load_enabled_providers()

    assert [provider.name for provider in providers] == [
        "google_fli",
        "manual_fixture",
        "skyscanner",
        "traveloka",
    ]
    assert [provider.capabilities for provider in providers] == [
        ("exact_one_way", "exact_round_trip"),
        ("exact_one_way",),
        ("exact_one_way", "exact_round_trip"),
        ("exact_one_way", "exact_round_trip"),
    ]


def test_load_search_providers_excludes_fixture_providers() -> None:
    providers = registry.load_search_providers()

    assert [provider.name for provider in providers] == [
        "google_fli",
        "skyscanner",
        "traveloka",
    ]
    assert [provider.capabilities for provider in providers] == [
        ("exact_one_way", "exact_round_trip"),
        ("exact_one_way", "exact_round_trip"),
        ("exact_one_way", "exact_round_trip"),
    ]
    assert all(provider.name != "manual_fixture" for provider in providers)


def test_load_search_providers_loads_enabled_live_providers_and_excludes_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_manifest = ProviderManifest(
        manifest_schema_version="1",
        name="live_provider",
        display_name="Live provider",
        default_enabled=True,
        provider_kind="live",
        module="live.provider",
        capabilities=["exact_one_way"],
    )
    fixture_manifest = ProviderManifest(
        manifest_schema_version="1",
        name="fixture_provider",
        display_name="Fixture provider",
        default_enabled=True,
        provider_kind="fixture",
        module="fixture.provider",
        capabilities=["exact_one_way"],
    )

    class FakeProvider:
        def __init__(self, manifest: ProviderManifest) -> None:
            self.name = manifest.name
            self.capabilities = tuple(manifest.capabilities)

    loaded_provider_names: list[str] = []

    def fake_load_provider(manifest: ProviderManifest) -> FakeProvider:
        loaded_provider_names.append(manifest.name)
        return FakeProvider(manifest)

    monkeypatch.setattr(
        registry,
        "discover_provider_manifests",
        lambda: [live_manifest, fixture_manifest],
    )
    monkeypatch.setattr(registry, "load_provider", fake_load_provider)

    providers = registry.load_search_providers()

    assert [provider.name for provider in providers] == ["live_provider"]
    assert providers[0].capabilities == ("exact_one_way",)
    assert loaded_provider_names == ["live_provider"]
