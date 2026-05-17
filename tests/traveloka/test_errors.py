from __future__ import annotations

from cheapy.models import ErrorCode
from cheapy.providers.traveloka import adapter as traveloka_adapter
from cheapy.providers.traveloka import errors as traveloka_errors


def test_traveloka_error_factories_live_in_errors_module() -> None:
    timeout_error = traveloka_errors.timeout_error("PlaywrightTimeoutError")
    blocked_error = traveloka_errors.blocked_error(403)

    assert isinstance(timeout_error, traveloka_errors.TravelokaProviderError)
    assert timeout_error.failure_type == "timeout"
    assert timeout_error.exception_type == "PlaywrightTimeoutError"
    assert blocked_error.failure_type == "blocked"
    assert blocked_error.http_status_code == 403
    assert "http" not in blocked_error.message_en.lower()
    assert not hasattr(traveloka_adapter, "TravelokaProviderError")
