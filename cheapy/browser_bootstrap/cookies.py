"""Cookie serialization helpers for browser bootstrap sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from cheapy.browser_bootstrap.errors import cookie_unavailable_error


def cookie_header_from_browser_cookies(
    cookies: Sequence[Mapping[str, object]],
    *,
    domain_suffix: str | None = None,
) -> str:
    normalized_suffix = _normalize_domain(domain_suffix) if domain_suffix else None
    serialized: list[str] = []
    for cookie in cookies:
        if normalized_suffix is not None and not _domain_matches(
            cookie.get("domain"),
            normalized_suffix,
        ):
            continue

        name = _cookie_part(cookie.get("name"))
        value = _cookie_part(cookie.get("value"))
        if name is None or value is None:
            continue
        serialized.append(f"{name}={value}")

    if not serialized:
        raise cookie_unavailable_error()
    return "; ".join(serialized)


def _cookie_part(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text == "" or text.strip() == "":
        return None
    return text


def _domain_matches(cookie_domain: object | None, normalized_suffix: str) -> bool:
    if cookie_domain is None:
        return False
    normalized_domain = _normalize_domain(str(cookie_domain))
    return normalized_domain == normalized_suffix or normalized_domain.endswith(
        f".{normalized_suffix}"
    )


def _normalize_domain(domain: str) -> str:
    return domain.strip().lower().strip(".")
