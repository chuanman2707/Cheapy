"""Shared browser helper functions for the Traveloka research provider."""

from __future__ import annotations

from time import monotonic

from cheapy.providers.traveloka.errors import timeout_error


def close_quietly(target: object | None) -> None:
    if target is None:
        return
    close = getattr(target, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        return


def remaining_timeout_ms(deadline: float, *, raise_on_expired: bool = True) -> int:
    remaining_seconds = deadline - monotonic()
    if remaining_seconds <= 0:
        if not raise_on_expired:
            return 0
        raise timeout_error()
    return max(1, round(remaining_seconds * 1000))


def dom_operation_timeout_ms(
    *,
    timeout_ms: int,
    deadline: float | None,
) -> int | None:
    if deadline is None:
        return max(1, timeout_ms)
    remaining_ms = remaining_timeout_ms(deadline, raise_on_expired=False)
    if remaining_ms <= 0:
        return None
    return max(1, min(timeout_ms, remaining_ms))


def locator_texts(
    page: object,
    selector: str,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> list[str]:
    try:
        locators = page.locator(selector)  # type: ignore[attr-defined]
    except Exception:
        return []

    local_budget_ms = max(1, timeout_ms)

    def next_timeout_ms() -> int | None:
        if local_budget_ms <= 0:
            return None
        if deadline is None:
            return local_budget_ms
        remaining_ms = remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            return None
        return max(1, min(local_budget_ms, remaining_ms))

    def read_text(locator: object) -> str | None:
        nonlocal local_budget_ms
        text_timeout_ms = next_timeout_ms()
        if text_timeout_ms is None:
            return None
        started_at = monotonic()
        try:
            text = locator.inner_text(timeout=text_timeout_ms)
        except Exception:
            return None
        finally:
            elapsed_ms = int((monotonic() - started_at) * 1000)
            local_budget_ms = max(0, local_budget_ms - elapsed_ms)
        return text if isinstance(text, str) else None

    first = getattr(locators, "first", None)
    if callable(first):
        try:
            first_locator = first()
        except Exception:
            return []
    else:
        first_locator = locators

    texts: list[str] = []
    first_text = read_text(first_locator)
    if first_text is not None:
        texts.append(first_text)

    count = getattr(locators, "count", None)
    if not callable(count):
        return texts

    try:
        locator_count = count()
    except Exception:
        return texts

    for index in range(1, locator_count):
        try:
            locator = locators.nth(index)
        except Exception:
            continue
        text = read_text(locator)
        if text is None and next_timeout_ms() is None:
            break
        if text is not None:
            texts.append(text)
    return texts


def read_body_text(
    page: object,
    *,
    timeout_ms: int,
    deadline: float | None,
) -> str:
    if deadline is None:
        text_timeout_ms: int | None = max(1, timeout_ms)
    else:
        remaining_ms = remaining_timeout_ms(deadline, raise_on_expired=False)
        if remaining_ms <= 0:
            text_timeout_ms = None
        else:
            text_timeout_ms = max(1, min(timeout_ms, remaining_ms))
    if text_timeout_ms is None:
        return ""
    try:
        return page.locator("body").inner_text(timeout=text_timeout_ms)  # type: ignore[attr-defined]
    except Exception:
        return ""
