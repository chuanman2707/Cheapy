# Cheapy Local SQLite History And Watchlist Design

Date: 2026-05-25

## Summary

Add local-only SQLite persistence to Cheapy so every MCP flight search can store
a sanitized local history snapshot, and add a manual CLI watchlist MVP for
checking price movement against saved thresholds and past observations.

The persistence layer is local app data only. It has no hosted backend, sync,
external database, cron, notifications, account login, or provider-internal data
capture. Storage reads only normalized Contract V1 request/response data and
safe derived fields after search orchestration has completed.

Search remains resilient: if SQLite storage fails, the MCP tool still returns
the flight search response and appends a safe Contract V1 warning with code
`local_storage_failed`.

## Approved Decisions

1. Persistence is implemented as a separate module, not inside provider modules.
2. `cheapy.search.search_exact()` remains focused on search orchestration and
   Contract V1 response assembly.
3. A thin service, `cheapy.search_service.search_with_storage()`, runs search
   and then performs best-effort local persistence.
4. MCP `search_cheapest_flights` uses `search_with_storage()`.
5. CLI watchlist checks also use `search_with_storage()` so MCP and CLI share
   the same persistence behavior.
6. SQLite uses stdlib `sqlite3`; no new dependency is added.
7. Storage can be disabled with `CHEAPY_DISABLE_STORAGE=1`.
8. Storage path can be overridden with `CHEAPY_DB_PATH`.
9. A storage failure adds Contract V1 warning code `local_storage_failed`.
10. Storage does not persist cookies, headers, provider request bodies,
    challenge URLs, tokens, raw provider payloads, browser session data, or raw
    exception messages.
11. Browserless is not reintroduced.

## Goals

- Persist every completed MCP search response as a local sanitized search
  snapshot when storage is enabled, including failed Contract V1 responses.
- Store enough normalized history to inspect past searches, provider statuses,
  and offer observations.
- Add manual watchlists for route/date thresholds and one-shot checks from the
  CLI.
- Let users keep all history local by default with predictable OS-specific app
  data paths.
- Keep MCP stdout protocol-clean.
- Keep normal tests offline and deterministic.

## Non-Goals

- No hosted backend, sync, cloud database, or external storage service.
- No cron, background scheduler, notifications, or price-alert delivery.
- No provider-internal persistence.
- No raw browser, cookie, header, token, request-body, challenge, or payload
  persistence.
- No live provider calls in default tests.
- No change to MCP tool count or tool name.
- No broad provider/search refactor beyond the narrow service boundary.

## Current Context

Cheapy currently has:

- Contract V1 request/response models in `cheapy/models/contracts.py`.
- Search orchestration in `cheapy/search.py`.
- MCP tool registration in `cheapy/mcp.py`.
- JSON-first CLI in `cheapy/cli.py`.
- Provider output normalized into `SearchResponseV1`, including offers,
  warnings, errors, provider statuses, search plan, currency groups, and mixed
  currency notes.

The clean attachment point is after `search_exact(request)` returns a normalized
`SearchResponseV1`. That point has all safe public data needed for history and
watchlist decisions, without reaching into provider implementation details.

## Architecture

Add:

```text
cheapy/storage/
  __init__.py
  sqlite.py
```

Optionally add small internal helpers or dataclasses in `cheapy/storage/models.py`
if the implementation needs typed return payloads for CLI output. These are not
public Contract V1 models.

Add:

```text
cheapy/search_service.py
```

`search_service.py` exposes:

```python
def search_with_storage(request: SearchRequestV1) -> SearchResponseV1:
    ...
```

Flow:

1. Call `search_exact(request)`.
2. If `CHEAPY_DISABLE_STORAGE=1`, return the response unchanged.
3. Otherwise initialize/migrate SQLite and persist a sanitized snapshot.
4. If persistence succeeds, return the response unchanged.
5. If persistence fails, append warning `local_storage_failed` and return the
   response.

`cheapy/mcp.py` changes its tool implementation to call `search_with_storage`
inside `asyncio.to_thread`.

`cheapy/cli.py` adds `history` and `watchlist` Typer subcommands. These call
storage helpers directly for read/list/add operations, and call
`search_with_storage()` for `watchlist check`.

Provider modules do not import storage modules.

## Storage Path And Disable Rules

Use stdlib `sqlite3`.

Storage is disabled when:

```text
CHEAPY_DISABLE_STORAGE=1
```

Any other value, including unset, means storage is enabled.

Path resolution:

1. If `CHEAPY_DB_PATH` is set and non-empty, use that exact path.
2. On macOS, use:
   `~/Library/Application Support/Cheapy/cheapy.sqlite3`
3. On Linux, use:
   `~/.local/share/cheapy/cheapy.sqlite3`
4. On Windows, use:
   `%LOCALAPPDATA%/Cheapy/cheapy.sqlite3`

The storage module creates parent directories safely with private permissions
where practical. When the DB file is first created, it attempts to set mode
`0600` on POSIX systems. Permission hardening is best-effort and must not make
search fail.

All SQL uses parameterized statements.

## Migrations

Migrations are idempotent and versioned.

Use a metadata table such as:

```text
schema_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)
```

`schema_metadata["schema_version"]` stores the current integer version as text.
Version 1 creates all MVP tables and indexes.

`migrate()`:

1. Enables `PRAGMA foreign_keys=ON`.
2. Creates metadata table if missing.
3. Reads current schema version, defaulting to 0.
4. Applies each migration in order inside a transaction.
5. Updates the version after each migration.

Running migrations multiple times is safe.

## Schema

### `search_runs`

Stores the sanitized top-level search snapshot.

Columns:

- `id INTEGER PRIMARY KEY`
- `created_at_utc TEXT NOT NULL`
- `request_id TEXT NOT NULL`
- `schema_version TEXT NOT NULL`
- `status TEXT NOT NULL`
- `trip_type TEXT NOT NULL`
- `origin TEXT NOT NULL`
- `destination TEXT NOT NULL`
- `departure_date TEXT NOT NULL`
- `return_date TEXT`
- `search_mode TEXT NOT NULL`
- `max_results INTEGER NOT NULL`
- `passengers_json TEXT NOT NULL`
- `mixed_currency INTEGER NOT NULL`
- `response_json TEXT NOT NULL`

`response_json` is produced from `SearchResponseV1.model_dump(mode="json")`.

### `provider_runs`

Stores safe provider execution summaries from `response.provider_statuses`.

Columns:

- `id INTEGER PRIMARY KEY`
- `search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE`
- `provider_name TEXT NOT NULL`
- `capability TEXT NOT NULL`
- `status TEXT NOT NULL`
- `duration_ms INTEGER NOT NULL`
- `offer_count INTEGER NOT NULL`
- `error_count INTEGER NOT NULL`
- `retryable INTEGER NOT NULL`

`offer_count` is derived by counting returned offers with the same provider.

### `offer_observations`

Stores normalized returned offers and a stable itinerary fingerprint.

Columns:

- `id INTEGER PRIMARY KEY`
- `search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE`
- `observed_at_utc TEXT NOT NULL`
- `offer_id TEXT NOT NULL`
- `itinerary_fingerprint TEXT NOT NULL`
- `provider TEXT NOT NULL`
- `actual_origin TEXT NOT NULL`
- `actual_destination TEXT NOT NULL`
- `actual_departure_date TEXT NOT NULL`
- `actual_return_date TEXT`
- `price_amount REAL NOT NULL`
- `currency TEXT NOT NULL`
- `comparable INTEGER NOT NULL`
- `total_duration_minutes INTEGER NOT NULL`
- `stops INTEGER NOT NULL`
- `flags_json TEXT NOT NULL`
- `legs_json TEXT NOT NULL`

The itinerary fingerprint is deterministic and excludes price. It uses provider,
actual route/date, actual return date, stops, duration, and normalized leg
fields such as origin, destination, departure time, arrival time, airline code,
and flight number.

### `watchlists`

Stores manual watchlist definitions.

Columns:

- `id INTEGER PRIMARY KEY`
- `created_at_utc TEXT NOT NULL`
- `updated_at_utc TEXT NOT NULL`
- `name TEXT NOT NULL`
- `enabled INTEGER NOT NULL`
- `origin TEXT NOT NULL`
- `destination TEXT NOT NULL`
- `departure_date TEXT NOT NULL`
- `return_date TEXT`
- `max_price_amount REAL`
- `currency TEXT`
- `max_stops INTEGER`
- `max_results INTEGER NOT NULL`

MVP adds only create/list/check. Disable/update/delete can be added later.

### `watchlist_checks`

Stores each manual check decision.

Columns:

- `id INTEGER PRIMARY KEY`
- `watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE`
- `search_run_id INTEGER REFERENCES search_runs(id) ON DELETE SET NULL`
- `checked_at_utc TEXT NOT NULL`
- `decision TEXT NOT NULL`
- `best_offer_id TEXT`
- `best_price_amount REAL`
- `currency TEXT`
- `rationale_json TEXT NOT NULL`

### Indexes

Create at least:

- `search_runs(created_at_utc)`
- `search_runs(origin, destination, departure_date, return_date)`
- `offer_observations(itinerary_fingerprint, observed_at_utc)`
- `watchlist_checks(watchlist_id, checked_at_utc)`

## Sanitization Rules

Storage persists only:

- `SearchRequestV1` fields.
- `SearchResponseV1` fields.
- Derived summary fields from normalized response data.
- Safe exception class names for storage failures.

Storage never persists:

- Cookies.
- Headers.
- Provider request bodies.
- Challenge URLs.
- Tokens.
- Raw provider payloads.
- Browser session data.
- Raw provider exception messages.
- Provider debug logs.

If a string is not present in Contract V1 request/response models, storage
should not obtain it.

## Contract V1 Warning

Add warning code:

```text
local_storage_failed
```

This code is used only when local SQLite persistence fails after search
orchestration has produced a response.

Warning shape:

- `code`: `local_storage_failed`
- `severity`: `warning`
- `message_en`: `Local search history could not be saved.`
- `details`: safe metadata such as
  `{"storage_backend": "sqlite", "exception_type": "OperationalError"}`
- `retryable`: `false`

The warning must not include DB path, provider internals, raw exception message,
environment values, cookies, headers, URLs, tokens, or payloads.

## CLI Commands

All commands default to JSON output. Structured errors go to stderr.

### `cheapy history list --limit N`

Lists recent search runs.

Output:

```json
{
  "status": "ok",
  "runs": []
}
```

Each run includes:

- `id`
- `created_at_utc`
- `request_id`
- `status`
- `origin`
- `destination`
- `departure_date`
- `return_date`
- `search_mode`
- `offer_count`
- `best_price_amount`
- `currency`

### `cheapy history show RUN_ID`

Shows one persisted search run.

Output includes:

- `status`
- `search_run`
- `provider_runs`
- `offer_observations`
- `response`

If `RUN_ID` is not found, return a structured error to stderr and exit
non-zero.

### `cheapy watchlist add ...`

Adds one enabled watchlist.

Required flags:

- `--name`
- `--origin`
- `--destination`
- `--departure-date`

Optional flags:

- `--return-date`
- `--max-price-amount`
- `--currency`
- `--max-stops`
- `--max-results`

Defaults:

- `enabled = true`
- `max_results = 5`

Output:

```json
{
  "status": "ok",
  "watchlist": {}
}
```

### `cheapy watchlist list`

Lists watchlists.

Output:

```json
{
  "status": "ok",
  "watchlists": []
}
```

### `cheapy watchlist check WATCHLIST_ID`

Runs a fresh search for the watchlist, persists the search run if storage is
enabled, records a check row, and prints a JSON decision.

Output includes:

- `status`
- `watchlist_id`
- `search_run_id`
- `decision`: `book_now`, `watch`, or `skip`
- `best_offer`
- `threshold_comparison`
- `historical_comparison`
- `provider_confidence`
- `rationale`

## Watchlist Decision Rules

First filter offers by:

1. Currency, when watchlist currency is set.
2. `max_stops`, when set.
3. Comparability where a price threshold comparison is required.

Mixed currency behavior:

- If watchlist `currency` is set, compare only offers in that currency.
- If response has mixed currencies and watchlist `currency` is unset, do not
  compare cross-currency thresholds. Return a rationale string explaining that
  currencies are not converted.

Decision:

- `book_now`: a best offer exists, satisfies route/date/watchlist constraints,
  and is at or below `max_price_amount` when a threshold is set.
- `watch`: a best offer exists but is above the threshold, lacks enough
  comparable context, or is not clearly better than recent history.
- `skip`: search failed, no qualifying offer exists, mixed currency prevents a
  useful comparison, or constraints eliminate all offers.

Historical comparison uses persisted `offer_observations` for the same
origin/destination/departure/return/currency. It reports latest observed price
and historical low when available. It does not perform currency conversion.

Provider confidence:

- `high`: at least one provider succeeded or partially succeeded, and there are
  no retryable provider failures materially reducing confidence.
- `medium`: offers exist, but one or more providers failed, timed out, or were
  retryable.
- `low`: no offers, failed search, or provider statuses indicate only failed or
  retryable results.

Rationale strings are safe, concise, and derived only from Contract V1 response
and local history.

## Error Handling

Search must not fail only because local storage fails.

Storage failure during MCP search:

- Returns normal search response plus `local_storage_failed` warning.
- Emits no stdout diagnostic.
- May emit safe diagnostics to stderr if needed.

Storage failure during CLI history/watchlist commands:

- Returns structured JSON error to stderr.
- Uses non-zero exit code.
- Does not print partial data to stdout unless the command completed
  successfully.

CLI history/watchlist commands while `CHEAPY_DISABLE_STORAGE=1`:

- Return a structured `STORAGE_DISABLED` error to stderr.
- Use a non-zero exit code.
- Do not create or write a database.

Watchlist check with search success but check-row write failure:

- The command exits non-zero because the requested CLI operation did not
  complete.
- The error remains structured and safe.

## Testing

Add offline deterministic tests.

Storage tests:

- DB path resolution for macOS/Linux/Windows behavior where practical.
- `CHEAPY_DB_PATH` override.
- `CHEAPY_DISABLE_STORAGE=1`.
- Migration idempotency.
- Best-effort file permission hardening.
- Insert search snapshot and verify `search_runs`, `provider_runs`, and
  `offer_observations`.
- Fingerprint stability and price exclusion.
- History list/show.
- Watchlist add/list/check records with fake search data.
- Parameterized SQL behavior through normal API use.

MCP tests:

- Search returns valid Contract V1.
- Search persists when storage is enabled with temp DB path.
- Disable env prevents writes.
- Storage failure appends `local_storage_failed` warning and does not make the
  MCP tool error.

CLI tests:

- `history list --limit N` JSON output.
- `history show RUN_ID` JSON output and not-found structured error.
- `watchlist add` JSON output.
- `watchlist list` JSON output.
- `watchlist check WATCHLIST_ID` JSON output using fake search data.
- Structured stderr errors for bad IDs and storage failures.

Contract/schema tests:

- Warning enum accepts `local_storage_failed`.
- Schema export includes the new warning enum value.

Required verification:

```sh
uv run pytest tests/test_contracts.py tests/test_cli.py tests/test_schema_export.py tests/test_mcp.py tests/test_search.py tests/test_providers.py -v
uv run pytest -v
uv run cheapy --version
```

## Rollout Notes

This is a local-only feature. Existing users without a DB get one lazily when a
search or history/watchlist command needs storage. Existing MCP clients continue
to call the same tool with the same input shape. The only Contract V1 surface
change is the added warning enum value for best-effort local storage failures.

Implementation must stage only files relevant to this feature because the repo
may contain unrelated WIP changes.
