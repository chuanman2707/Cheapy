"""Search service that adds best-effort local persistence around search."""

from __future__ import annotations

from dataclasses import dataclass

from cheapy.models import (
    SearchRequestV1,
    SearchResponseV1,
    Severity,
    WarningCode,
    WarningV1,
)
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
        return SearchWithStorageResult(
            response=response.model_copy(
                update={"warnings": [*response.warnings, warning]}
            ),
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
