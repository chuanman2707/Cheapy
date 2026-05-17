"""Safe phase timing helpers for the Traveloka browser adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic


Clock = Callable[[], float]


@dataclass(frozen=True)
class TravelokaPhaseTiming:
    phase: str
    duration_ms: int
    success: bool
    failure_type: str | None = None
    count: int | None = None


class TravelokaPhaseRecorder:
    """Records phase names and durations without URLs, headers, cookies, or payloads."""

    def __init__(self, *, clock: Clock = monotonic) -> None:
        self._clock = clock
        self._records: list[TravelokaPhaseTiming] = []

    @property
    def records(self) -> tuple[TravelokaPhaseTiming, ...]:
        return tuple(self._records)

    @contextmanager
    def phase(self, phase: str, *, count: int | None = None) -> Iterator[None]:
        started_at = self._clock()
        try:
            yield
        except Exception as exc:
            self.record(
                phase,
                started_at=started_at,
                success=False,
                failure_type=_safe_failure_type(exc),
                count=count,
            )
            raise
        else:
            self.record(phase, started_at=started_at, success=True, count=count)

    def record(
        self,
        phase: str,
        *,
        started_at: float,
        success: bool,
        failure_type: str | None = None,
        count: int | None = None,
    ) -> None:
        duration_ms = max(0, round((self._clock() - started_at) * 1000))
        self._records.append(
            TravelokaPhaseTiming(
                phase=_safe_token(phase),
                duration_ms=duration_ms,
                success=success,
                failure_type=_safe_token(failure_type) if failure_type else None,
                count=count,
            )
        )


def _safe_failure_type(exc: Exception) -> str:
    value = getattr(exc, "failure_type", None)
    if isinstance(value, str) and value:
        return _safe_token(value)
    return _safe_token(type(exc).__name__)


def _safe_token(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in value.strip().lower()
    )
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "unknown"
