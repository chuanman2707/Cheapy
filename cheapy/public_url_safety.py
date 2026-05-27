from __future__ import annotations

import posixpath
import re
from urllib.parse import parse_qsl, unquote, urlsplit


_MAX_URL_DECODE_ROUNDS = 10

_PROVIDER_HOSTS = {
    "google_fli": "www.google.com",
    "traveloka": "www.traveloka.com",
    "skyscanner": "www.skyscanner.com.sg",
}

_SENSITIVE_TERMS = {
    "auth",
    "body",
    "challenge",
    "cookie",
    "header",
    "jwt",
    "payload",
    "requestid",
    "session",
    "token",
}


def validate_public_search_url(provider: str, url: str) -> str | None:
    """Return the original URL when it is a provider public search URL."""
    expected_host = _PROVIDER_HOSTS.get(provider)
    if expected_host is None:
        return None

    try:
        parsed = urlsplit(url)
    except ValueError:
        return None

    if parsed.scheme != "https":
        return None
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None

    if hostname != expected_host:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if port is not None:
        return None
    if parsed.fragment:
        return None

    decoded_path = _decode_to_stability(parsed.path)
    if decoded_path is None:
        return None
    if "\\" in decoded_path:
        return None

    normalized_path = _normalize_path(decoded_path)
    if normalized_path is None:
        return None
    if _has_internal_path_material(decoded_path, normalized_path):
        return None
    if not _is_allowed_provider_path(provider, parsed.path):
        return None
    if _has_sensitive_query_material(parsed.query):
        return None

    return url


def _decode_to_stability(value: str) -> str | None:
    previous = value
    for _ in range(_MAX_URL_DECODE_ROUNDS):
        current = unquote(previous)
        if current == previous:
            return current
        previous = current
    if unquote(previous) != previous:
        return None
    return previous


def _normalize_path(path: str) -> str | None:
    if not path.startswith("/"):
        return None

    lowered = path.lower()
    if any(segment in {".", ".."} for segment in lowered.split("/")):
        return None

    normalized = posixpath.normpath(lowered)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _has_internal_path_material(decoded_path: str, normalized_path: str) -> bool:
    for path in (decoded_path.lower(), normalized_path):
        segments = [segment for segment in path.split("/") if segment]
        if "api" in segments or "transport_deeplink" in segments:
            return True
        if any(
            first == "g" and second == "radar"
            for first, second in zip(segments, segments[1:])
        ):
            return True
    return False


def _is_allowed_provider_path(provider: str, path: str) -> bool:
    if provider == "google_fli":
        return path == "/travel/flights"
    if provider == "traveloka":
        return path == "/en-en/flight/fulltwosearch"
    if provider == "skyscanner":
        return path.startswith("/transport/flights/")
    return False


def _has_sensitive_query_material(query: str) -> bool:
    decoded_query = _decode_to_stability(query)
    if decoded_query is None:
        return True
    for key, value in parse_qsl(decoded_query, keep_blank_values=True):
        if _contains_sensitive_term(key) or _contains_sensitive_term(value):
            return True
    return _contains_sensitive_term(decoded_query)


def _contains_sensitive_term(value: str) -> bool:
    decoded_value = _decode_to_stability(value)
    if decoded_value is None:
        return True
    normalized = re.sub(r"[^a-z0-9]+", "", decoded_value.lower())
    return any(term in normalized for term in _SENSITIVE_TERMS)
