from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cheapy.models import SearchRequestV1, SearchResponseV1, SearchStatus, WarningCode
from cheapy.search_service import search_with_storage
from cheapy.storage import sqlite as storage


def _request() -> SearchRequestV1:
    return SearchRequestV1.model_validate(
        {
            "schema_version": "1",
            "origin": "CXR",
            "destination": "SGN",
            "departure_date": "2026-07-10",
            "return_date": None,
            "search_mode": "exact",
            "max_results": 5,
        }
    )


def _response() -> SearchResponseV1:
    return SearchResponseV1.model_validate(
        {
            "schema_version": "1",
            "status": "success",
            "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
            "offers": [],
            "warnings": [],
            "errors": [],
            "provider_statuses": [],
            "search_plan": {
                "search_mode": "exact",
                "planned_candidate_count": 1,
                "executed_candidate_count": 1,
                "planned_provider_call_count": 1,
                "executed_provider_call_count": 1,
                "candidate_count_by_family": {"exact": 1},
                "provider_call_count_by_family": {"exact": 1},
                "truncated": False,
                "truncated_families": [],
                "candidate_families": ["exact"],
            },
            "mixed_currency": False,
            "currency_groups": [],
            "currency_notes": [],
            "candidates": None,
        }
    )


def test_search_with_storage_persists_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    monkeypatch.setattr("cheapy.search_service.search_exact", lambda request: _response())

    result = search_with_storage(_request())

    assert result.search_run_id is not None
    assert result.storage_enabled is True
    assert result.storage_warning is None
    assert result.response.status == SearchStatus.SUCCESS
    with storage.open_database() as conn:
        shown = storage.show_history(conn, result.search_run_id)
    assert shown is not None
    assert shown["search_run"]["request_id"] == result.response.request_id


def test_search_with_storage_disable_skips_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "cheapy.sqlite3"
    monkeypatch.setenv("CHEAPY_DB_PATH", str(db_path))
    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "1")
    monkeypatch.setattr("cheapy.search_service.search_exact", lambda request: _response())

    result = search_with_storage(_request())

    assert result.search_run_id is None
    assert result.storage_enabled is False
    assert result.storage_warning is None
    assert db_path.exists() is False


def test_search_with_storage_failure_adds_safe_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_storage_error(*args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("path /tmp/secret.sqlite3 failed")

    monkeypatch.delenv("CHEAPY_DISABLE_STORAGE", raising=False)
    monkeypatch.setattr("cheapy.search_service.search_exact", lambda request: _response())
    monkeypatch.setattr("cheapy.search_service._persist_response", raise_storage_error)

    result = search_with_storage(_request())

    assert result.search_run_id is None
    assert result.storage_enabled is True
    assert result.storage_warning is not None
    assert result.storage_warning.code == WarningCode.LOCAL_STORAGE_FAILED
    assert result.response.warnings[-1].code == WarningCode.LOCAL_STORAGE_FAILED
    assert result.response.warnings[-1].details == {
        "storage_backend": "sqlite",
        "exception_type": "OperationalError",
    }
    assert "secret" not in result.response.model_dump_json()
