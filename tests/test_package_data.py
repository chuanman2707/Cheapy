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
    assert "cheapy/data/airports.v1.json" in names
    assert "cheapy/data/hubs.v1.json" in names
    assert "cheapy/data/README.md" in names
    assert "cheapy/providers/manual_fixture/manifest.toml" in names

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--offline", str(wheel)],
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
manifest = files("cheapy.providers").joinpath("manual_fixture", "manifest.toml").read_text(encoding="utf-8")

assert airports["version"] == 1
assert hubs["version"] == 1
assert "OurAirports" in readme
assert 'name = "manual_fixture"' in manifest
"""
    subprocess.run([str(python), "-c", resource_script], check=True, cwd=tmp_path)

    list_result = subprocess.run(
        [str(python), "-m", "cheapy", "providers", "list"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert list_result.stderr == ""
    assert json.loads(list_result.stdout)["providers"][0]["name"] == "manual_fixture"

    test_result = subprocess.run(
        [str(python), "-m", "cheapy", "providers", "test"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert test_result.stderr == ""
    assert json.loads(test_result.stdout)["providers_tested"] == 1
