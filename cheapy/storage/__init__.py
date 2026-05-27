"""Local storage APIs for Cheapy."""

from __future__ import annotations

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
    sanitize_response_for_storage,
    show_history,
    watchlist_historical_comparison,
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
    "sanitize_response_for_storage",
    "show_history",
    "watchlist_historical_comparison",
]
