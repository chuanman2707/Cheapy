from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def load_probe():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "skyscanner_http_probe.py"
    spec = importlib.util.spec_from_file_location("skyscanner_http_probe", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["skyscanner_http_probe"] = module
    spec.loader.exec_module(module)
    return module


probe = load_probe()


def test_normalize_iata_uppercases_and_strips() -> None:
    assert probe.normalize_iata(" han ") == "HAN"


@pytest.mark.parametrize("value", ["", "HA", "HANO", "H1N", "h@n"])
def test_normalize_iata_rejects_invalid_values(value: str) -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.normalize_iata(value)

    assert exc_info.value.code == "invalid_argument"


def test_date_parts_validates_and_formats_date() -> None:
    assert probe.date_parts("2026-06-11") == {
        "@type": "date",
        "year": "2026",
        "month": "06",
        "day": "11",
    }


@pytest.mark.parametrize("value", ["2026-6-11", "2026-02-30", "11-06-2026"])
def test_date_parts_rejects_invalid_dates(value: str) -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.date_parts(value)

    assert exc_info.value.code == "invalid_argument"


def test_require_cookie_rejects_missing_cookie() -> None:
    with pytest.raises(probe.ProbeError) as exc_info:
        probe.require_cookie({"CHEAPY_SKYSCANNER_COOKIE": ""})

    assert exc_info.value.code == "missing_cookie"
    assert "cookie" in exc_info.value.message.lower()


def test_default_config_from_env_uses_safe_defaults() -> None:
    config = probe.config_from_env(
        {"CHEAPY_SKYSCANNER_COOKIE": "abgroup=1; __Secure-anon_token=secret"},
        market="SG",
        locale="en-GB",
        currency="SGD",
    )

    assert config.base_url == "https://www.skyscanner.com.sg"
    assert config.market == "SG"
    assert config.locale == "en-GB"
    assert config.currency == "SGD"
    assert config.cookie.startswith("abgroup=1")
    assert config.timeout_seconds == 20.0
