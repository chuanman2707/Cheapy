from __future__ import annotations

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.traveloka import errors as traveloka_errors
from cheapy.providers.traveloka.timing import TravelokaPhaseRecorder


def test_phase_recorder_records_safe_phase_without_sensitive_metadata() -> None:
    now_values = iter([10.0, 10.125])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))

    with recorder.phase("initial_navigation"):
        pass

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "initial_navigation"
    assert record.duration_ms == 125
    assert record.success is True
    assert record.failure_type is None
    assert record.count is None
    assert not hasattr(record, "url")
    assert not hasattr(record, "headers")
    assert not hasattr(record, "payload")
    assert not hasattr(record, "cookies")


def test_phase_recorder_records_safe_failure_type() -> None:
    now_values = iter([20.0, 20.25])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))
    error = traveloka_errors.TravelokaProviderError(
        failure_type="navigation_failed",
        message_en="Traveloka navigation failed at https://example.invalid/path",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
    )

    with pytest.raises(traveloka_errors.TravelokaProviderError):
        with recorder.phase("initial_navigation"):
            raise error

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "initial_navigation"
    assert record.duration_ms == 250
    assert record.success is False
    assert record.failure_type == "navigation_failed"
    assert "example.invalid" not in str(record)


def test_phase_recorder_uses_safe_exception_type_without_message() -> None:
    now_values = iter([30.0, 30.5])
    recorder = TravelokaPhaseRecorder(clock=lambda: next(now_values))

    with pytest.raises(RuntimeError):
        with recorder.phase("context_page_setup"):
            raise RuntimeError("failed at https://example.invalid/private")

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.phase == "context_page_setup"
    assert record.duration_ms == 500
    assert record.success is False
    assert record.failure_type == "runtimeerror"
    assert "example.invalid" not in str(record)
    assert "private" not in str(record)
