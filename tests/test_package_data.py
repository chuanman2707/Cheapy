from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_can_load_packaged_airport_and_provider_data(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--no-build-isolation",
            "--offline",
            "--no-index",
            "--out-dir",
            str(dist_dir),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_paths = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        assert len(metadata_paths) == 1
        metadata = archive.read(metadata_paths[0]).decode("utf-8")
    assert "cheapy/data/airports.v1.json" in names
    assert "cheapy/data/hubs.v1.json" in names
    assert "cheapy/data/README.md" in names
    assert "cheapy/providers/manual_fixture/manifest.toml" in names
    assert "cheapy/providers/google_fli/manifest.toml" in names
    assert "cheapy/providers/traveloka/manifest.toml" in names
    assert "cheapy/providers/skyscanner/__init__.py" in names
    assert "cheapy/providers/skyscanner/manifest.toml" in names
    assert "cheapy/providers/skyscanner/provider.py" in names
    assert "cheapy/providers/skyscanner/scan_graphql_bundles.py" in names
    assert "Requires-Dist: cloakbrowser>=0.3.26" in metadata
    assert "Requires-Dist: flights>=0.8.4" in metadata
    assert (
        "Requires-Dist: mcp<1.28,>=1.27.1" in metadata
        or "Requires-Dist: mcp>=1.27.1,<1.28" in metadata
    )
    assert "Requires-Dist: pydantic>=2.10" in metadata
    assert "Requires-Dist: tomlkit>=0.15.0" in metadata
    assert "Requires-Dist: typer>=0.15" in metadata

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    requirements_path = tmp_path / "runtime-requirements.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--offline",
            "--requirement",
            str(requirements_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--offline",
            "--no-deps",
            str(wheel),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    resource_script = """
from importlib.resources import files
import json

base = files("cheapy").joinpath("data")
airports = json.loads(base.joinpath("airports.v1.json").read_text(encoding="utf-8"))
hubs = json.loads(base.joinpath("hubs.v1.json").read_text(encoding="utf-8"))
readme = base.joinpath("README.md").read_text(encoding="utf-8")
manual_manifest = files("cheapy.providers").joinpath("manual_fixture", "manifest.toml").read_text(encoding="utf-8")
google_manifest = files("cheapy.providers").joinpath("google_fli", "manifest.toml").read_text(encoding="utf-8")
traveloka_manifest = files("cheapy.providers").joinpath("traveloka", "manifest.toml").read_text(encoding="utf-8")
skyscanner_manifest = files("cheapy.providers").joinpath("skyscanner", "manifest.toml").read_text(encoding="utf-8")
skyscanner_root = files("cheapy.providers").joinpath("skyscanner")
assert skyscanner_root.joinpath("__init__.py").is_file()
assert skyscanner_root.joinpath("provider.py").is_file()
assert skyscanner_root.joinpath("scan_graphql_bundles.py").is_file()

assert airports["version"] == 1
assert hubs["version"] == 1
assert "OurAirports" in readme
assert 'name = "manual_fixture"' in manual_manifest
assert 'provider_kind = "fixture"' in manual_manifest
assert 'name = "google_fli"' in google_manifest
assert 'provider_kind = "live"' in google_manifest
assert "default_enabled = true" in google_manifest
assert 'name = "traveloka"' in traveloka_manifest
assert 'provider_kind = "live"' in traveloka_manifest
assert "default_enabled = true" in traveloka_manifest
assert 'name = "skyscanner"' in skyscanner_manifest
assert 'provider_kind = "live"' in skyscanner_manifest
assert "default_enabled = true" in skyscanner_manifest
"""
    subprocess.run([str(python), "-c", resource_script], check=True, cwd=tmp_path)

    origin_script = f"""
from pathlib import Path
import cheapy

repo_package = Path({str(Path(__file__).parents[1] / "cheapy")!r}).resolve()
installed_package = Path(cheapy.__file__).resolve()
assert not installed_package.is_relative_to(repo_package), installed_package
"""
    subprocess.run([str(python), "-c", origin_script], check=True, cwd=tmp_path)

    list_result = subprocess.run(
        [str(python), "-m", "cheapy", "providers", "list"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
    )
    assert list_result.stderr == ""
    providers = {
        provider["name"]: provider
        for provider in json.loads(list_result.stdout)["providers"]
    }
    assert providers["manual_fixture"]["provider_kind"] == "fixture"
    assert providers["google_fli"]["provider_kind"] == "live"
    assert providers["google_fli"]["default_enabled"] is True
    assert providers["traveloka"]["provider_kind"] == "live"
    assert providers["traveloka"]["default_enabled"] is True
    assert providers["skyscanner"]["provider_kind"] == "live"
    assert providers["skyscanner"]["default_enabled"] is True

    test_result = subprocess.run(
        [str(python), "-m", "cheapy", "providers", "test"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
    )
    assert test_result.stderr == ""
    test_payload = json.loads(test_result.stdout)
    providers = {provider["name"]: provider for provider in test_payload["providers"]}
    assert providers["manual_fixture"]["status"] == "success"
    assert providers["google_fli"]["live_smoke"] == "not_run"
    assert providers["skyscanner"]["live_smoke"] == "not_run"
    assert providers["traveloka"]["live_smoke"] == "not_run"
