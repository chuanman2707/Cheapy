from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_built_wheel_can_load_packaged_airport_data(tmp_path: Path) -> None:
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
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    script = """
from importlib.resources import files
import json

base = files("cheapy").joinpath("data")
airports = json.loads(base.joinpath("airports.v1.json").read_text(encoding="utf-8"))
hubs = json.loads(base.joinpath("hubs.v1.json").read_text(encoding="utf-8"))
readme = base.joinpath("README.md").read_text(encoding="utf-8")

assert airports["version"] == 1
assert hubs["version"] == 1
assert "OurAirports" in readme
"""
    subprocess.run([str(python), "-c", script], check=True)
