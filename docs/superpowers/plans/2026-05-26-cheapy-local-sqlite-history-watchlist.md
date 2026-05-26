# Cheapy Local SQLite History And Watchlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local-only SQLite search history and manual watchlists for Cheapy without storing provider/browser internals or making search fail when storage fails.

**Architecture:** Keep `cheapy.search.search_exact()` provider orchestration clean and add a thin `cheapy.search_service.search_with_storage()` wrapper that persists sanitized Contract V1 snapshots. Put all SQLite path, migration, history, watchlist, and snapshot logic in `cheapy.storage.sqlite`; keep watchlist decision logic in a small pure module so the CLI stays thin. MCP returns only Contract V1 responses, while CLI commands use storage metadata such as `search_run_id`.

**Tech Stack:** Python 3.12+, stdlib `sqlite3`, Pydantic Contract V1 models, Typer CLI, FastMCP, pytest, uv.

---

## Commit Policy

The repository may have unrelated dirty worktree changes. Before each commit, run `git diff --cached --name-status` and confirm only files from that task are staged. Do not revert or reformat unrelated files.

Recommended commit body for AI commits:

```text
AI-Generated-By: GPT-5 Codex
```

## Reference Material

- Project instructions: `AGENTS.md`
- Cheapy skill: `.codex/skills/cheapy/SKILL.md`
- Design spec: `docs/superpowers/specs/2026-05-25-cheapy-local-sqlite-history-watchlist-design.md`
- Contract source of truth: `cheapy/models/contracts.py`
- Search orchestration: `cheapy/search.py`
- MCP tool registration: `cheapy/mcp.py`
- CLI entrypoint: `cheapy/cli.py`
- Existing CLI tests: `tests/test_cli.py`
- Existing MCP tests: `tests/test_mcp.py`

## Scope Check

This feature touches one product surface: local persistence and manual watchlists. It spans Contract V1 warning enum, SQLite storage, a search wrapper, MCP wiring, CLI commands, and tests. Do not add cron, notifications, hosted backend, sync, real provider behavior, provider-internal persistence, Browserless, or live network calls.

## File Structure

Create:

- `cheapy/storage/__init__.py`: public storage exports.
- `cheapy/storage/sqlite.py`: SQLite path resolution, migrations, sanitized snapshot inserts, history queries, watchlist CRUD, watchlist check records, storage-disabled checks.
- `cheapy/search_service.py`: `SearchWithStorageResult` and `search_with_storage()`.
- `cheapy/watchlist.py`: pure watchlist request-building, best-offer selection, provider-confidence, and decision/rationale logic.
- `tests/storage/__init__.py`: storage test package marker.
- `tests/storage/test_sqlite.py`: path, migration, snapshot, sanitizer, fingerprint, history, watchlist storage tests.
- `tests/test_search_service.py`: storage wrapper behavior tests.
- `tests/test_watchlist.py`: pure watchlist decision tests.

Modify:

- `cheapy/models/contracts.py`: add `WarningCode.LOCAL_STORAGE_FAILED`.
- `cheapy/mcp.py`: call `search_with_storage()` and update tool annotations.
- `cheapy/cli.py`: add `history` and `watchlist` subcommands.
- `tests/test_contracts.py`: assert warning code accepts `local_storage_failed`.
- `tests/test_schema_export.py`: assert schema includes the new warning enum value.
- `tests/test_mcp.py`: update fake search patching, annotation assertions, persistence/disable/failure tests.
- `tests/test_cli.py`: add history/watchlist CLI tests.

Do not modify provider modules for this feature.

## Shared Test Helpers

Several tasks need deterministic Contract V1 objects. Use local helpers in each test file instead of importing private helpers across tests.

Use this request helper where needed:

```python
from cheapy.models import SearchRequestV1


def _request(**overrides: object) -> SearchRequestV1:
    data: dict[str, object] = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "max_results": 5,
    }
    data.update(overrides)
    return SearchRequestV1.model_validate(data)
```

Use this response helper where needed:

```python
from cheapy.models import SearchResponseV1


def _response(**overrides: object) -> SearchResponseV1:
    data: dict[str, object] = {
        "schema_version": "1",
        "status": "success",
        "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
        "offers": [
            {
                "offer_id": "fixture:1",
                "price_amount": 1280000.0,
                "currency": "VND",
                "comparable": True,
                "rank_within_currency": 1,
                "global_rank": 1,
                "provider": "manual_fixture",
                "requested_origin": "CXR",
                "requested_destination": "SGN",
                "actual_origin": "CXR",
                "actual_destination": "SGN",
                "nearby_origin_distance_km": None,
                "nearby_destination_distance_km": None,
                "requested_departure_date": "2026-07-10",
                "actual_departure_date": "2026-07-10",
                "departure_offset_days": 0,
                "requested_return_date": None,
                "actual_return_date": None,
                "return_offset_days": None,
                "legs": [
                    {
                        "origin": "CXR",
                        "destination": "SGN",
                        "departure_time": "2026-07-10T08:15:00",
                        "arrival_time": "2026-07-10T09:25:00",
                        "airline_code": "VJ",
                        "flight_number": "VJ601",
                        "duration_minutes": 70,
                    }
                ],
                "total_duration_minutes": 70,
                "stops": 0,
                "flags": {
                    "is_split_ticket": False,
                    "is_self_transfer": False,
                    "uses_nearby_origin": False,
                    "uses_nearby_destination": False,
                    "uses_flexible_departure_date": False,
                    "uses_flexible_return_date": False,
                    "has_long_connection": False,
                    "has_overnight_connection": False,
                    "has_many_stops": False,
                    "baggage_unknown": True,
                },
                "fare_details_status": "not_collected",
            }
        ],
        "warnings": [],
        "errors": [],
        "provider_statuses": [
            {
                "provider_name": "manual_fixture",
                "capability": "exact_one_way",
                "status": "success",
                "planned_call_count": 1,
                "executed_call_count": 1,
                "succeeded_call_count": 1,
                "failed_call_count": 0,
                "duration_ms": 3,
                "warnings": [],
                "errors": [],
                "retryable": False,
            }
        ],
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
        "currency_groups": [{"currency": "VND", "offer_ids": ["fixture:1"]}],
        "currency_notes": [],
        "candidates": None,
    }
    data.update(overrides)
    return SearchResponseV1.model_validate(data)
```

## Task 0: Preflight

**Files:**
- Read: `AGENTS.md`
- Read: `.codex/skills/cheapy/SKILL.md`
- Read: `docs/superpowers/specs/2026-05-25-cheapy-local-sqlite-history-watchlist-design.md`
- Read: `cheapy/models/contracts.py`
- Read: `cheapy/mcp.py`
- Read: `cheapy/cli.py`

- [ ] **Step 1: Confirm worktree state**

Run:

```bash
git status --short
```

Expected: the worktree may contain unrelated modified and untracked files. Record that state in task notes and do not revert unrelated changes.

- [ ] **Step 2: Run focused baseline tests**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_cli.py tests/test_schema_export.py tests/test_mcp.py -v
```

Expected: PASS, or failures that are clearly pre-existing from unrelated WIP. If baseline fails, record exact failing tests before editing.

## Task 1: Add Contract V1 Storage Warning

**Files:**
- Modify: `cheapy/models/contracts.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_schema_export.py`

- [ ] **Step 1: Write failing contract tests**

Append to `tests/test_contracts.py`:

```python
def test_warning_accepts_local_storage_failed_code() -> None:
    warning = WarningV1(
        code="local_storage_failed",
        severity=Severity.WARNING,
        message_en="Local search history could not be saved.",
        details={"storage_backend": "sqlite", "exception_type": "OperationalError"},
        retryable=False,
    )

    assert warning.code == WarningCode.LOCAL_STORAGE_FAILED
    assert warning.details == {
        "storage_backend": "sqlite",
        "exception_type": "OperationalError",
    }
```

Append to `tests/test_schema_export.py`:

```python
def test_schema_exports_local_storage_warning_code() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    exported = json.loads(result.output)
    assert "local_storage_failed" in json.dumps(exported["SearchResponseV1"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_contracts.py::test_warning_accepts_local_storage_failed_code tests/test_schema_export.py::test_schema_exports_local_storage_warning_code -v
```

Expected: FAIL with `AttributeError: LOCAL_STORAGE_FAILED` or validation error for `local_storage_failed`.

- [ ] **Step 3: Add warning enum value**

Modify `cheapy/models/contracts.py` in `class WarningCode`:

```python
class WarningCode(StrEnum):
    """Stable phase-1 warning codes."""

    MIXED_CURRENCY = "mixed_currency"
    SEARCH_TRUNCATED = "search_truncated"
    CANDIDATE_FAMILY_TRUNCATED = "candidate_family_truncated"
    FARE_DETAILS_NOT_COLLECTED = "fare_details_not_collected"
    SPLIT_TICKET = "split_ticket"
    SELF_TRANSFER = "self_transfer"
    NEARBY_AIRPORT_USED = "nearby_airport_used"
    FLEXIBLE_DATE_USED = "flexible_date_used"
    LOCAL_STORAGE_FAILED = "local_storage_failed"
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_contracts.py::test_warning_accepts_local_storage_failed_code tests/test_schema_export.py::test_schema_exports_local_storage_warning_code -v
```

Expected: PASS.

- [ ] **Step 5: Run contract/schema tests**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_schema_export.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add cheapy/models/contracts.py tests/test_contracts.py tests/test_schema_export.py
git diff --cached --name-status
git commit -m "feat: add local storage warning code" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
M	cheapy/models/contracts.py
M	tests/test_contracts.py
M	tests/test_schema_export.py
```

## Task 2: Add SQLite Storage Core

**Files:**
- Create: `cheapy/storage/__init__.py`
- Create: `cheapy/storage/sqlite.py`
- Create: `tests/storage/__init__.py`
- Create: `tests/storage/test_sqlite.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/storage/__init__.py`:

```python
"""Storage tests."""
```

Create `tests/storage/test_sqlite.py` with these tests. Include the shared `_request()` and `_response()` helpers from the "Shared Test Helpers" section at the top of the file.

```python
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

from cheapy.models import SearchRequestV1, SearchResponseV1
from cheapy.storage import sqlite as storage


def test_resolve_db_path_uses_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_path = tmp_path / "custom.sqlite3"
    monkeypatch.setenv("CHEAPY_DB_PATH", str(custom_path))

    assert storage.resolve_db_path() == custom_path


def test_resolve_db_path_uses_platform_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHEAPY_DB_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    assert storage.resolve_db_path(platform_name="darwin", home=tmp_path) == (
        tmp_path / "Library" / "Application Support" / "Cheapy" / "cheapy.sqlite3"
    )
    assert storage.resolve_db_path(platform_name="linux", home=tmp_path) == (
        tmp_path / ".local" / "share" / "cheapy" / "cheapy.sqlite3"
    )
    assert storage.resolve_db_path(platform_name="win32", home=tmp_path) == (
        tmp_path / "LocalAppData" / "Cheapy" / "cheapy.sqlite3"
    )


def test_is_storage_disabled_only_for_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHEAPY_DISABLE_STORAGE", raising=False)
    assert storage.is_storage_disabled() is False

    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "0")
    assert storage.is_storage_disabled() is False

    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "1")
    assert storage.is_storage_disabled() is True


def test_open_database_migrates_idempotently_and_hardens_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "state" / "cheapy.sqlite3"
    monkeypatch.setenv("CHEAPY_DB_PATH", str(db_path))

    with storage.open_database() as conn:
        storage.migrate(conn)
        storage.migrate(conn)
        version = conn.execute(
            "SELECT value FROM schema_metadata WHERE key = ?",
            ("schema_version",),
        ).fetchone()[0]

    assert version == str(storage.CURRENT_SCHEMA_VERSION)
    assert db_path.exists()
    if os.name == "posix":
        mode = stat.S_IMODE(db_path.stat().st_mode)
        assert mode & stat.S_IRWXO == 0


def test_insert_search_snapshot_persists_safe_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    request = _request()
    response = _response()

    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, request, response)
        search_run = conn.execute("SELECT * FROM search_runs WHERE id = ?", (run_id,)).fetchone()
        provider_run = conn.execute("SELECT * FROM provider_runs WHERE search_run_id = ?", (run_id,)).fetchone()
        observation = conn.execute("SELECT * FROM offer_observations WHERE search_run_id = ?", (run_id,)).fetchone()

    assert search_run["request_id"] == response.request_id
    assert search_run["trip_type"] == "one_way"
    assert search_run["origin"] == "CXR"
    assert search_run["destination"] == "SGN"
    assert json.loads(search_run["passengers_json"]) == {
        "adults": 1,
        "children": 0,
        "infants_on_lap": 0,
        "infants_in_seat": 0,
    }
    assert provider_run["provider_name"] == "manual_fixture"
    assert provider_run["offer_count"] == 1
    assert observation["offer_id"] == "fixture:1"
    assert observation["price_amount"] == 1280000.0
    assert observation["currency"] == "VND"


def test_insert_search_snapshot_rolls_back_on_child_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))

    with storage.open_database() as conn:
        conn.execute("DROP TABLE offer_observations")
        with pytest.raises(sqlite3.Error):
            storage.insert_search_snapshot(conn, _request(), _response())
        row_count = conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0]

    assert row_count == 0


def test_itinerary_fingerprint_is_stable_and_excludes_price() -> None:
    offer = _response().offers[0]
    cheaper_offer = offer.model_copy(update={"price_amount": 1.0})

    assert storage.itinerary_fingerprint(offer) == storage.itinerary_fingerprint(cheaper_offer)


def test_response_json_redacts_sensitive_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    response = _response(
        warnings=[
            {
                "code": "fare_details_not_collected",
                "severity": "warning",
                "message_en": "Safe warning.",
                "details": {
                    "provider": "manual_fixture",
                    "token": "secret-token",
                    "nested": {"url": "https://example.test/challenge?token=secret"},
                },
                "retryable": False,
            }
        ]
    )

    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, _request(), response)
        stored = conn.execute(
            "SELECT response_json FROM search_runs WHERE id = ?",
            (run_id,),
        ).fetchone()[0]

    assert "secret-token" not in stored
    assert "challenge?token=secret" not in stored
    assert storage.REDACTED_VALUE in stored


def test_history_list_and_show_return_summaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))

    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, _request(), _response())
        history = storage.list_history(conn, limit=5)
        shown = storage.show_history(conn, run_id)

    assert history[0]["id"] == run_id
    assert history[0]["best_price_amount"] == 1280000.0
    assert history[0]["currency"] == "VND"
    assert history[0]["mixed_currency"] is False
    assert shown is not None
    assert shown["search_run"]["id"] == run_id
    assert shown["provider_runs"][0]["provider_name"] == "manual_fixture"
    assert shown["offer_observations"][0]["offer_id"] == "fixture:1"


def test_history_list_handles_mixed_currency_without_global_best(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    base_offer = _response().offers[0].model_dump(mode="json")
    usd_offer = dict(base_offer)
    usd_offer.update({"offer_id": "fixture:usd", "currency": "USD", "price_amount": 99.0})
    response = _response(
        offers=[base_offer, usd_offer],
        mixed_currency=True,
        currency_groups=[
            {"currency": "USD", "offer_ids": ["fixture:usd"]},
            {"currency": "VND", "offer_ids": ["fixture:1"]},
        ],
        currency_notes=["Currency conversion was not applied; compare mixed-currency offers separately."],
    )

    with storage.open_database() as conn:
        storage.insert_search_snapshot(conn, _request(), response)
        history = storage.list_history(conn, limit=5)

    assert history[0]["mixed_currency"] is True
    assert history[0]["best_price_amount"] is None
    assert history[0]["currency"] is None
    assert history[0]["best_prices_by_currency"] == [
        {"currency": "USD", "price_amount": 99.0},
        {"currency": "VND", "price_amount": 1280000.0},
    ]


def test_watchlist_add_list_get_and_record_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))

    with storage.open_database() as conn:
        watchlist = storage.add_watchlist(
            conn,
            name="CXR to SGN",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            return_date=None,
            max_price_amount=1300000.0,
            currency="VND",
            max_stops=0,
            max_results=5,
        )
        run_id = storage.insert_search_snapshot(conn, _request(), _response())
        check = storage.record_watchlist_check(
            conn,
            watchlist_id=watchlist["id"],
            search_run_id=run_id,
            decision="book_now",
            best_offer_id="fixture:1",
            best_price_amount=1280000.0,
            currency="VND",
            rationale={"reasons": ["Best fare is below threshold."]},
        )

        watchlists = storage.list_watchlists(conn)
        loaded = storage.get_watchlist(conn, watchlist["id"])

    assert watchlist["enabled"] is True
    assert watchlists == [watchlist]
    assert loaded == watchlist
    assert check["watchlist_id"] == watchlist["id"]
    assert check["search_run_id"] == run_id
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/storage/test_sqlite.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.storage'`.

- [ ] **Step 3: Create storage package exports**

Create `cheapy/storage/__init__.py`:

```python
"""Local storage helpers for Cheapy."""

from cheapy.storage.sqlite import (
    CURRENT_SCHEMA_VERSION,
    REDACTED_VALUE,
    StorageDisabled,
    add_watchlist,
    get_watchlist,
    insert_search_snapshot,
    is_storage_disabled,
    itinerary_fingerprint,
    list_history,
    list_watchlists,
    migrate,
    open_database,
    record_watchlist_check,
    resolve_db_path,
    show_history,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "REDACTED_VALUE",
    "StorageDisabled",
    "add_watchlist",
    "get_watchlist",
    "insert_search_snapshot",
    "is_storage_disabled",
    "itinerary_fingerprint",
    "list_history",
    "list_watchlists",
    "migrate",
    "open_database",
    "record_watchlist_check",
    "resolve_db_path",
    "show_history",
]
```

- [ ] **Step 4: Implement SQLite storage module**

Create `cheapy/storage/sqlite.py` with these public functions and constants:

```python
"""Local SQLite persistence for sanitized Cheapy search history."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterator

from cheapy.models import FlightOfferV1, SearchRequestV1, SearchResponseV1


CURRENT_SCHEMA_VERSION = 1
REDACTED_VALUE = "[redacted]"
_SENSITIVE_KEY_RE = re.compile(
    r"(token|cookie|header|url|payload|body|request|session|secret|authorization|challenge)",
    re.IGNORECASE,
)


class StorageDisabled(RuntimeError):
    """Raised when a CLI storage command is called while storage is disabled."""


def is_storage_disabled(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return values.get("CHEAPY_DISABLE_STORAGE") == "1"


def resolve_db_path(
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    values = os.environ if env is None else env
    override = values.get("CHEAPY_DB_PATH")
    if override:
        return Path(override).expanduser()

    platform = sys.platform if platform_name is None else platform_name
    base_home = Path.home() if home is None else home
    if platform == "darwin":
        return base_home / "Library" / "Application Support" / "Cheapy" / "cheapy.sqlite3"
    if platform.startswith("win"):
        local_app_data = values.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else base_home / "AppData" / "Local"
        return base / "Cheapy" / "cheapy.sqlite3"
    return base_home / ".local" / "share" / "cheapy" / "cheapy.sqlite3"


@contextmanager
def open_database(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    if is_storage_disabled():
        raise StorageDisabled("Local Cheapy storage is disabled.")

    db_path = resolve_db_path() if path is None else path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(db_path.parent, 0o700)
    existed = db_path.exists()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        if not existed and os.name == "posix":
            os.chmod(db_path, 0o600)
        yield conn
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        row = conn.execute(
            "SELECT value FROM schema_metadata WHERE key = ?",
            ("schema_version",),
        ).fetchone()
        current = int(row["value"]) if row is not None else 0
        if current < 1:
            _apply_v1(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_metadata (key, value) VALUES (?, ?)",
                ("schema_version", str(CURRENT_SCHEMA_VERSION)),
            )


def _apply_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_runs (
          id INTEGER PRIMARY KEY,
          created_at_utc TEXT NOT NULL,
          request_id TEXT NOT NULL,
          schema_version TEXT NOT NULL,
          status TEXT NOT NULL,
          trip_type TEXT NOT NULL,
          origin TEXT NOT NULL,
          destination TEXT NOT NULL,
          departure_date TEXT NOT NULL,
          return_date TEXT,
          search_mode TEXT NOT NULL,
          max_results INTEGER NOT NULL,
          passengers_json TEXT NOT NULL,
          mixed_currency INTEGER NOT NULL,
          response_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_runs (
          id INTEGER PRIMARY KEY,
          search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
          provider_name TEXT NOT NULL,
          capability TEXT NOT NULL,
          status TEXT NOT NULL,
          duration_ms INTEGER NOT NULL,
          offer_count INTEGER NOT NULL,
          error_count INTEGER NOT NULL,
          retryable INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS offer_observations (
          id INTEGER PRIMARY KEY,
          search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
          observed_at_utc TEXT NOT NULL,
          offer_id TEXT NOT NULL,
          itinerary_fingerprint TEXT NOT NULL,
          provider TEXT NOT NULL,
          actual_origin TEXT NOT NULL,
          actual_destination TEXT NOT NULL,
          actual_departure_date TEXT NOT NULL,
          actual_return_date TEXT,
          price_amount REAL NOT NULL,
          currency TEXT NOT NULL,
          comparable INTEGER NOT NULL,
          total_duration_minutes INTEGER NOT NULL,
          stops INTEGER NOT NULL,
          flags_json TEXT NOT NULL,
          legs_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlists (
          id INTEGER PRIMARY KEY,
          created_at_utc TEXT NOT NULL,
          updated_at_utc TEXT NOT NULL,
          name TEXT NOT NULL,
          enabled INTEGER NOT NULL,
          origin TEXT NOT NULL,
          destination TEXT NOT NULL,
          departure_date TEXT NOT NULL,
          return_date TEXT,
          max_price_amount REAL,
          currency TEXT,
          max_stops INTEGER,
          max_results INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlist_checks (
          id INTEGER PRIMARY KEY,
          watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
          search_run_id INTEGER REFERENCES search_runs(id) ON DELETE SET NULL,
          checked_at_utc TEXT NOT NULL,
          decision TEXT NOT NULL,
          best_offer_id TEXT,
          best_price_amount REAL,
          currency TEXT,
          rationale_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_search_runs_created_at ON search_runs(created_at_utc);
        CREATE INDEX IF NOT EXISTS idx_search_runs_route ON search_runs(origin, destination, departure_date, return_date);
        CREATE INDEX IF NOT EXISTS idx_offer_observations_fingerprint ON offer_observations(itinerary_fingerprint, observed_at_utc);
        CREATE INDEX IF NOT EXISTS idx_watchlist_checks_watchlist ON watchlist_checks(watchlist_id, checked_at_utc);
        """
    )


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def insert_search_snapshot(
    conn: sqlite3.Connection,
    request: SearchRequestV1,
    response: SearchResponseV1,
    *,
    now_utc: str | None = None,
) -> int:
    observed_at = utc_now() if now_utc is None else now_utc
    trip_type = "one_way" if request.return_date is None else "round_trip"
    sanitized_response = sanitize_response_for_storage(response)
    response_json = json.dumps(sanitized_response.model_dump(mode="json"), sort_keys=True)
    passengers_json = json.dumps(request.passengers.model_dump(mode="json"), sort_keys=True)
    offer_count_by_provider = _offer_count_by_provider(response)

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO search_runs (
              created_at_utc, request_id, schema_version, status, trip_type,
              origin, destination, departure_date, return_date, search_mode,
              max_results, passengers_json, mixed_currency, response_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observed_at,
                response.request_id,
                request.schema_version,
                response.status.value,
                trip_type,
                request.origin.strip().upper(),
                request.destination.strip().upper(),
                request.departure_date,
                request.return_date,
                request.search_mode.value,
                request.max_results,
                passengers_json,
                int(response.mixed_currency),
                response_json,
            ),
        )
        search_run_id = int(cursor.lastrowid)
        for provider_status in response.provider_statuses:
            conn.execute(
                """
                INSERT INTO provider_runs (
                  search_run_id, provider_name, capability, status, duration_ms,
                  offer_count, error_count, retryable
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    search_run_id,
                    provider_status.provider_name,
                    provider_status.capability,
                    provider_status.status.value,
                    provider_status.duration_ms,
                    offer_count_by_provider.get(provider_status.provider_name, 0),
                    len(provider_status.errors),
                    int(provider_status.retryable),
                ),
            )
        for offer in response.offers:
            conn.execute(
                """
                INSERT INTO offer_observations (
                  search_run_id, observed_at_utc, offer_id, itinerary_fingerprint,
                  provider, actual_origin, actual_destination, actual_departure_date,
                  actual_return_date, price_amount, currency, comparable,
                  total_duration_minutes, stops, flags_json, legs_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    search_run_id,
                    observed_at,
                    offer.offer_id,
                    itinerary_fingerprint(offer),
                    offer.provider,
                    offer.actual_origin,
                    offer.actual_destination,
                    offer.actual_departure_date,
                    offer.actual_return_date,
                    offer.price_amount,
                    offer.currency,
                    int(offer.comparable),
                    offer.total_duration_minutes,
                    offer.stops,
                    json.dumps(offer.flags.model_dump(mode="json"), sort_keys=True),
                    json.dumps([leg.model_dump(mode="json") for leg in offer.legs], sort_keys=True),
                ),
            )
    return search_run_id
```

Also implement the helpers used above:

```python
def _offer_count_by_provider(response: SearchResponseV1) -> dict[str, int]:
    counts: dict[str, int] = {}
    for offer in response.offers:
        counts[offer.provider] = counts.get(offer.provider, 0) + 1
    return counts


def itinerary_fingerprint(offer: FlightOfferV1) -> str:
    payload = {
        "provider": offer.provider,
        "actual_origin": offer.actual_origin,
        "actual_destination": offer.actual_destination,
        "actual_departure_date": offer.actual_departure_date,
        "actual_return_date": offer.actual_return_date,
        "total_duration_minutes": offer.total_duration_minutes,
        "stops": offer.stops,
        "legs": [
            {
                "origin": leg.origin,
                "destination": leg.destination,
                "departure_time": leg.departure_time,
                "arrival_time": leg.arrival_time,
                "airline_code": leg.airline_code,
                "flight_number": leg.flight_number,
            }
            for leg in offer.legs
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitize_response_for_storage(response: SearchResponseV1) -> SearchResponseV1:
    payload = response.model_dump(mode="json")
    for key in ("warnings", "errors"):
        for item in payload[key]:
            item["details"] = _sanitize_details(item.get("details", {}))
    for provider_status in payload["provider_statuses"]:
        for key in ("warnings", "errors"):
            for item in provider_status[key]:
                item["details"] = _sanitize_details(item.get("details", {}))
    return SearchResponseV1.model_validate(payload)


def _sanitize_details(value: Any, path: str = "") -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if _SENSITIVE_KEY_RE.search(child_path):
                sanitized[key_text] = REDACTED_VALUE
            else:
                sanitized[key_text] = _sanitize_details(item, child_path)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_details(item, path) for item in value]
    return value
```

Implement history and watchlist helpers:

```python
def list_history(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM search_runs ORDER BY created_at_utc DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_history_summary(conn, row) for row in rows]


def show_history(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    search_run = conn.execute("SELECT * FROM search_runs WHERE id = ?", (run_id,)).fetchone()
    if search_run is None:
        return None
    provider_runs = conn.execute(
        "SELECT * FROM provider_runs WHERE search_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    observations = conn.execute(
        "SELECT * FROM offer_observations WHERE search_run_id = ? ORDER BY price_amount, id",
        (run_id,),
    ).fetchall()
    return {
        "search_run": _row_dict(search_run),
        "provider_runs": [_row_dict(row) for row in provider_runs],
        "offer_observations": [_observation_dict(row) for row in observations],
        "response": json.loads(search_run["response_json"]),
    }


def _history_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    run_id = int(row["id"])
    observations = conn.execute(
        "SELECT currency, price_amount FROM offer_observations WHERE search_run_id = ? ORDER BY currency, price_amount",
        (run_id,),
    ).fetchall()
    best_by_currency: dict[str, float] = {}
    for observation in observations:
        currency = observation["currency"]
        price = float(observation["price_amount"])
        best_by_currency[currency] = min(best_by_currency.get(currency, price), price)
    mixed_currency = bool(row["mixed_currency"])
    best_price_amount: float | None = None
    currency: str | None = None
    if not mixed_currency and best_by_currency:
        currency, best_price_amount = min(best_by_currency.items(), key=lambda item: item[1])
    return {
        "id": run_id,
        "created_at_utc": row["created_at_utc"],
        "request_id": row["request_id"],
        "status": row["status"],
        "origin": row["origin"],
        "destination": row["destination"],
        "departure_date": row["departure_date"],
        "return_date": row["return_date"],
        "search_mode": row["search_mode"],
        "offer_count": len(observations),
        "best_price_amount": best_price_amount,
        "currency": currency,
        "mixed_currency": mixed_currency,
        "best_prices_by_currency": [
            {"currency": item_currency, "price_amount": price}
            for item_currency, price in sorted(best_by_currency.items())
        ],
    }


def add_watchlist(
    conn: sqlite3.Connection,
    *,
    name: str,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
    max_price_amount: float | None,
    currency: str | None,
    max_stops: int | None,
    max_results: int,
) -> dict[str, Any]:
    timestamp = utc_now()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO watchlists (
              created_at_utc, updated_at_utc, name, enabled, origin, destination,
              departure_date, return_date, max_price_amount, currency, max_stops, max_results
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                timestamp,
                name,
                1,
                origin,
                destination,
                departure_date,
                return_date,
                max_price_amount,
                currency,
                max_stops,
                max_results,
            ),
        )
    watchlist = get_watchlist(conn, int(cursor.lastrowid))
    assert watchlist is not None
    return watchlist


def list_watchlists(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM watchlists ORDER BY created_at_utc DESC, id DESC").fetchall()
    return [_watchlist_dict(row) for row in rows]


def get_watchlist(conn: sqlite3.Connection, watchlist_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (watchlist_id,)).fetchone()
    if row is None:
        return None
    return _watchlist_dict(row)


def record_watchlist_check(
    conn: sqlite3.Connection,
    *,
    watchlist_id: int,
    search_run_id: int | None,
    decision: str,
    best_offer_id: str | None,
    best_price_amount: float | None,
    currency: str | None,
    rationale: dict[str, Any],
) -> dict[str, Any]:
    timestamp = utc_now()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO watchlist_checks (
              watchlist_id, search_run_id, checked_at_utc, decision,
              best_offer_id, best_price_amount, currency, rationale_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watchlist_id,
                search_run_id,
                timestamp,
                decision,
                best_offer_id,
                best_price_amount,
                currency,
                json.dumps(rationale, sort_keys=True),
            ),
        )
    return {
        "id": int(cursor.lastrowid),
        "watchlist_id": watchlist_id,
        "search_run_id": search_run_id,
        "checked_at_utc": timestamp,
        "decision": decision,
        "best_offer_id": best_offer_id,
        "best_price_amount": best_price_amount,
        "currency": currency,
        "rationale": rationale,
    }
```

Implement row conversion helpers:

```python
def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _observation_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["comparable"] = bool(data["comparable"])
    data["flags"] = json.loads(data.pop("flags_json"))
    data["legs"] = json.loads(data.pop("legs_json"))
    return data


def _watchlist_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["enabled"] = bool(data["enabled"])
    return data
```

- [ ] **Step 5: Run storage tests**

Run:

```bash
uv run pytest tests/storage/test_sqlite.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add cheapy/storage/__init__.py cheapy/storage/sqlite.py tests/storage/__init__.py tests/storage/test_sqlite.py
git diff --cached --name-status
git commit -m "feat: add sqlite storage core" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
A	cheapy/storage/__init__.py
A	cheapy/storage/sqlite.py
A	tests/storage/__init__.py
A	tests/storage/test_sqlite.py
```

## Task 3: Add Search Service And MCP Persistence

**Files:**
- Create: `cheapy/search_service.py`
- Create: `tests/test_search_service.py`
- Modify: `cheapy/mcp.py`
- Modify: `tests/test_mcp.py`

- [ ] **Step 1: Write failing search service tests**

Create `tests/test_search_service.py`. Include the shared `_request()` and `_response()` helpers.

```python
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from cheapy.models import SearchStatus, WarningCode
from cheapy.search_service import search_with_storage
from cheapy.storage import sqlite as storage


def test_search_with_storage_persists_snapshot(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_search_with_storage_disable_skips_writes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "cheapy.sqlite3"
    monkeypatch.setenv("CHEAPY_DB_PATH", str(db_path))
    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "1")
    monkeypatch.setattr("cheapy.search_service.search_exact", lambda request: _response())

    result = search_with_storage(_request())

    assert result.search_run_id is None
    assert result.storage_enabled is False
    assert result.storage_warning is None
    assert db_path.exists() is False


def test_search_with_storage_failure_adds_safe_warning(monkeypatch: pytest.MonkeyPatch) -> None:
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
```

- [ ] **Step 2: Run search service tests to verify failure**

Run:

```bash
uv run pytest tests/test_search_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.search_service'`.

- [ ] **Step 3: Implement search service**

Create `cheapy/search_service.py`:

```python
"""Search service that adds best-effort local persistence around search."""

from __future__ import annotations

from dataclasses import dataclass

from cheapy.models import SearchRequestV1, SearchResponseV1, Severity, WarningCode, WarningV1
from cheapy.search import search_exact
from cheapy.storage import sqlite as storage


@dataclass(frozen=True)
class SearchWithStorageResult:
    response: SearchResponseV1
    search_run_id: int | None
    storage_enabled: bool
    storage_warning: WarningV1 | None


def search_with_storage(request: SearchRequestV1) -> SearchWithStorageResult:
    response = search_exact(request)
    if storage.is_storage_disabled():
        return SearchWithStorageResult(
            response=response,
            search_run_id=None,
            storage_enabled=False,
            storage_warning=None,
        )

    try:
        search_run_id = _persist_response(request, response)
    except Exception as exc:
        warning = _storage_warning(exc)
        response_with_warning = response.model_copy(
            update={"warnings": [*response.warnings, warning]}
        )
        return SearchWithStorageResult(
            response=response_with_warning,
            search_run_id=None,
            storage_enabled=True,
            storage_warning=warning,
        )

    return SearchWithStorageResult(
        response=response,
        search_run_id=search_run_id,
        storage_enabled=True,
        storage_warning=None,
    )


def _persist_response(request: SearchRequestV1, response: SearchResponseV1) -> int:
    with storage.open_database() as conn:
        return storage.insert_search_snapshot(conn, request, response)


def _storage_warning(exc: Exception) -> WarningV1:
    return WarningV1(
        code=WarningCode.LOCAL_STORAGE_FAILED,
        severity=Severity.WARNING,
        message_en="Local search history could not be saved.",
        details={
            "storage_backend": "sqlite",
            "exception_type": type(exc).__name__,
        },
        retryable=False,
    )
```

- [ ] **Step 4: Run search service tests**

Run:

```bash
uv run pytest tests/test_search_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Write failing MCP tests**

Modify `tests/test_mcp.py`:

1. Update imports:

```python
from cheapy.search_service import SearchWithStorageResult
```

2. Replace `test_mcp_search_tool_annotation_marks_open_world` with:

```python
def test_mcp_search_tool_annotations_reflect_local_history_write() -> None:
    tool = _mcp_tool()

    assert tool.annotations.openWorldHint is True
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.idempotentHint is False
    assert tool.annotations.destructiveHint is False
```

3. In `test_mcp_search_tool_returns_structured_contract_response`, patch `cheapy.mcp.search_with_storage` instead of `cheapy.mcp.search_exact`:

```python
    def fake_search_with_storage(request: Any) -> SearchWithStorageResult:
        assert request.origin == "CXR"
        assert request.destination == "SGN"
        assert request.departure_date == "2026-07-10"
        assert request.return_date == "2026-07-15"
        assert request.search_mode == SearchMode.EXPANDED
        response = SearchResponseV1.model_validate(
            {
                "schema_version": "1",
                "status": "success",
                "request_id": (
                    "search:round_trip:CXR:SGN:2026-07-10:2026-07-15:"
                    "expanded:1:0:0:0:5"
                ),
                "offers": [],
                "warnings": [],
                "errors": [],
                "provider_statuses": [],
                "search_plan": {
                    "search_mode": "expanded",
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
        return SearchWithStorageResult(
            response=response,
            search_run_id=1,
            storage_enabled=True,
            storage_warning=None,
        )

    monkeypatch.setattr("cheapy.mcp.search_with_storage", fake_search_with_storage)
```

- [ ] **Step 6: Run MCP tests to verify failure**

Run:

```bash
uv run pytest tests/test_mcp.py::test_mcp_search_tool_annotations_reflect_local_history_write tests/test_mcp.py::test_mcp_search_tool_returns_structured_contract_response -v
```

Expected: FAIL because MCP still imports `search_exact` and annotations still mark read-only/idempotent.

- [ ] **Step 7: Update MCP wiring**

Modify `cheapy/mcp.py`:

```python
from cheapy.search_service import search_with_storage


_TOOL_ANNOTATIONS: dict[str, object] = {
    "title": "Search Cheapest Flights",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
```

Inside the tool function, replace:

```python
        return await asyncio.to_thread(search_exact, request)
```

with:

```python
        result = await asyncio.to_thread(search_with_storage, request)
        return result.response
```

- [ ] **Step 8: Run MCP tests**

Run:

```bash
uv run pytest tests/test_mcp.py -v
```

Expected: PASS.

- [ ] **Step 9: Run related tests**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_schema_export.py tests/storage/test_sqlite.py tests/test_search_service.py tests/test_mcp.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add cheapy/search_service.py cheapy/mcp.py tests/test_search_service.py tests/test_mcp.py
git diff --cached --name-status
git commit -m "feat: persist mcp searches locally" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
A	cheapy/search_service.py
M	cheapy/mcp.py
A	tests/test_search_service.py
M	tests/test_mcp.py
```

## Task 4: Add CLI History Commands

**Files:**
- Modify: `cheapy/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI history tests**

Append to `tests/test_cli.py`:

```python
from cheapy.storage import sqlite as storage


def _cli_request():
    from cheapy.models import SearchRequestV1

    return SearchRequestV1.model_validate(
        {
            "schema_version": "1",
            "origin": "CXR",
            "destination": "SGN",
            "departure_date": "2026-07-10",
            "return_date": None,
            "max_results": 5,
        }
    )


def _cli_response():
    return _response()


def test_history_list_prints_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        storage.insert_search_snapshot(conn, _cli_request(), _cli_response())

    result = runner.invoke(app, ["history", "list", "--limit", "5"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["runs"][0]["origin"] == "CXR"
    assert payload["runs"][0]["best_price_amount"] == 1280000.0


def test_history_show_prints_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        run_id = storage.insert_search_snapshot(conn, _cli_request(), _cli_response())

    result = runner.invoke(app, ["history", "show", str(run_id)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["search_run"]["id"] == run_id
    assert payload["provider_runs"][0]["provider_name"] == "manual_fixture"
    assert payload["offer_observations"][0]["offer_id"] == "fixture:1"
    assert payload["response"]["request_id"] == _cli_response().request_id


def test_history_show_reports_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))

    result = runner.invoke(app, ["history", "show", "999"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": True,
        "code": "HISTORY_RUN_NOT_FOUND",
        "message": "Search run was not found.",
        "suggestion": "Run 'cheapy history list' to see available search runs.",
    }


def test_history_commands_fail_when_storage_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "1")

    result = runner.invoke(app, ["history", "list"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "STORAGE_DISABLED"
```

Paste the complete `_response()` helper from the "Shared Test Helpers" section
above `_cli_response()` in `tests/test_cli.py`. Keep the helper local to this
file so CLI tests do not import from another test module.

- [ ] **Step 2: Run history CLI tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_history_list_prints_json tests/test_cli.py::test_history_show_prints_json tests/test_cli.py::test_history_show_reports_not_found tests/test_cli.py::test_history_commands_fail_when_storage_disabled -v
```

Expected: FAIL with Typer usage errors for missing `history` command.

- [ ] **Step 3: Add CLI history app**

Modify `cheapy/cli.py` near existing Typer app setup:

```python
from cheapy.storage import sqlite as storage
```

Add Typer group:

```python
history_app = typer.Typer(
    help="Inspect local Cheapy search history.",
    no_args_is_help=True,
)
app.add_typer(history_app, name="history")
```

Add helper:

```python
def _storage_disabled_exit() -> None:
    _json_echo(
        _error_payload(
            "STORAGE_DISABLED",
            "Local Cheapy storage is disabled.",
            "Unset CHEAPY_DISABLE_STORAGE or set it to a value other than 1.",
        ),
        err=True,
    )
    raise typer.Exit(code=1)


def _open_storage_or_exit():
    try:
        return storage.open_database()
    except storage.StorageDisabled:
        _storage_disabled_exit()
```

Add commands:

```python
@history_app.command("list")
def history_list(
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        max=100,
        help="Maximum number of search runs to list.",
    ),
) -> None:
    """List local search history."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()
    with storage.open_database() as conn:
        runs = storage.list_history(conn, limit=limit)
    _json_echo({"status": "ok", "runs": runs})


@history_app.command("show")
def history_show(run_id: int = typer.Argument(..., help="Search run id.")) -> None:
    """Show one local search run."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()
    with storage.open_database() as conn:
        payload = storage.show_history(conn, run_id)
    if payload is None:
        _json_echo(
            _error_payload(
                "HISTORY_RUN_NOT_FOUND",
                "Search run was not found.",
                "Run 'cheapy history list' to see available search runs.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    _json_echo({"status": "ok", **payload})
```

- [ ] **Step 4: Run history CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_history_list_prints_json tests/test_cli.py::test_history_show_prints_json tests/test_cli.py::test_history_show_reports_not_found tests/test_cli.py::test_history_commands_fail_when_storage_disabled -v
```

Expected: PASS.

- [ ] **Step 5: Run full CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add cheapy/cli.py tests/test_cli.py
git diff --cached --name-status
git commit -m "feat: add history cli commands" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
M	cheapy/cli.py
M	tests/test_cli.py
```

## Task 5: Add Watchlist Decision Logic And CLI Commands

**Files:**
- Create: `cheapy/watchlist.py`
- Create: `tests/test_watchlist.py`
- Modify: `cheapy/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing pure watchlist tests**

Create `tests/test_watchlist.py`. Include the shared `_request()` and `_response()` helpers.

```python
from __future__ import annotations

from cheapy.watchlist import build_watchlist_request, evaluate_watchlist


def _watchlist(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 1,
        "name": "CXR to SGN",
        "enabled": True,
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "max_price_amount": 1300000.0,
        "currency": "VND",
        "max_stops": 0,
        "max_results": 5,
    }
    data.update(overrides)
    return data


def test_build_watchlist_request_uses_contract_v1_fields() -> None:
    request = build_watchlist_request(_watchlist())

    assert request.origin == "CXR"
    assert request.destination == "SGN"
    assert request.departure_date == "2026-07-10"
    assert request.return_date is None
    assert request.max_results == 5


def test_evaluate_watchlist_books_when_threshold_met() -> None:
    decision = evaluate_watchlist(
        response=_response(),
        watchlist=_watchlist(max_price_amount=1300000.0),
        historical_comparison={"historical_low": None, "latest_price_amount": None},
    )

    assert decision["decision"] == "book_now"
    assert decision["best_offer"]["offer_id"] == "fixture:1"
    assert decision["threshold_comparison"]["threshold_met"] is True
    assert decision["provider_confidence"] == "high"


def test_evaluate_watchlist_watches_without_threshold() -> None:
    decision = evaluate_watchlist(
        response=_response(),
        watchlist=_watchlist(max_price_amount=None),
        historical_comparison={"historical_low": None, "latest_price_amount": None},
    )

    assert decision["decision"] == "watch"
    assert decision["threshold_comparison"]["threshold_met"] is None
    assert "No max price threshold is configured." in decision["rationale"]


def test_evaluate_watchlist_skips_when_currency_cannot_be_compared() -> None:
    decision = evaluate_watchlist(
        response=_response(mixed_currency=True),
        watchlist=_watchlist(currency=None),
        historical_comparison={"historical_low": None, "latest_price_amount": None},
    )

    assert decision["decision"] == "skip"
    assert "Mixed currencies cannot be compared without a watchlist currency." in decision["rationale"]


def test_evaluate_watchlist_filters_max_stops() -> None:
    response = _response(
        offers=[
            _response().offers[0].model_copy(update={"stops": 1}).model_dump(mode="json")
        ]
    )

    decision = evaluate_watchlist(
        response=response,
        watchlist=_watchlist(max_stops=0),
        historical_comparison={"historical_low": None, "latest_price_amount": None},
    )

    assert decision["decision"] == "skip"
    assert "No qualifying offer matched the watchlist constraints." in decision["rationale"]
```

- [ ] **Step 2: Run pure watchlist tests to verify failure**

Run:

```bash
uv run pytest tests/test_watchlist.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.watchlist'`.

- [ ] **Step 3: Implement pure watchlist module**

Create `cheapy/watchlist.py`:

```python
"""Pure watchlist decision helpers."""

from __future__ import annotations

from typing import Any

from cheapy.models import FlightOfferV1, ProviderStatusCode, SearchRequestV1, SearchResponseV1, SearchStatus


def build_watchlist_request(watchlist: dict[str, Any]) -> SearchRequestV1:
    return SearchRequestV1.model_validate(
        {
            "schema_version": "1",
            "origin": watchlist["origin"],
            "destination": watchlist["destination"],
            "departure_date": watchlist["departure_date"],
            "return_date": watchlist["return_date"],
            "max_results": watchlist["max_results"],
        }
    )


def evaluate_watchlist(
    *,
    response: SearchResponseV1,
    watchlist: dict[str, Any],
    historical_comparison: dict[str, Any],
) -> dict[str, Any]:
    rationale: list[str] = []
    if response.status == SearchStatus.FAILED:
        rationale.append("Search failed, so no booking decision can be made.")
        return _decision_payload("skip", None, watchlist, historical_comparison, response, rationale)

    if response.mixed_currency and watchlist.get("currency") is None:
        rationale.append("Mixed currencies cannot be compared without a watchlist currency.")
        return _decision_payload("skip", None, watchlist, historical_comparison, response, rationale)

    offers = _qualifying_offers(response.offers, watchlist)
    if not offers:
        rationale.append("No qualifying offer matched the watchlist constraints.")
        return _decision_payload("skip", None, watchlist, historical_comparison, response, rationale)

    best_offer = min(offers, key=lambda offer: (not offer.comparable, offer.price_amount, offer.offer_id))
    threshold = watchlist.get("max_price_amount")
    if threshold is None:
        rationale.append("No max price threshold is configured.")
        return _decision_payload("watch", best_offer, watchlist, historical_comparison, response, rationale)

    if best_offer.price_amount <= float(threshold):
        rationale.append("Best fare is at or below the configured threshold.")
        return _decision_payload("book_now", best_offer, watchlist, historical_comparison, response, rationale)

    rationale.append("Best fare is above the configured threshold.")
    return _decision_payload("watch", best_offer, watchlist, historical_comparison, response, rationale)


def _qualifying_offers(
    offers: list[FlightOfferV1],
    watchlist: dict[str, Any],
) -> list[FlightOfferV1]:
    currency = watchlist.get("currency")
    max_stops = watchlist.get("max_stops")
    result: list[FlightOfferV1] = []
    for offer in offers:
        if currency is not None and offer.currency != currency:
            continue
        if max_stops is not None and offer.stops > int(max_stops):
            continue
        result.append(offer)
    return result


def _decision_payload(
    decision: str,
    best_offer: FlightOfferV1 | None,
    watchlist: dict[str, Any],
    historical_comparison: dict[str, Any],
    response: SearchResponseV1,
    rationale: list[str],
) -> dict[str, Any]:
    threshold = watchlist.get("max_price_amount")
    best_price = best_offer.price_amount if best_offer is not None else None
    threshold_met = None
    if threshold is not None and best_price is not None:
        threshold_met = best_price <= float(threshold)
    return {
        "decision": decision,
        "best_offer": _best_offer_summary(best_offer),
        "threshold_comparison": {
            "max_price_amount": threshold,
            "best_price_amount": best_price,
            "currency": best_offer.currency if best_offer is not None else watchlist.get("currency"),
            "threshold_met": threshold_met,
        },
        "historical_comparison": historical_comparison,
        "provider_confidence": provider_confidence(response),
        "rationale": rationale,
    }


def _best_offer_summary(offer: FlightOfferV1 | None) -> dict[str, Any] | None:
    if offer is None:
        return None
    return {
        "offer_id": offer.offer_id,
        "provider": offer.provider,
        "price_amount": offer.price_amount,
        "currency": offer.currency,
        "stops": offer.stops,
        "total_duration_minutes": offer.total_duration_minutes,
        "actual_origin": offer.actual_origin,
        "actual_destination": offer.actual_destination,
        "actual_departure_date": offer.actual_departure_date,
        "actual_return_date": offer.actual_return_date,
    }


def provider_confidence(response: SearchResponseV1) -> str:
    if response.status == SearchStatus.FAILED or not response.offers:
        return "low"
    failed_or_retryable = [
        status
        for status in response.provider_statuses
        if status.status == ProviderStatusCode.FAILED or status.retryable
    ]
    if failed_or_retryable:
        return "medium"
    return "high"
```

- [ ] **Step 4: Run pure watchlist tests**

Run:

```bash
uv run pytest tests/test_watchlist.py -v
```

Expected: PASS.

- [ ] **Step 5: Write failing watchlist CLI tests**

Append to `tests/test_cli.py`:

```python
from cheapy.search_service import SearchWithStorageResult


def test_watchlist_add_and_list_print_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))

    add_result = runner.invoke(
        app,
        [
            "watchlist",
            "add",
            "--name",
            "CXR SGN",
            "--origin",
            "cxr",
            "--destination",
            "sgn",
            "--departure-date",
            "2026-07-10",
            "--max-price-amount",
            "1300000",
            "--currency",
            "VND",
            "--max-stops",
            "0",
        ],
    )
    list_result = runner.invoke(app, ["watchlist", "list"])

    assert add_result.exit_code == 0
    added = json.loads(add_result.stdout)
    assert added["status"] == "ok"
    assert added["watchlist"]["origin"] == "CXR"
    assert added["watchlist"]["destination"] == "SGN"
    assert list_result.exit_code == 0
    listed = json.loads(list_result.stdout)
    assert listed["watchlists"][0]["name"] == "CXR SGN"


def test_watchlist_add_rejects_non_iata_airport(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))

    result = runner.invoke(
        app,
        [
            "watchlist",
            "add",
            "--name",
            "Bad",
            "--origin",
            "saigon",
            "--destination",
            "SGN",
            "--departure-date",
            "2026-07-10",
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "USAGE_ERROR"


def test_watchlist_check_runs_search_records_check_and_prints_decision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    with storage.open_database() as conn:
        watchlist = storage.add_watchlist(
            conn,
            name="CXR SGN",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            return_date=None,
            max_price_amount=1300000.0,
            currency="VND",
            max_stops=0,
            max_results=5,
        )
        run_id = storage.insert_search_snapshot(conn, _cli_request(), _cli_response())

    def fake_search_with_storage(request):
        assert request.origin == "CXR"
        assert request.destination == "SGN"
        return SearchWithStorageResult(
            response=_cli_response(),
            search_run_id=run_id,
            storage_enabled=True,
            storage_warning=None,
        )

    monkeypatch.setattr("cheapy.cli.search_with_storage", fake_search_with_storage)

    result = runner.invoke(app, ["watchlist", "check", str(watchlist["id"])])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["search_run_id"] == run_id
    assert payload["decision"] == "book_now"
    assert payload["best_offer"]["offer_id"] == "fixture:1"


def test_watchlist_check_fails_before_search_when_storage_disabled(monkeypatch) -> None:
    called = False

    def fake_search_with_storage(request):
        nonlocal called
        called = True
        raise AssertionError("search must not be called")

    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "1")
    monkeypatch.setattr("cheapy.cli.search_with_storage", fake_search_with_storage)

    result = runner.invoke(app, ["watchlist", "check", "1"])

    assert result.exit_code == 1
    assert called is False
    assert json.loads(result.stderr)["code"] == "STORAGE_DISABLED"
```

- [ ] **Step 6: Run watchlist CLI tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_watchlist_add_and_list_print_json tests/test_cli.py::test_watchlist_add_rejects_non_iata_airport tests/test_cli.py::test_watchlist_check_runs_search_records_check_and_prints_decision tests/test_cli.py::test_watchlist_check_fails_before_search_when_storage_disabled -v
```

Expected: FAIL with Typer usage errors for missing `watchlist` command.

- [ ] **Step 7: Add watchlist CLI app**

Modify `cheapy/cli.py` imports:

```python
from cheapy.airports import AirportNotFound, resolve_airport
from cheapy.search_service import search_with_storage
from cheapy.watchlist import build_watchlist_request, evaluate_watchlist
```

Add Typer group near other app groups:

```python
watchlist_app = typer.Typer(
    help="Manage local Cheapy price watchlists.",
    no_args_is_help=True,
)
app.add_typer(watchlist_app, name="watchlist")
```

Add validation helpers:

```python
def _normalize_iata(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise click.BadParameter("Airport must be a 3-letter IATA code.")
    try:
        resolve_airport(normalized)
    except AirportNotFound as exc:
        raise click.BadParameter("Airport is not in Cheapy's packaged airport catalog.") from exc
    return normalized
```

Add commands:

```python
@watchlist_app.command("add")
def watchlist_add(
    name: str = typer.Option(..., "--name", help="Watchlist name."),
    origin: str = typer.Option(..., "--origin", help="Origin IATA code."),
    destination: str = typer.Option(..., "--destination", help="Destination IATA code."),
    departure_date: str = typer.Option(..., "--departure-date", help="Departure date in YYYY-MM-DD format."),
    return_date: str | None = typer.Option(None, "--return-date", help="Optional return date in YYYY-MM-DD format."),
    max_price_amount: float | None = typer.Option(None, "--max-price-amount", help="Maximum acceptable price."),
    currency: str | None = typer.Option(None, "--currency", help="Currency code for price comparison."),
    max_stops: int | None = typer.Option(None, "--max-stops", min=0, help="Maximum allowed stops."),
    max_results: int = typer.Option(5, "--max-results", min=1, max=20, help="Maximum search results."),
) -> None:
    """Add a local watchlist."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()
    normalized_origin = _normalize_iata(origin)
    normalized_destination = _normalize_iata(destination)
    currency_code = currency.strip().upper() if currency is not None else None
    SearchRequestV1.model_validate(
        {
            "schema_version": "1",
            "origin": normalized_origin,
            "destination": normalized_destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "max_results": max_results,
        }
    )
    with storage.open_database() as conn:
        watchlist = storage.add_watchlist(
            conn,
            name=name,
            origin=normalized_origin,
            destination=normalized_destination,
            departure_date=departure_date,
            return_date=return_date,
            max_price_amount=max_price_amount,
            currency=currency_code,
            max_stops=max_stops,
            max_results=max_results,
        )
    _json_echo({"status": "ok", "watchlist": watchlist})


@watchlist_app.command("list")
def watchlist_list() -> None:
    """List local watchlists."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()
    with storage.open_database() as conn:
        watchlists = storage.list_watchlists(conn)
    _json_echo({"status": "ok", "watchlists": watchlists})


@watchlist_app.command("check")
def watchlist_check(watchlist_id: int = typer.Argument(..., help="Watchlist id.")) -> None:
    """Run a manual watchlist check."""
    if storage.is_storage_disabled():
        _storage_disabled_exit()
    with storage.open_database() as conn:
        watchlist = storage.get_watchlist(conn, watchlist_id)
    if watchlist is None:
        _json_echo(
            _error_payload(
                "WATCHLIST_NOT_FOUND",
                "Watchlist was not found.",
                "Run 'cheapy watchlist list' to see available watchlists.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    request = build_watchlist_request(watchlist)
    result = search_with_storage(request)
    if result.search_run_id is None:
        _json_echo(
            _error_payload(
                "WATCHLIST_CHECK_NOT_RECORDED",
                "Watchlist check could not be recorded.",
                "Verify local storage is writable and rerun the check.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    historical_comparison = {
        "historical_low": None,
        "latest_price_amount": None,
    }
    decision_payload = evaluate_watchlist(
        response=result.response,
        watchlist=watchlist,
        historical_comparison=historical_comparison,
    )
    best_offer = decision_payload["best_offer"]
    with storage.open_database() as conn:
        storage.record_watchlist_check(
            conn,
            watchlist_id=watchlist_id,
            search_run_id=result.search_run_id,
            decision=decision_payload["decision"],
            best_offer_id=best_offer["offer_id"] if best_offer is not None else None,
            best_price_amount=best_offer["price_amount"] if best_offer is not None else None,
            currency=best_offer["currency"] if best_offer is not None else watchlist.get("currency"),
            rationale={"rationale": decision_payload["rationale"]},
        )
    _json_echo(
        {
            "status": "ok",
            "watchlist_id": watchlist_id,
            "search_run_id": result.search_run_id,
            **decision_payload,
        }
    )
```

- [ ] **Step 8: Run watchlist tests**

Run:

```bash
uv run pytest tests/test_watchlist.py tests/test_cli.py::test_watchlist_add_and_list_print_json tests/test_cli.py::test_watchlist_add_rejects_non_iata_airport tests/test_cli.py::test_watchlist_check_runs_search_records_check_and_prints_decision tests/test_cli.py::test_watchlist_check_fails_before_search_when_storage_disabled -v
```

Expected: PASS.

- [ ] **Step 9: Run CLI and watchlist suites**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_watchlist.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add cheapy/watchlist.py cheapy/cli.py tests/test_watchlist.py tests/test_cli.py
git diff --cached --name-status
git commit -m "feat: add watchlist cli commands" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
A	cheapy/watchlist.py
M	cheapy/cli.py
A	tests/test_watchlist.py
M	tests/test_cli.py
```

## Task 6: Add Historical Comparison For Watchlist Checks

**Files:**
- Modify: `cheapy/storage/sqlite.py`
- Modify: `cheapy/watchlist.py`
- Modify: `cheapy/cli.py`
- Modify: `tests/storage/test_sqlite.py`
- Modify: `tests/test_watchlist.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing historical comparison storage test**

Append to `tests/storage/test_sqlite.py`:

```python
def test_watchlist_historical_comparison_uses_same_route_and_currency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "currency": "VND",
    }
    first = _response()
    second_offer = first.offers[0].model_copy(
        update={"offer_id": "fixture:2", "price_amount": 1200000.0}
    )
    second = _response(offers=[second_offer.model_dump(mode="json")])

    with storage.open_database() as conn:
        storage.insert_search_snapshot(conn, _request(), first, now_utc="2026-05-01T00:00:00Z")
        storage.insert_search_snapshot(conn, _request(), second, now_utc="2026-05-02T00:00:00Z")
        comparison = storage.watchlist_historical_comparison(conn, watchlist_data)

    assert comparison == {
        "historical_low": 1200000.0,
        "latest_price_amount": 1200000.0,
        "currency": "VND",
    }
```

- [ ] **Step 2: Run historical comparison test to verify failure**

Run:

```bash
uv run pytest tests/storage/test_sqlite.py::test_watchlist_historical_comparison_uses_same_route_and_currency -v
```

Expected: FAIL with `AttributeError: module 'cheapy.storage.sqlite' has no attribute 'watchlist_historical_comparison'`.

- [ ] **Step 3: Implement historical comparison helper**

Add to `cheapy/storage/sqlite.py`:

```python
def watchlist_historical_comparison(
    conn: sqlite3.Connection,
    watchlist: dict[str, Any],
) -> dict[str, Any]:
    currency = watchlist.get("currency")
    if currency is None:
        return {"historical_low": None, "latest_price_amount": None, "currency": None}
    rows = conn.execute(
        """
        SELECT oo.price_amount, oo.currency, oo.observed_at_utc
        FROM offer_observations oo
        JOIN search_runs sr ON sr.id = oo.search_run_id
        WHERE sr.origin = ?
          AND sr.destination = ?
          AND sr.departure_date = ?
          AND (sr.return_date IS ? OR sr.return_date = ?)
          AND oo.currency = ?
        ORDER BY oo.observed_at_utc DESC, oo.id DESC
        """,
        (
            watchlist["origin"],
            watchlist["destination"],
            watchlist["departure_date"],
            watchlist.get("return_date"),
            watchlist.get("return_date"),
            currency,
        ),
    ).fetchall()
    if not rows:
        return {"historical_low": None, "latest_price_amount": None, "currency": currency}
    prices = [float(row["price_amount"]) for row in rows]
    return {
        "historical_low": min(prices),
        "latest_price_amount": prices[0],
        "currency": currency,
    }
```

Export it from `cheapy/storage/__init__.py`.

- [ ] **Step 4: Update CLI to use historical comparison before the fresh search**

In `cheapy/cli.py`, inside `watchlist_check`, replace the fixed historical
comparison and compute it before calling `search_with_storage()`:

```python
    with storage.open_database() as conn:
        historical_comparison = storage.watchlist_historical_comparison(conn, watchlist)

    request = build_watchlist_request(watchlist)
    result = search_with_storage(request)
```

- [ ] **Step 5: Run storage, watchlist, CLI tests**

Run:

```bash
uv run pytest tests/storage/test_sqlite.py tests/test_watchlist.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add cheapy/storage/__init__.py cheapy/storage/sqlite.py cheapy/cli.py tests/storage/test_sqlite.py tests/test_watchlist.py tests/test_cli.py
git diff --cached --name-status
git commit -m "feat: add watchlist historical comparison" -m "AI-Generated-By: GPT-5 Codex"
```

Expected staged files:

```text
M	cheapy/storage/__init__.py
M	cheapy/storage/sqlite.py
M	cheapy/cli.py
M	tests/storage/test_sqlite.py
M	tests/test_watchlist.py
M	tests/test_cli.py
```

## Task 7: Final Verification And Cleanup

**Files:**
- Read: all files modified by Tasks 1-6.

- [ ] **Step 1: Search for disallowed persistence strings**

Run:

```bash
rg -n "cookie|authorization|token|payload|browserless|Browserless" cheapy/storage cheapy/search_service.py cheapy/watchlist.py tests/storage tests/test_search_service.py tests/test_watchlist.py tests/test_cli.py tests/test_mcp.py
```

Expected: any matches are either sanitizer tests, sanitizer denylist words, or existing unrelated test text. No storage code should persist raw provider/browser data. Browserless must not appear in new storage/search/watchlist modules.

- [ ] **Step 2: Run required focused verification**

Run:

```bash
uv run pytest tests/test_contracts.py tests/test_cli.py tests/test_schema_export.py tests/test_mcp.py tests/test_search.py tests/test_providers.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full offline suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS. If unrelated pre-existing WIP tests fail, record the exact test names and failure summaries.

- [ ] **Step 4: Verify CLI version**

Run:

```bash
uv run cheapy --version
```

Expected:

```text
0.1.0
```

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only feature files remain changed relative to the last task commit, or no changes if every task committed cleanly. Unrelated WIP from before the feature may still be present and must not be reverted.

- [ ] **Step 6: Commit verification-only fixes if any**

If Step 2 or Step 3 exposed a bug in the feature implementation and you fixed it, commit only those feature files:

```bash
git add cheapy/storage/__init__.py cheapy/storage/sqlite.py cheapy/search_service.py cheapy/watchlist.py cheapy/mcp.py cheapy/cli.py tests/storage/test_sqlite.py tests/test_search_service.py tests/test_watchlist.py tests/test_mcp.py tests/test_cli.py tests/test_contracts.py tests/test_schema_export.py
git diff --cached --name-status
git commit -m "fix: stabilize local sqlite history watchlist" -m "AI-Generated-By: GPT-5 Codex"
```

Expected: commit only if there were actual fixes. If there were no fixes, do not create an empty commit.

## Acceptance Checklist

- MCP `search_cheapest_flights` still returns valid Contract V1.
- MCP search writes one `search_runs` row, related `provider_runs`, and related `offer_observations` when storage is enabled.
- `CHEAPY_DISABLE_STORAGE=1` prevents writes and prevents CLI history/watchlist DB work.
- `CHEAPY_DB_PATH` points storage at a custom DB path.
- Storage failures append safe warning `local_storage_failed` to MCP responses without breaking the tool.
- CLI `history list`, `history show`, `watchlist add`, `watchlist list`, and `watchlist check` return JSON-first outputs.
- Watchlist check records `watchlist_checks` using the exact inserted `search_run_id`.
- Mixed-currency history does not report a global best price.
- No cookies, headers, request bodies, challenge URLs, tokens, raw provider payloads, raw exception messages, or browser session data are persisted.
- Browserless is not reintroduced.
- Required verification commands pass or documented failures are clearly unrelated pre-existing WIP.
