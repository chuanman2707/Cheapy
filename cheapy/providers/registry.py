"""Packaged provider manifest discovery."""

from __future__ import annotations

from importlib.resources import files
from typing import Literal
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ProviderRegistryError(RuntimeError):
    """Base provider registry error."""


class ProviderManifestError(ProviderRegistryError):
    """Raised when a packaged provider manifest is invalid."""


class ProviderManifest(BaseModel):
    """Validated provider manifest loaded from package resources."""

    model_config = ConfigDict(extra="forbid", strict=True)

    manifest_schema_version: Literal["1"]
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    default_enabled: bool
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
