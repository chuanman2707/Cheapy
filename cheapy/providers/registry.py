"""Packaged provider manifest discovery."""

from __future__ import annotations

from importlib import import_module
from importlib.resources import files
from typing import Any, Literal, cast
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cheapy.providers.base import FlightProvider


class ProviderRegistryError(RuntimeError):
    """Base provider registry error."""


class ProviderManifestError(ProviderRegistryError):
    """Raised when a packaged provider manifest is invalid."""


class ProviderLoadError(ProviderRegistryError):
    """Raised when a packaged provider cannot be loaded."""


class ProviderManifest(BaseModel):
    """Validated provider manifest loaded from package resources."""

    model_config = ConfigDict(extra="forbid", strict=True)

    manifest_schema_version: Literal["1"]
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    default_enabled: bool
    provider_kind: Literal["live", "fixture"]
    module: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)


def _provider_resource_root():
    return files("cheapy.providers")


def discover_provider_manifests() -> list[ProviderManifest]:
    """Discover bundled provider manifests from package resources."""
    manifests: list[ProviderManifest] = []
    root = _provider_resource_root()

    for child in sorted(root.iterdir(), key=lambda resource: resource.name):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        manifest_resource = child.joinpath("manifest.toml")
        if not manifest_resource.is_file():
            continue
        try:
            data = tomllib.loads(manifest_resource.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ProviderManifestError(
                f"Invalid provider manifest for {child.name!r}"
            ) from exc
        try:
            manifest = ProviderManifest.model_validate(data)
        except ValidationError as exc:
            raise ProviderManifestError(
                f"Invalid provider manifest for {child.name!r}"
            ) from exc
        manifests.append(manifest)

    return manifests


def load_provider(manifest: ProviderManifest) -> FlightProvider:
    """Load a provider object from a validated bundled manifest."""
    try:
        module = import_module(manifest.module)
        factory: Any = getattr(module, "create_provider")
        if not callable(factory):
            raise TypeError("Provider factory is not callable")
        provider = factory()
    except Exception as exc:
        raise ProviderLoadError(_provider_load_error_message(manifest)) from exc

    return _validate_provider_shape(provider, manifest)


def _validate_provider_shape(
    provider: object,
    manifest: ProviderManifest,
) -> FlightProvider:
    name = getattr(provider, "name", None)
    capabilities = getattr(provider, "capabilities", None)

    if not isinstance(name, str) or not isinstance(capabilities, tuple):
        raise ProviderLoadError(_provider_load_error_message(manifest))

    required_methods = {
        "exact_one_way": "search_exact_one_way",
        "exact_round_trip": "search_exact_round_trip",
    }
    for capability in manifest.capabilities:
        method_name = required_methods.get(capability)
        if method_name is not None and not callable(getattr(provider, method_name, None)):
            raise ProviderLoadError(_provider_load_error_message(manifest))

    return cast(FlightProvider, provider)


def _provider_load_error_message(manifest: ProviderManifest) -> str:
    return f"Unable to load provider {manifest.name!r}"


def load_enabled_providers() -> list[FlightProvider]:
    """Load all bundled providers enabled by default."""
    return [
        load_provider(manifest)
        for manifest in discover_provider_manifests()
        if manifest.default_enabled
    ]


def load_search_providers() -> list[FlightProvider]:
    """Load bundled providers enabled for normal user-facing search."""
    return [
        load_provider(manifest)
        for manifest in discover_provider_manifests()
        if manifest.default_enabled and manifest.provider_kind != "fixture"
    ]
