from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import cheapy.storage.sqlite as sqlite_storage
from cheapy.models import (
    CandidateFamily,
    CurrencyGroupV1,
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
    Severity,
    WarningCode,
    WarningV1,
)
from cheapy.storage.sqlite import (
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
    sanitize_response_for_storage,
    show_history,
)


def _request(**overrides: Any) -> SearchRequestV1:
    data: dict[str, Any] = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "search_mode": SearchMode.EXACT,
        "max_results": 5,
    }
    data.update(overrides)
    return SearchRequestV1.model_validate(data)


def _offer(**overrides: Any) -> FlightOfferV1:
    data: dict[str, Any] = {
        "offer_id": "manual_fixture:cxr-sgn-20260710-1",
        "price_amount": 1_280_000.0,
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
            FlightLegV1(
                origin="CXR",
                destination="SGN",
                departure_time="2026-07-10T08:15:00",
                arrival_time="2026-07-10T09:25:00",
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=70,
            )
        ],
        "total_duration_minutes": 70,
        "stops": 0,
        "flags": OfferFlagsV1(),
        "fare_details_status": "not_collected",
    }
    data.update(overrides)
    return FlightOfferV1.model_validate(data)


def _provider_status(**overrides: Any) -> ProviderStatusV1:
    data: dict[str, Any] = {
        "provider_name": "manual_fixture",
        "capability": "exact_one_way",
        "status": ProviderStatusCode.SUCCESS,
        "planned_call_count": 1,
        "executed_call_count": 1,
        "succeeded_call_count": 1,
        "failed_call_count": 0,
        "duration_ms": 12,
        "warnings": [],
        "errors": [],
        "retryable": False,
    }
    data.update(overrides)
    return ProviderStatusV1.model_validate(data)


def _response(**overrides: Any) -> SearchResponseV1:
    offers = overrides.pop("offers", [_offer()])
    mixed_currency = overrides.pop(
        "mixed_currency", len({offer.currency for offer in offers}) > 1
    )
    data: dict[str, Any] = {
        "schema_version": "1",
        "status": SearchStatus.SUCCESS,
        "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
        "offers": offers,
        "warnings": [],
        "errors": [],
        "provider_statuses": [_provider_status()],
        "search_plan": SearchPlanV1(
            search_mode=SearchMode.EXACT,
            planned_candidate_count=1,
            executed_candidate_count=1,
            planned_provider_call_count=1,
            executed_provider_call_count=1,
            candidate_count_by_family={CandidateFamily.EXACT: 1},
            provider_call_count_by_family={CandidateFamily.EXACT: 1},
            truncated=False,
            truncated_families=[],
            candidate_families=[CandidateFamily.EXACT],
        ),
        "mixed_currency": mixed_currency,
        "currency_groups": [
            CurrencyGroupV1(
                currency=currency,
                offer_ids=[
                    offer.offer_id for offer in offers if offer.currency == currency
                ],
            )
            for currency in sorted({offer.currency for offer in offers})
        ],
        "currency_notes": [] if not mixed_currency else ["Currencies are not comparable."],
        "candidates": None,
    }
    data.update(overrides)
    return SearchResponseV1.model_validate(data)


def test_resolve_db_path_uses_env_override(tmp_path: Path) -> None:
    custom_path = tmp_path / "custom" / "cheapy.sqlite3"

    assert resolve_db_path(env={"CHEAPY_DB_PATH": str(custom_path)}) == custom_path


@pytest.mark.parametrize(
    ("platform_name", "expected_parts"),
    [
        ("darwin", ("Library", "Application Support", "Cheapy", "cheapy.sqlite3")),
        ("linux", (".local", "share", "cheapy", "cheapy.sqlite3")),
        ("win32", ("AppData", "Local", "Cheapy", "cheapy.sqlite3")),
    ],
)
def test_resolve_db_path_uses_platform_defaults(
    tmp_path: Path, platform_name: str, expected_parts: tuple[str, ...]
) -> None:
    home = tmp_path / "home"

    assert resolve_db_path(env={}, platform_name=platform_name, home=home) == (
        home.joinpath(*expected_parts)
    )
    assert resolve_db_path(
        env={"LOCALAPPDATA": str(tmp_path / "local-app-data")},
        platform_name="win32",
        home=home,
    ) == tmp_path / "local-app-data" / "Cheapy" / "cheapy.sqlite3"


def test_is_storage_disabled_only_for_one() -> None:
    assert is_storage_disabled({}) is False
    assert is_storage_disabled({"CHEAPY_DISABLE_STORAGE": "0"}) is False
    assert is_storage_disabled({"CHEAPY_DISABLE_STORAGE": "true"}) is False
    assert is_storage_disabled({"CHEAPY_DISABLE_STORAGE": "1 "}) is False
    assert is_storage_disabled({"CHEAPY_DISABLE_STORAGE": "1"}) is True


def test_open_database_migrates_idempotently_and_hardens_file(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "cheapy.sqlite3"

    with open_database(db_path) as conn:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0] == "1"

        migrate(conn)
        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "schema_metadata",
            "search_runs",
            "provider_runs",
            "offer_observations",
            "watchlists",
            "watchlist_checks",
        } <= table_names
        index_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {
            "idx_search_runs_created_at_utc",
            "idx_search_runs_route_dates",
            "idx_provider_runs_search_run_id",
            "idx_offer_observations_search_run_id",
            "idx_offer_observations_fingerprint_observed",
            "idx_watchlist_checks_watchlist_checked",
        } <= index_names

    if os.name == "posix":
        assert (db_path.parent.stat().st_mode & 0o777) == 0o700
        assert (db_path.stat().st_mode & 0o777) == 0o600


def test_migrate_rolls_back_partial_schema_on_failure(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "broken-migration.sqlite3")
    conn.row_factory = sqlite3.Row

    def deny_provider_runs_table(
        action: int,
        arg1: str | None,
        arg2: str | None,
        db_name: str | None,
        source: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "provider_runs":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny_provider_runs_table)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            migrate(conn)
    finally:
        conn.set_authorizer(None)

    table_names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert not (
        {
            "schema_metadata",
            "search_runs",
            "provider_runs",
            "offer_observations",
            "watchlists",
            "watchlist_checks",
        }
        & table_names
    )
    conn.close()


def test_migrate_rejects_future_schema_version_without_downgrade(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(tmp_path / "future-schema.sqlite3")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
        ("schema_version", "999"),
    )
    conn.commit()

    with pytest.raises(RuntimeError, match="999"):
        migrate(conn)

    assert conn.execute(
        "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
    ).fetchone()[0] == "999"
    table_names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "search_runs" not in table_names
    conn.close()


def test_open_database_rehardens_existing_db_file(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file mode hardening is only asserted on POSIX")

    db_path = tmp_path / "data" / "cheapy.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    db_path.chmod(0o644)

    with open_database(db_path) as conn:
        assert conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0] == "1"

    assert (db_path.stat().st_mode & 0o077) == 0


def test_open_database_does_not_chmod_existing_env_override_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file mode hardening is only asserted on POSIX")

    parent = tmp_path / "existing-user-dir"
    parent.mkdir()
    parent.chmod(0o755)
    db_path = parent / "cheapy.sqlite3"
    monkeypatch.setenv("CHEAPY_DB_PATH", str(db_path))

    with open_database() as conn:
        assert conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0] == "1"

    assert (parent.stat().st_mode & 0o777) == 0o755
    assert (db_path.stat().st_mode & 0o077) == 0


def test_open_database_disabled_does_not_create_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "disabled" / "cheapy.sqlite3"
    monkeypatch.setenv("CHEAPY_DISABLE_STORAGE", "1")

    with pytest.raises(StorageDisabled):
        with open_database(db_path):
            raise AssertionError("disabled storage must not open a connection")

    assert not db_path.exists()


def test_open_database_hardens_existing_db_before_migration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file mode hardening is only asserted on POSIX")

    db_path = tmp_path / "data" / "cheapy.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    db_path.chmod(0o644)

    def raise_after_open(conn: sqlite3.Connection) -> None:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        raise RuntimeError("migration failed after open")

    monkeypatch.setattr(sqlite_storage, "migrate", raise_after_open)

    with pytest.raises(RuntimeError, match="migration failed after open"):
        with open_database(db_path):
            raise AssertionError("migration failure must prevent yielding")

    assert (db_path.stat().st_mode & 0o077) == 0


def test_open_database_hardens_new_db_before_migration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file mode hardening is only asserted on POSIX")

    db_path = tmp_path / "data" / "cheapy.sqlite3"
    original_connect = sqlite_storage.sqlite3.connect

    def connect_and_leave_permissive_file(
        path: Path, *args: Any, **kwargs: Any
    ) -> sqlite3.Connection:
        conn = original_connect(path, *args, **kwargs)
        db_path.chmod(0o644)
        return conn

    def raise_after_new_db_open(conn: sqlite3.Connection) -> None:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        raise RuntimeError("migration failed after creating db")

    monkeypatch.setattr(
        sqlite_storage.sqlite3, "connect", connect_and_leave_permissive_file
    )
    monkeypatch.setattr(sqlite_storage, "migrate", raise_after_new_db_open)

    with pytest.raises(RuntimeError, match="migration failed after creating db"):
        with open_database(db_path):
            raise AssertionError("migration failure must prevent yielding")

    assert db_path.exists()
    assert (db_path.stat().st_mode & 0o077) == 0


def test_insert_search_snapshot_persists_safe_rows(tmp_path: Path) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        run_id = insert_search_snapshot(
            conn,
            _request(origin=" cxr ", destination="sgn"),
            _response(),
            now_utc="2026-05-26T10:00:00Z",
        )

        search_run = conn.execute(
            "SELECT * FROM search_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert dict(search_run) | {"response_json": "<json>"} == {
            "id": run_id,
            "created_at_utc": "2026-05-26T10:00:00Z",
            "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
            "schema_version": "1",
            "status": "success",
            "trip_type": "one_way",
            "origin": "CXR",
            "destination": "SGN",
            "departure_date": "2026-07-10",
            "return_date": None,
            "search_mode": "exact",
            "max_results": 5,
            "passengers_json": json.dumps(
                {
                    "adults": 1,
                    "children": 0,
                    "infants_on_lap": 0,
                    "infants_in_seat": 0,
                },
                sort_keys=True,
            ),
            "mixed_currency": 0,
            "response_json": "<json>",
        }
        assert json.loads(search_run["response_json"])["offers"][0]["offer_id"] == (
            "manual_fixture:cxr-sgn-20260710-1"
        )

        provider_run = conn.execute(
            "SELECT * FROM provider_runs WHERE search_run_id = ?", (run_id,)
        ).fetchone()
        assert provider_run["provider_name"] == "manual_fixture"
        assert provider_run["capability"] == "exact_one_way"
        assert provider_run["status"] == "success"
        assert provider_run["duration_ms"] == 12
        assert provider_run["offer_count"] == 1
        assert provider_run["error_count"] == 0
        assert provider_run["retryable"] == 0

        observation = conn.execute(
            "SELECT * FROM offer_observations WHERE search_run_id = ?", (run_id,)
        ).fetchone()
        assert observation["offer_id"] == "manual_fixture:cxr-sgn-20260710-1"
        assert observation["provider"] == "manual_fixture"
        assert observation["actual_origin"] == "CXR"
        assert observation["actual_destination"] == "SGN"
        assert observation["actual_departure_date"] == "2026-07-10"
        assert observation["actual_return_date"] is None
        assert observation["price_amount"] == 1_280_000.0
        assert observation["currency"] == "VND"
        assert observation["comparable"] == 1
        assert observation["total_duration_minutes"] == 70
        assert observation["stops"] == 0
        assert json.loads(observation["legs_json"])[0]["flight_number"] == "VJ601"


def test_stored_response_json_preserves_valid_public_search_url(
    tmp_path: Path,
) -> None:
    public_search_url = (
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
        "ap=CXR.SGN&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
    )
    offer = _offer(
        offer_id="traveloka:cxr-sgn-20260710-1",
        provider="traveloka",
        public_search_url=public_search_url,
    )

    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        run_id = insert_search_snapshot(
            conn,
            _request(),
            _response(
                offers=[offer],
                provider_statuses=[_provider_status(provider_name="traveloka")],
            ),
            now_utc="2026-05-26T10:00:00Z",
        )
        response_json = conn.execute(
            "SELECT response_json FROM search_runs WHERE id = ?", (run_id,)
        ).fetchone()["response_json"]

    assert json.loads(response_json)["offers"][0]["public_search_url"] == (
        public_search_url
    )


def test_sanitize_response_for_storage_nulls_invalid_public_search_url() -> None:
    unsafe_url = "https://evil.example/challenge?token=secret"
    offer = _offer(provider="traveloka").model_copy(
        update={"public_search_url": unsafe_url}
    )
    response = _response(offers=[_offer(provider="traveloka")]).model_copy(
        update={"offers": [offer]}
    )

    sanitized = sanitize_response_for_storage(response)

    assert sanitized.offers[0].public_search_url is None
    assert unsafe_url not in sanitized.model_dump_json()


def test_offer_observations_has_no_public_url_column_or_value(
    tmp_path: Path,
) -> None:
    public_search_url = (
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
        "ap=CXR.SGN&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
    )
    offer = _offer(
        offer_id="traveloka:cxr-sgn-20260710-1",
        provider="traveloka",
        public_search_url=public_search_url,
    )

    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        run_id = insert_search_snapshot(
            conn,
            _request(),
            _response(
                offers=[offer],
                provider_statuses=[_provider_status(provider_name="traveloka")],
            ),
            now_utc="2026-05-26T10:00:00Z",
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(offer_observations)")
        }
        observation = dict(
            conn.execute(
                "SELECT * FROM offer_observations WHERE search_run_id = ?",
                (run_id,),
            ).fetchone()
        )

    assert "url" not in columns
    assert "public_search_url" not in columns
    assert not any("url" in column or "link" in column for column in columns)
    assert public_search_url not in json.dumps(observation, sort_keys=True)


def test_sanitized_response_json_drops_unsafe_public_url_strings() -> None:
    unsafe_urls = [
        "https://www.skyscanner.com.sg/transport_deeplink/sgn/bkk",
        "https://www.traveloka.com/en-en/flight/fulltwosearch?token=secret",
        "https://www.google.com/travel/flights?cookie=session-secret",
        "https://www.google.com/travel/flights?session=secret",
        "https://www.traveloka.com/en-en/flight/fulltwosearch?challenge=block",
        "https://www.traveloka.com/api/flight/search",
    ]
    offers = [
        _offer(
            offer_id=f"traveloka:cxr-sgn-20260710-{index}",
            provider="traveloka",
        ).model_copy(update={"public_search_url": unsafe_url})
        for index, unsafe_url in enumerate(unsafe_urls, start=1)
    ]
    response = _response(
        offers=[
            _offer(
                offer_id=f"traveloka:cxr-sgn-20260710-{index}",
                provider="traveloka",
            )
            for index in range(1, len(unsafe_urls) + 1)
        ]
    ).model_copy(update={"offers": offers})

    payload = sanitize_response_for_storage(response).model_dump_json()

    for unsafe_url in unsafe_urls:
        assert unsafe_url not in payload
    for unsafe_fragment in (
        "transport_deeplink",
        "token=secret",
        "cookie=session-secret",
        "session=secret",
        "challenge=block",
        "/api/flight/search",
    ):
        assert unsafe_fragment not in payload


def test_insert_search_snapshot_rolls_back_on_child_failure(tmp_path: Path) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_provider_insert
            BEFORE INSERT ON provider_runs
            BEGIN
                SELECT RAISE(ABORT, 'provider child boom');
            END
            """
        )

        with pytest.raises(sqlite3.DatabaseError, match="provider child boom"):
            insert_search_snapshot(
                conn,
                _request(),
                _response(),
                now_utc="2026-05-26T10:00:00Z",
            )

        assert conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM provider_runs").fetchone()[0] == 0


def test_insert_search_snapshot_rolls_back_autocommit_child_failure(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(tmp_path / "autocommit.sqlite3", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        migrate(conn)
        conn.execute("DROP TABLE offer_observations")

        with pytest.raises(sqlite3.DatabaseError):
            insert_search_snapshot(
                conn,
                _request(),
                _response(),
                now_utc="2026-05-26T10:00:00Z",
            )

        assert conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM provider_runs").fetchone()[0] == 0
    finally:
        conn.close()


def test_insert_search_snapshot_respects_caller_managed_transaction(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        conn.execute("BEGIN")

        insert_search_snapshot(
            conn,
            _request(),
            _response(),
            now_utc="2026-05-26T10:00:00Z",
        )

        assert conn.in_transaction is True
        conn.execute("ROLLBACK")
        assert conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0] == 0


def test_itinerary_fingerprint_is_stable_and_excludes_price() -> None:
    offer = _offer()

    assert itinerary_fingerprint(offer) == itinerary_fingerprint(
        offer.model_copy(update={"offer_id": "other-id", "price_amount": 999_999.0})
    )
    assert itinerary_fingerprint(offer) != itinerary_fingerprint(
        offer.model_copy(
            update={
                "legs": [
                    offer.legs[0].model_copy(update={"flight_number": "VJ999"})
                ]
            }
        )
    )


def test_response_json_redacts_sensitive_details() -> None:
    response = _response(
        warnings=[
            WarningV1(
                code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
                severity=Severity.WARNING,
                message_en="Fare details were not collected.",
                details={
                    "provider": "manual_fixture",
                    "message": "raw provider text must not persist",
                    "headers": {"authorization": "Bearer top-secret"},
                    "nested": {"request_body": "secret body"},
                },
                retryable=False,
            )
        ],
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Provider failed.",
                details={
                    "exception_type": "ProviderError",
                    "url": "https://secret.example",
                    "raw_error": "raw provider stack must not persist",
                },
                retryable=False,
            )
        ],
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.PARTIAL,
                succeeded_call_count=0,
                failed_call_count=1,
                warnings=[
                    WarningV1(
                        code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
                        severity=Severity.WARNING,
                        message_en="Provider warning.",
                        details={"session": {"id": "secret-session"}},
                        retryable=False,
                    )
                ],
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_FAILED,
                        severity=Severity.ERROR,
                        message_en="Provider error.",
                        details={
                            "provider": "manual_fixture",
                            "failure_type": "blocked",
                            "debug": "raw provider debug must not persist",
                            "challenge_token": "secret-provider-token",
                        },
                        retryable=False,
                    )
                ],
            )
        ],
    )

    sanitized = sanitize_response_for_storage(response)
    payload = sanitized.model_dump(mode="json")

    assert payload["warnings"][0]["details"] == {
        "provider": "manual_fixture",
        "redacted_1": REDACTED_VALUE,
        "redacted_2": REDACTED_VALUE,
        "redacted_3": REDACTED_VALUE,
    }
    assert payload["errors"][0]["details"] == {
        "exception_type": "ProviderError",
        "redacted_1": REDACTED_VALUE,
        "redacted_2": REDACTED_VALUE,
    }
    assert payload["provider_statuses"][0]["warnings"][0]["details"] == {
        "redacted_1": REDACTED_VALUE
    }
    assert payload["provider_statuses"][0]["errors"][0]["details"] == {
        "provider": "manual_fixture",
        "failure_type": "blocked",
        "redacted_1": REDACTED_VALUE,
        "redacted_2": REDACTED_VALUE,
    }
    assert "Bearer top-secret" in response.model_dump_json()
    assert "Bearer top-secret" not in sanitized.model_dump_json()
    assert "secret-provider-token" not in sanitized.model_dump_json()
    assert "raw provider text" not in sanitized.model_dump_json()
    assert "raw provider debug" not in sanitized.model_dump_json()


def test_response_json_preserves_allowlisted_details_recursively() -> None:
    response = _response(
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Provider failed.",
                details={
                    "provider": "manual_fixture",
                    "capability": "exact_one_way",
                    "candidate_family": "exact",
                    "field": "origin",
                    "value": "CXR",
                    "reason": {
                        "failure_type": "timeout",
                        "raw_error": "provider said raw timeout text",
                    },
                    "registry_error_type": "ProviderLoadError",
                    "exception_type": "RuntimeError",
                    "provider_status": "failed",
                    "storage_backend": "sqlite",
                },
                retryable=True,
            )
        ],
    )

    details = sanitize_response_for_storage(response).model_dump(mode="json")[
        "errors"
    ][0]["details"]

    assert details == {
        "provider": "manual_fixture",
        "capability": "exact_one_way",
        "candidate_family": "exact",
        "field": "origin",
        "value": "CXR",
        "reason": {
            "failure_type": "timeout",
            "redacted_1": REDACTED_VALUE,
        },
        "registry_error_type": "ProviderLoadError",
        "exception_type": "RuntimeError",
        "provider_status": "failed",
        "storage_backend": "sqlite",
    }


def test_response_json_redacts_sensitive_allowlisted_values() -> None:
    response = _response(
        warnings=[
            WarningV1(
                code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
                severity=Severity.WARNING,
                message_en="Provider warning.",
                details={
                    "provider": "manual_fixture",
                    "capability": "exact_one_way",
                    "exception_type": "OperationalError",
                    "field": "origin",
                    "value": "ZZZ",
                    "reason": "no_exact_one_way_provider",
                    "storage_backend": "sqlite",
                },
                retryable=False,
            )
        ],
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Provider failed.",
                details={
                    "value": "https://provider.example/search?token=secret",
                    "reason": "Bearer provider-token",
                    "provider_status": [
                        "failed",
                        "cookie=session-secret",
                    ],
                },
                retryable=True,
            )
        ],
    )

    payload = sanitize_response_for_storage(response).model_dump(mode="json")

    assert payload["warnings"][0]["details"] == {
        "provider": "manual_fixture",
        "capability": "exact_one_way",
        "exception_type": "OperationalError",
        "field": "origin",
        "value": "ZZZ",
        "reason": "no_exact_one_way_provider",
        "storage_backend": "sqlite",
    }
    assert payload["errors"][0]["details"] == {
        "value": REDACTED_VALUE,
        "reason": REDACTED_VALUE,
        "provider_status": REDACTED_VALUE,
    }
    assert "provider-token" not in payload["errors"][0]["details"].values()


def test_response_json_redacts_opaque_credential_allowlisted_values() -> None:
    response = _response(
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Provider failed.",
                details={
                    "value": "sk_live_1234567890abcdef",
                    "reason": "AKIAIOSFODNN7EXAMPLE",
                    "exception_type": "OperationalError",
                },
                retryable=True,
            )
        ],
    )

    details = sanitize_response_for_storage(response).model_dump(mode="json")[
        "errors"
    ][0]["details"]

    assert details == {
        "value": REDACTED_VALUE,
        "reason": REDACTED_VALUE,
        "exception_type": "OperationalError",
    }


def test_response_json_redacts_raw_stack_allowlisted_reason() -> None:
    response = _response(
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Provider failed.",
                details={
                    "reason": "raw provider stack must not persist",
                    "exception_type": "OperationalError",
                    "value": "ZZZ",
                },
                retryable=True,
            )
        ],
    )

    details = sanitize_response_for_storage(response).model_dump(mode="json")[
        "errors"
    ][0]["details"]

    assert details == {
        "reason": REDACTED_VALUE,
        "exception_type": "OperationalError",
        "value": "ZZZ",
    }


def test_stored_response_json_replaces_sensitive_and_unknown_detail_keys(
    tmp_path: Path,
) -> None:
    response = _response(
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Provider failed.",
                details={
                    "provider": "manual_fixture",
                    "https://example.test/token=secret": "dynamic sensitive key",
                    "authorization_header": "Bearer secret",
                    "raw_provider_payload": {"body": "secret payload"},
                    "unknown_context": "safe-looking internal text",
                },
                retryable=True,
            )
        ],
    )

    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        run_id = insert_search_snapshot(
            conn,
            _request(),
            response,
            now_utc="2026-05-26T10:00:00Z",
        )
        response_json = conn.execute(
            "SELECT response_json FROM search_runs WHERE id = ?", (run_id,)
        ).fetchone()["response_json"]

    assert "https://example.test" not in response_json
    assert "authorization_header" not in response_json
    assert "raw_provider_payload" not in response_json
    assert "unknown_context" not in response_json
    assert "dynamic sensitive key" not in response_json
    assert "safe-looking internal text" not in response_json

    details = json.loads(response_json)["errors"][0]["details"]
    assert details["provider"] == "manual_fixture"
    assert set(details) == {"provider", "redacted_1", "redacted_2", "redacted_3", "redacted_4"}
    assert all(
        value == REDACTED_VALUE
        for key, value in details.items()
        if key.startswith("redacted_")
    )


def test_stored_response_json_redacts_sensitive_messages(tmp_path: Path) -> None:
    response = _response(
        warnings=[
            WarningV1(
                code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
                severity=Severity.WARNING,
                message_en=(
                    "Provider warning at "
                    "https://example.test/challenge?token=top-warning"
                ),
                details={"provider": "manual_fixture"},
                retryable=False,
            )
        ],
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en=(
                    "Provider failed at "
                    "https://example.test/challenge?token=top-error"
                ),
                details={"exception_type": "ProviderError"},
                retryable=True,
            )
        ],
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.PARTIAL,
                succeeded_call_count=0,
                failed_call_count=1,
                warnings=[
                    WarningV1(
                        code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
                        severity=Severity.WARNING,
                        message_en=(
                            "Nested warning at "
                            "https://example.test/challenge?token=nested-warning"
                        ),
                        details={"provider": "manual_fixture"},
                        retryable=False,
                    )
                ],
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_FAILED,
                        severity=Severity.ERROR,
                        message_en=(
                            "Nested error at "
                            "https://example.test/challenge?token=nested-error"
                        ),
                        details={"failure_type": "blocked"},
                        retryable=True,
                    )
                ],
            )
        ],
    )

    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        run_id = insert_search_snapshot(
            conn,
            _request(),
            response,
            now_utc="2026-05-26T10:00:00Z",
        )
        response_json = conn.execute(
            "SELECT response_json FROM search_runs WHERE id = ?", (run_id,)
        ).fetchone()["response_json"]

    assert "https://example.test" not in response_json
    assert "challenge?token=" not in response_json
    assert "top-warning" not in response_json
    assert "top-error" not in response_json
    assert "nested-warning" not in response_json
    assert "nested-error" not in response_json

    payload = json.loads(response_json)
    assert payload["warnings"][0]["message_en"] == REDACTED_VALUE
    assert payload["errors"][0]["message_en"] == REDACTED_VALUE
    assert payload["provider_statuses"][0]["warnings"][0]["message_en"] == (
        REDACTED_VALUE
    )
    assert payload["provider_statuses"][0]["errors"][0]["message_en"] == (
        REDACTED_VALUE
    )


def test_history_list_and_show_return_summaries(tmp_path: Path) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        older_id = insert_search_snapshot(
            conn,
            _request(),
            _response(),
            now_utc="2026-05-26T10:00:00Z",
        )
        newer_offer = _offer(
            offer_id="manual_fixture:cxr-sgn-20260710-cheaper",
            price_amount=900_000.0,
        )
        newer_id = insert_search_snapshot(
            conn,
            _request(max_results=1),
            _response(request_id="search:newer", offers=[newer_offer]),
            now_utc="2026-05-26T11:00:00Z",
        )

        summaries = list_history(conn, limit=10)

        assert [summary["id"] for summary in summaries] == [newer_id, older_id]
        assert summaries[0] == {
            "id": newer_id,
            "created_at_utc": "2026-05-26T11:00:00Z",
            "request_id": "search:newer",
            "status": "success",
            "origin": "CXR",
            "destination": "SGN",
            "departure_date": "2026-07-10",
            "return_date": None,
            "search_mode": "exact",
            "offer_count": 1,
            "best_price_amount": 900_000.0,
            "currency": "VND",
            "mixed_currency": False,
            "best_prices_by_currency": [
                {
                    "currency": "VND",
                    "price_amount": 900_000.0,
                    "offer_id": "manual_fixture:cxr-sgn-20260710-cheaper",
                }
            ],
        }

        detail = show_history(conn, older_id)
        assert detail is not None
        assert detail["search_run"]["id"] == older_id
        assert detail["search_run"]["origin"] == "CXR"
        assert detail["provider_runs"][0]["provider_name"] == "manual_fixture"
        assert detail["offer_observations"][0]["offer_id"] == (
            "manual_fixture:cxr-sgn-20260710-1"
        )
        assert detail["response"]["request_id"] == (
            "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5"
        )
        assert show_history(conn, 999_999) is None


def test_history_list_handles_mixed_currency_without_global_best(tmp_path: Path) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        run_id = insert_search_snapshot(
            conn,
            _request(),
            _response(
                offers=[
                    _offer(
                        offer_id="manual_fixture:cxr-sgn-20260710-vnd",
                        price_amount=1_280_000.0,
                        currency="VND",
                    ),
                    _offer(
                        offer_id="manual_fixture:cxr-sgn-20260710-usd",
                        price_amount=49.0,
                        currency="USD",
                    ),
                ],
                mixed_currency=True,
            ),
            now_utc="2026-05-26T10:00:00Z",
        )

        assert list_history(conn, limit=1) == [
            {
                "id": run_id,
                "created_at_utc": "2026-05-26T10:00:00Z",
                "request_id": "search:one_way:CXR:SGN:2026-07-10:none:exact:1:0:0:0:5",
                "status": "success",
                "origin": "CXR",
                "destination": "SGN",
                "departure_date": "2026-07-10",
                "return_date": None,
                "search_mode": "exact",
                "offer_count": 2,
                "best_price_amount": None,
                "currency": None,
                "mixed_currency": True,
                "best_prices_by_currency": [
                    {
                        "currency": "USD",
                        "price_amount": 49.0,
                        "offer_id": "manual_fixture:cxr-sgn-20260710-usd",
                    },
                    {
                        "currency": "VND",
                        "price_amount": 1_280_000.0,
                        "offer_id": "manual_fixture:cxr-sgn-20260710-vnd",
                    },
                ],
            }
        ]


def test_watchlist_historical_comparison_uses_same_route_and_currency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        update={"offer_id": "fixture:2", "price_amount": 1_200_000.0}
    )
    second = _response(offers=[second_offer])

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            first,
            now_utc="2026-05-01T00:00:00Z",
        )
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            second,
            now_utc="2026-05-02T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_200_000.0,
        "latest_price_amount": 1_200_000.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_uses_best_price_from_latest_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "currency": "VND",
    }
    older_offer = _offer(
        offer_id="fixture:older",
        price_amount=1_280_000.0,
    )
    latest_best = _offer(
        offer_id="fixture:latest-best",
        price_amount=90.0,
    )
    latest_expensive = _offer(
        offer_id="fixture:latest-expensive",
        price_amount=300.0,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            _response(offers=[older_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            _response(offers=[latest_best, latest_expensive]),
            now_utc="2026-05-02T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 90.0,
        "latest_price_amount": 90.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_filters_actual_itinerary_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "currency": "VND",
    }
    matching_offer = _offer(
        offer_id="fixture:matching-date",
        price_amount=1_280_000.0,
    )
    off_date_offer = _offer(
        offer_id="fixture:off-date",
        actual_departure_date="2026-07-11",
        price_amount=900_000.0,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            _response(offers=[matching_offer, off_date_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_280_000.0,
        "latest_price_amount": 1_280_000.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_filters_actual_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "currency": "VND",
    }
    matching_offer = _offer(
        offer_id="fixture:matching-origin",
        price_amount=1_280_000.0,
    )
    off_origin_offer = _offer(
        offer_id="fixture:off-origin",
        actual_origin="HAN",
        price_amount=900_000.0,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            _response(offers=[matching_offer, off_origin_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_280_000.0,
        "latest_price_amount": 1_280_000.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_filters_actual_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "currency": "VND",
    }
    matching_offer = _offer(
        offer_id="fixture:matching-destination",
        price_amount=1_280_000.0,
    )
    off_destination_offer = _offer(
        offer_id="fixture:off-destination",
        actual_destination="HAN",
        price_amount=900_000.0,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            _response(offers=[matching_offer, off_destination_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_280_000.0,
        "latest_price_amount": 1_280_000.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_filters_round_trip_actual_return_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": "2026-07-15",
        "currency": "VND",
    }
    matching_offer = _offer(
        offer_id="fixture:matching-return",
        requested_return_date="2026-07-15",
        actual_return_date="2026-07-15",
        return_offset_days=0,
        price_amount=1_280_000.0,
    )
    off_return_offer = _offer(
        offer_id="fixture:off-return",
        requested_return_date="2026-07-15",
        actual_return_date="2026-07-16",
        return_offset_days=1,
        price_amount=900_000.0,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(return_date="2026-07-15"),
            _response(offers=[matching_offer, off_return_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_280_000.0,
        "latest_price_amount": 1_280_000.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_matches_round_trip_return_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": "2026-07-15",
        "currency": "VND",
    }
    older_offer = _offer(
        offer_id="fixture:round-trip-older",
        requested_return_date="2026-07-15",
        actual_return_date="2026-07-15",
        return_offset_days=0,
        price_amount=1_280_000.0,
    )
    latest_offer = _offer(
        offer_id="fixture:round-trip-latest",
        requested_return_date="2026-07-15",
        actual_return_date="2026-07-15",
        return_offset_days=0,
        price_amount=1_100_000.0,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(return_date="2026-07-15"),
            _response(offers=[older_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(return_date="2026-07-15"),
            _response(offers=[latest_offer]),
            now_utc="2026-05-02T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_100_000.0,
        "latest_price_amount": 1_100_000.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_ignores_non_comparable_offers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "currency": "VND",
    }
    comparable_offer = _offer(
        offer_id="fixture:comparable",
        price_amount=1_280_000.0,
        comparable=True,
    )
    non_comparable_offer = _offer(
        offer_id="fixture:non-comparable",
        price_amount=900_000.0,
        comparable=False,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            _response(offers=[comparable_offer, non_comparable_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_280_000.0,
        "latest_price_amount": 1_280_000.0,
        "currency": "VND",
    }


def test_watchlist_historical_comparison_filters_passenger_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHEAPY_DB_PATH", str(tmp_path / "cheapy.sqlite3"))
    watchlist_data = {
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "currency": "VND",
        "max_results": 5,
    }
    default_passenger_offer = _offer(
        offer_id="fixture:default-passengers",
        price_amount=1_280_000.0,
    )
    two_adult_offer = _offer(
        offer_id="fixture:two-adults",
        price_amount=900_000.0,
    )

    with sqlite_storage.open_database() as conn:
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(),
            _response(offers=[default_passenger_offer]),
            now_utc="2026-05-01T00:00:00Z",
        )
        sqlite_storage.insert_search_snapshot(
            conn,
            _request(passengers={"adults": 2}),
            _response(offers=[two_adult_offer]),
            now_utc="2026-05-02T00:00:00Z",
        )
        comparison = sqlite_storage.watchlist_historical_comparison(
            conn, watchlist_data
        )

    assert comparison == {
        "historical_low": 1_280_000.0,
        "latest_price_amount": 1_280_000.0,
        "currency": "VND",
    }


def test_add_watchlist_respects_caller_managed_transaction(tmp_path: Path) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        conn.execute("BEGIN")

        watchlist = add_watchlist(
            conn,
            name="CXR to SGN",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            now_utc="2026-05-26T10:00:00Z",
        )

        assert conn.in_transaction is True
        conn.execute("ROLLBACK")
        assert get_watchlist(conn, watchlist["id"]) is None


def test_record_watchlist_check_respects_caller_managed_transaction(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        watchlist = add_watchlist(
            conn,
            name="CXR to SGN",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            now_utc="2026-05-26T10:00:00Z",
        )
        run_id = insert_search_snapshot(
            conn,
            _request(),
            _response(),
            now_utc="2026-05-26T11:00:00Z",
        )

        conn.execute("BEGIN")
        check = record_watchlist_check(
            conn,
            watchlist_id=watchlist["id"],
            search_run_id=run_id,
            checked_at_utc="2026-05-26T11:01:00Z",
            decision="notify",
            rationale={"matched": True, "reasons": ["below_max_price"]},
        )

        assert conn.in_transaction is True
        conn.execute("ROLLBACK")
        assert conn.execute(
            "SELECT COUNT(*) FROM watchlist_checks WHERE id = ?",
            (check["id"],),
        ).fetchone()[0] == 0


def test_watchlist_add_list_get_and_record_check(tmp_path: Path) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        first = add_watchlist(
            conn,
            name="CXR to SGN under 1.5m",
            origin=" cxr ",
            destination="sgn",
            departure_date="2026-07-10",
            return_date=None,
            max_price_amount=1_500_000.0,
            currency="vnd",
            max_stops=0,
            max_results=5,
            now_utc="2026-05-26T10:00:00Z",
        )
        second = add_watchlist(
            conn,
            name="CXR to SGN anytime",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-11",
            max_results=3,
            now_utc="2026-05-26T11:00:00Z",
        )

        assert first["enabled"] is True
        assert first["origin"] == "CXR"
        assert first["destination"] == "SGN"
        assert first["currency"] == "VND"
        assert [item["id"] for item in list_watchlists(conn)] == [
            second["id"],
            first["id"],
        ]
        assert get_watchlist(conn, first["id"]) == first
        assert get_watchlist(conn, 999_999) is None

        run_id = insert_search_snapshot(
            conn,
            _request(),
            _response(),
            now_utc="2026-05-26T12:00:00Z",
        )
        check = record_watchlist_check(
            conn,
            watchlist_id=first["id"],
            search_run_id=run_id,
            checked_at_utc="2026-05-26T12:01:00Z",
            decision="notify",
            best_offer_id="manual_fixture:cxr-sgn-20260710-1",
            best_price_amount=1_280_000.0,
            currency="VND",
            rationale={"matched": True, "reasons": ["below_max_price"]},
        )

        assert check == {
            "id": check["id"],
            "watchlist_id": first["id"],
            "search_run_id": run_id,
            "checked_at_utc": "2026-05-26T12:01:00Z",
            "decision": "notify",
            "best_offer_id": "manual_fixture:cxr-sgn-20260710-1",
            "best_price_amount": 1_280_000.0,
            "currency": "VND",
            "rationale": {"matched": True, "reasons": ["below_max_price"]},
        }


def test_record_watchlist_check_sanitizes_rationale_json(tmp_path: Path) -> None:
    with open_database(tmp_path / "cheapy.sqlite3") as conn:
        watchlist = add_watchlist(
            conn,
            name="CXR to SGN",
            origin="CXR",
            destination="SGN",
            departure_date="2026-07-10",
            now_utc="2026-05-26T10:00:00Z",
        )

        check = record_watchlist_check(
            conn,
            watchlist_id=watchlist["id"],
            checked_at_utc="2026-05-26T10:01:00Z",
            decision="skip",
            rationale={
                "matched": False,
                "reasons": ["price_above_limit"],
                "token": "secret-token",
                "https://example.test/token=secret": "dynamic sensitive key",
                "raw_provider_payload": {"body": "secret payload"},
                "unknown_context": "safe-looking internal text",
                "provider_status": "Bearer provider-token",
            },
        )
        rationale_json = conn.execute(
            "SELECT rationale_json FROM watchlist_checks WHERE id = ?",
            (check["id"],),
        ).fetchone()["rationale_json"]

    assert "secret-token" not in rationale_json
    assert "https://example.test" not in rationale_json
    assert "raw_provider_payload" not in rationale_json
    assert "unknown_context" not in rationale_json
    assert "safe-looking internal text" not in rationale_json
    assert "provider-token" not in rationale_json
    assert REDACTED_VALUE in rationale_json

    rationale = json.loads(rationale_json)
    assert rationale["matched"] is False
    assert rationale["reasons"] == ["price_above_limit"]
    assert "provider_status" in rationale
    assert rationale["provider_status"] == REDACTED_VALUE
    assert any(key.startswith("redacted_") for key in rationale)
