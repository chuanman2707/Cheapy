from __future__ import annotations

from time import monotonic

from cheapy.providers.traveloka import browser_helpers


def test_browser_helpers_keep_deadline_and_dom_reads_together() -> None:
    deadline = monotonic() + 10

    assert browser_helpers.remaining_timeout_ms(deadline) > 0
    assert browser_helpers.dom_operation_timeout_ms(
        timeout_ms=250,
        deadline=deadline,
    ) <= 250
