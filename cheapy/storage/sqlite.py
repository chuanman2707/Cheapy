"""SQLite-backed local storage for search history and watchlists."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import itertools
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from cheapy.models import FlightOfferV1, SearchRequestV1, SearchResponseV1

CURRENT_SCHEMA_VERSION = 1
REDACTED_VALUE = "[redacted]"

_SENSITIVE_DETAIL_WORDS = (
    "token",
    "cookie",
    "header",
    "url",
    "payload",
    "body",
    "request",
    "session",
    "secret",
    "authorization",
    "challenge",
)
_SENSITIVE_VALUE_MARKERS = (
    "raw",
    "debug",
    "stack",
    "stack trace",
    "stacktrace",
    "traceback",
    "error message",
    "error-message",
    "error_message",
)
_SAFE_DETAIL_KEYS = frozenset(
    {
        "provider",
        "capability",
        "candidate_family",
        "field",
        "value",
        "reason",
        "registry_error_type",
        "exception_type",
        "failure_type",
        "provider_status",
        "storage_backend",
    }
)
_SAFE_RATIONALE_KEYS = _SAFE_DETAIL_KEYS | frozenset({"matched", "reasons"})
_SAVEPOINT_COUNTER = itertools.count(1)


class StorageDisabled(RuntimeError):
    """Raised when local storage is disabled by environment configuration."""


def is_storage_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether local storage is disabled by environment configuration."""

    values = os.environ if env is None else env
    return values.get("CHEAPY_DISABLE_STORAGE") == "1"


def resolve_db_path(
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: str | Path | None = None,
) -> Path:
    """Resolve the local SQLite database path for the current platform."""

    values = os.environ if env is None else env
    override = values.get("CHEAPY_DB_PATH")
    if override:
        return Path(override).expanduser()

    home_path = Path.home() if home is None else Path(home).expanduser()
    platform = sys.platform if platform_name is None else platform_name

    if platform.startswith("darwin"):
        return home_path / "Library" / "Application Support" / "Cheapy" / "cheapy.sqlite3"
    if platform.startswith("win"):
        local_app_data = values.get("LOCALAPPDATA")
        base_path = (
            Path(local_app_data).expanduser()
            if local_app_data
            else home_path / "AppData" / "Local"
        )
        return base_path / "Cheapy" / "cheapy.sqlite3"
    return home_path / ".local" / "share" / "cheapy" / "cheapy.sqlite3"


@contextmanager
def open_database(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open, initialize, and yield a SQLite connection for local storage."""

    if is_storage_disabled():
        raise StorageDisabled("Cheapy local storage is disabled")

    db_path = resolve_db_path() if path is None else Path(path).expanduser()
    db_existed = db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Hardening is best-effort and applies to the resolved DB path parent,
    # including CHEAPY_DB_PATH override directories.
    _best_effort_chmod(db_path.parent, 0o700)
    if db_existed:
        _best_effort_chmod(db_path, 0o600)

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _best_effort_chmod(db_path, 0o600)
        migrate(conn)
        _best_effort_chmod(db_path, 0o600)
        yield conn
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema migrations idempotently, owning one migration transaction."""

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN")
    try:
        existing_version = _read_schema_version(conn)
        if (
            existing_version is not None
            and existing_version > CURRENT_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "Cheapy storage schema_version "
                f"{existing_version} is newer than supported "
                f"{CURRENT_SCHEMA_VERSION}"
            )
        _apply_schema_v1(conn)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _read_schema_version(conn: sqlite3.Connection) -> int | None:
    metadata_exists = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_metadata'
        """
    ).fetchone()
    if metadata_exists is None:
        return None

    row = conn.execute(
        "SELECT value FROM schema_metadata WHERE key = ?",
        ("schema_version",),
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid Cheapy storage schema_version: {row[0]!r}") from exc


def _apply_schema_v1(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
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
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_runs (
            id INTEGER PRIMARY KEY,
            search_run_id INTEGER NOT NULL
                REFERENCES search_runs(id) ON DELETE CASCADE,
            provider_name TEXT NOT NULL,
            capability TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            offer_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            retryable INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS offer_observations (
            id INTEGER PRIMARY KEY,
            search_run_id INTEGER NOT NULL
                REFERENCES search_runs(id) ON DELETE CASCADE,
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
        )
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_checks (
            id INTEGER PRIMARY KEY,
            watchlist_id INTEGER NOT NULL
                REFERENCES watchlists(id) ON DELETE CASCADE,
            search_run_id INTEGER
                REFERENCES search_runs(id) ON DELETE SET NULL,
            checked_at_utc TEXT NOT NULL,
            decision TEXT NOT NULL,
            best_offer_id TEXT,
            best_price_amount REAL,
            currency TEXT,
            rationale_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_runs_created_at_utc "
        "ON search_runs(created_at_utc)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_runs_route_dates "
        "ON search_runs(origin, destination, departure_date, return_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_provider_runs_search_run_id "
        "ON provider_runs(search_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_offer_observations_search_run_id "
        "ON offer_observations(search_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_offer_observations_fingerprint_observed "
        "ON offer_observations(itinerary_fingerprint, observed_at_utc)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_checks_watchlist_checked "
        "ON watchlist_checks(watchlist_id, checked_at_utc)"
    )
    conn.execute(
        """
        INSERT INTO schema_metadata(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("schema_version", str(CURRENT_SCHEMA_VERSION)),
    )


def insert_search_snapshot(
    conn: sqlite3.Connection,
    request: SearchRequestV1,
    response: SearchResponseV1,
    *,
    now_utc: str | None = None,
) -> int:
    """Persist one search snapshot, owning one savepoint transaction."""

    observed_at = now_utc or _now_utc()
    sanitized_response = sanitize_response_for_storage(response)
    response_json = _json_dumps(sanitized_response.model_dump(mode="json"))
    passengers_json = _json_dumps(request.passengers.model_dump(mode="json"))
    origin = _normalize_code(request.origin)
    destination = _normalize_code(request.destination)
    trip_type = "one_way" if request.return_date is None else "round_trip"

    offer_counts_by_provider: dict[str, int] = {}
    for offer in sanitized_response.offers:
        offer_counts_by_provider[offer.provider] = (
            offer_counts_by_provider.get(offer.provider, 0) + 1
        )

    with _savepoint(conn, "cheapy_snapshot"):
        cursor = conn.execute(
            """
            INSERT INTO search_runs (
                created_at_utc,
                request_id,
                schema_version,
                status,
                trip_type,
                origin,
                destination,
                departure_date,
                return_date,
                search_mode,
                max_results,
                passengers_json,
                mixed_currency,
                response_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observed_at,
                sanitized_response.request_id,
                sanitized_response.schema_version,
                sanitized_response.status.value,
                trip_type,
                origin,
                destination,
                request.departure_date,
                request.return_date,
                request.search_mode.value,
                request.max_results,
                passengers_json,
                int(sanitized_response.mixed_currency),
                response_json,
            ),
        )
        run_id = int(cursor.lastrowid)

        for provider_status in sanitized_response.provider_statuses:
            conn.execute(
                """
                INSERT INTO provider_runs (
                    search_run_id,
                    provider_name,
                    capability,
                    status,
                    duration_ms,
                    offer_count,
                    error_count,
                    retryable
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    provider_status.provider_name,
                    provider_status.capability,
                    provider_status.status.value,
                    provider_status.duration_ms,
                    offer_counts_by_provider.get(provider_status.provider_name, 0),
                    len(provider_status.errors),
                    int(provider_status.retryable),
                ),
            )

        for offer in sanitized_response.offers:
            conn.execute(
                """
                INSERT INTO offer_observations (
                    search_run_id,
                    observed_at_utc,
                    offer_id,
                    itinerary_fingerprint,
                    provider,
                    actual_origin,
                    actual_destination,
                    actual_departure_date,
                    actual_return_date,
                    price_amount,
                    currency,
                    comparable,
                    total_duration_minutes,
                    stops,
                    flags_json,
                    legs_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    observed_at,
                    offer.offer_id,
                    itinerary_fingerprint(offer),
                    offer.provider,
                    _normalize_code(offer.actual_origin),
                    _normalize_code(offer.actual_destination),
                    offer.actual_departure_date,
                    offer.actual_return_date,
                    offer.price_amount,
                    offer.currency,
                    int(offer.comparable),
                    offer.total_duration_minutes,
                    offer.stops,
                    _json_dumps(offer.flags.model_dump(mode="json")),
                    _json_dumps(
                        [leg.model_dump(mode="json") for leg in offer.legs]
                    ),
                ),
            )

    return run_id


@contextmanager
def _savepoint(conn: sqlite3.Connection, prefix: str) -> Iterator[None]:
    savepoint_name = f"{prefix}_{next(_SAVEPOINT_COUNTER)}"
    conn.execute(f"SAVEPOINT {savepoint_name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")


def itinerary_fingerprint(offer: FlightOfferV1) -> str:
    """Return a deterministic itinerary fingerprint that excludes price."""

    payload = {
        "provider": offer.provider.strip().lower(),
        "actual_origin": _normalize_code(offer.actual_origin),
        "actual_destination": _normalize_code(offer.actual_destination),
        "actual_departure_date": offer.actual_departure_date,
        "actual_return_date": offer.actual_return_date,
        "total_duration_minutes": offer.total_duration_minutes,
        "stops": offer.stops,
        "legs": [
            {
                "origin": _normalize_code(leg.origin),
                "destination": _normalize_code(leg.destination),
                "departure_time": leg.departure_time.strip(),
                "arrival_time": leg.arrival_time.strip(),
                "airline_code": leg.airline_code.strip().upper(),
                "flight_number": leg.flight_number.strip().upper(),
            }
            for leg in offer.legs
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sanitize_response_for_storage(response: SearchResponseV1) -> SearchResponseV1:
    """Redact sensitive warning and error details before storing a response."""

    payload = response.model_dump(mode="json")
    for warning in payload["warnings"]:
        warning["details"] = _redact_sensitive_detail(warning.get("details", {}))
    for error in payload["errors"]:
        error["details"] = _redact_sensitive_detail(error.get("details", {}))
    for provider_status in payload["provider_statuses"]:
        for warning in provider_status["warnings"]:
            warning["details"] = _redact_sensitive_detail(warning.get("details", {}))
        for error in provider_status["errors"]:
            error["details"] = _redact_sensitive_detail(error.get("details", {}))
    return SearchResponseV1.model_validate(payload)


def list_history(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    """List search history summaries newest first."""

    if limit <= 0:
        return []

    rows = conn.execute(
        """
        SELECT *
        FROM search_runs
        ORDER BY created_at_utc DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_history_summary(conn, row) for row in rows]


def show_history(
    conn: sqlite3.Connection, run_id: int
) -> dict[str, Any] | None:
    """Return a complete search history record, or None if missing."""

    search_run = conn.execute(
        "SELECT * FROM search_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if search_run is None:
        return None

    search_run_dict = _row_dict(search_run)
    response = json.loads(search_run_dict.pop("response_json"))
    search_run_dict["mixed_currency"] = bool(search_run_dict["mixed_currency"])

    provider_runs = [
        _provider_run_dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM provider_runs
            WHERE search_run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        )
    ]
    offer_observations = [
        _offer_observation_dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM offer_observations
            WHERE search_run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        )
    ]

    return {
        "search_run": search_run_dict,
        "provider_runs": provider_runs,
        "offer_observations": offer_observations,
        "response": response,
    }


def add_watchlist(
    conn: sqlite3.Connection,
    *,
    name: str,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    max_price_amount: float | None = None,
    currency: str | None = None,
    max_stops: int | None = None,
    max_results: int = 5,
    now_utc: str | None = None,
) -> dict[str, Any]:
    """Create one enabled watchlist, owning one transaction for the operation."""

    created_at = now_utc or _now_utc()
    normalized_currency = currency.strip().upper() if currency else None
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO watchlists (
                created_at_utc,
                updated_at_utc,
                name,
                enabled,
                origin,
                destination,
                departure_date,
                return_date,
                max_price_amount,
                currency,
                max_stops,
                max_results
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                created_at,
                name.strip(),
                1,
                _normalize_code(origin),
                _normalize_code(destination),
                departure_date,
                return_date,
                max_price_amount,
                normalized_currency,
                max_stops,
                max_results,
            ),
        )
        watchlist_id = int(cursor.lastrowid)

    watchlist = get_watchlist(conn, watchlist_id)
    if watchlist is None:
        raise RuntimeError("Inserted watchlist could not be loaded")
    return watchlist


def list_watchlists(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all watchlists newest first."""

    return [
        _watchlist_dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM watchlists
            ORDER BY created_at_utc DESC, id DESC
            """
        )
    ]


def get_watchlist(
    conn: sqlite3.Connection, watchlist_id: int
) -> dict[str, Any] | None:
    """Return a watchlist by id, or None if it is missing."""

    row = conn.execute(
        "SELECT * FROM watchlists WHERE id = ?", (watchlist_id,)
    ).fetchone()
    if row is None:
        return None
    return _watchlist_dict(row)


def record_watchlist_check(
    conn: sqlite3.Connection,
    *,
    watchlist_id: int,
    decision: str,
    rationale: Mapping[str, Any] | None = None,
    search_run_id: int | None = None,
    checked_at_utc: str | None = None,
    best_offer_id: str | None = None,
    best_price_amount: float | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Record one watchlist check, owning one transaction for the operation."""

    checked_at = checked_at_utc or _now_utc()
    rationale_json = _json_dumps(
        _redact_sensitive_detail(dict(rationale or {}), safe_keys=_SAFE_RATIONALE_KEYS)
    )
    normalized_currency = currency.strip().upper() if currency else None
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO watchlist_checks (
                watchlist_id,
                search_run_id,
                checked_at_utc,
                decision,
                best_offer_id,
                best_price_amount,
                currency,
                rationale_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watchlist_id,
                search_run_id,
                checked_at,
                decision,
                best_offer_id,
                best_price_amount,
                normalized_currency,
                rationale_json,
            ),
        )
        check_id = int(cursor.lastrowid)

    row = conn.execute(
        "SELECT * FROM watchlist_checks WHERE id = ?", (check_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Inserted watchlist check could not be loaded")
    return _watchlist_check_dict(row)


def _best_effort_chmod(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        path.chmod(mode)
    except OSError:
        pass


def _now_utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_code(value: str) -> str:
    return value.strip().upper()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _provider_run_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["retryable"] = bool(data["retryable"])
    return data


def _offer_observation_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["comparable"] = bool(data["comparable"])
    data["flags"] = json.loads(data.pop("flags_json"))
    data["legs"] = json.loads(data.pop("legs_json"))
    return data


def _watchlist_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["enabled"] = bool(data["enabled"])
    return data


def _watchlist_check_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["rationale"] = json.loads(data.pop("rationale_json"))
    return data


def _history_summary(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> dict[str, Any]:
    per_currency = _best_prices_by_currency(conn, int(row["id"]))
    mixed_currency = bool(row["mixed_currency"])
    best_price_amount = None
    currency = None
    if not mixed_currency and per_currency:
        best = min(per_currency, key=lambda item: (item["price_amount"], item["currency"]))
        best_price_amount = best["price_amount"]
        currency = best["currency"]

    return {
        "id": row["id"],
        "created_at_utc": row["created_at_utc"],
        "request_id": row["request_id"],
        "status": row["status"],
        "origin": row["origin"],
        "destination": row["destination"],
        "departure_date": row["departure_date"],
        "return_date": row["return_date"],
        "search_mode": row["search_mode"],
        "offer_count": _offer_count(conn, int(row["id"])),
        "best_price_amount": best_price_amount,
        "currency": currency,
        "mixed_currency": mixed_currency,
        "best_prices_by_currency": per_currency,
    }


def _offer_count(conn: sqlite3.Connection, run_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM offer_observations WHERE search_run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )


def _best_prices_by_currency(
    conn: sqlite3.Connection, run_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT currency, offer_id, price_amount
        FROM offer_observations
        WHERE search_run_id = ?
        ORDER BY currency ASC, price_amount ASC, id ASC
        """,
        (run_id,),
    ).fetchall()
    best_by_currency: dict[str, dict[str, Any]] = {}
    for row in rows:
        currency = row["currency"]
        if currency not in best_by_currency:
            best_by_currency[currency] = {
                "currency": currency,
                "price_amount": row["price_amount"],
                "offer_id": row["offer_id"],
            }
    return [best_by_currency[currency] for currency in sorted(best_by_currency)]


def _redact_sensitive_detail(
    value: Any,
    path: tuple[str, ...] = (),
    *,
    safe_keys: frozenset[str] = _SAFE_DETAIL_KEYS,
) -> Any:
    if _path_is_sensitive(path):
        return REDACTED_VALUE
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        redacted_count = 1
        for key, child in value.items():
            key_text = str(key)
            child_path = path + (key_text,)
            if _path_is_sensitive(child_path) or key_text not in safe_keys:
                redacted_key = _next_redacted_key(redacted, redacted_count)
                redacted[redacted_key] = REDACTED_VALUE
                redacted_count += 1
            else:
                redacted[key_text] = _redact_sensitive_detail(
                    child, child_path, safe_keys=safe_keys
                )
        return redacted
    if isinstance(value, list):
        if any(_value_is_sensitive(item) for item in value):
            return REDACTED_VALUE
        return [
            _redact_sensitive_detail(item, path, safe_keys=safe_keys)
            for item in value
        ]
    if _value_is_sensitive(value):
        return REDACTED_VALUE
    return value


def _next_redacted_key(existing: Mapping[str, Any], start: int) -> str:
    current = start
    while f"redacted_{current}" in existing:
        current += 1
    return f"redacted_{current}"


def _path_is_sensitive(path: tuple[str, ...]) -> bool:
    lowered_path = ".".join(path).lower()
    return any(word in lowered_path for word in _SENSITIVE_DETAIL_WORDS)


def _value_is_sensitive(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return (
            any(word in lowered for word in _SENSITIVE_DETAIL_WORDS)
            or any(marker in lowered for marker in _SENSITIVE_VALUE_MARKERS)
            or "bearer " in lowered
            or "http://" in lowered
            or "https://" in lowered
        )
    if isinstance(value, list):
        return any(_value_is_sensitive(item) for item in value)
    if isinstance(value, dict):
        return any(
            _path_is_sensitive((str(key),)) or _value_is_sensitive(child)
            for key, child in value.items()
        )
    return False
