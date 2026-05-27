from __future__ import annotations

import posixpath
import re
from urllib.parse import parse_qsl, unquote, urlsplit


_MAX_URL_DECODE_ROUNDS = 10
_HEX_DIGITS = set("0123456789abcdefABCDEF")
_JWT_SHAPE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{2,}"
    r"\.[A-Za-z0-9_-]{3,}"
    r"(?![A-Za-z0-9_-])"
)
_SKYSCANNER_SEARCH_PATH_RE = re.compile(
    r"^/transport/flights/[a-z0-9-]+/[a-z0-9-]+/[0-9]{6}/(?:[0-9]{6}/)?$"
)

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
    if _has_raw_control_or_space(url):
        return None
    if "#" in url:
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
    if parsed.netloc.endswith(":"):
        return None
    if port not in (None, 443):
        return None
    if parsed.fragment:
        return None

    decoded_path = _decode_to_stability(parsed.path)
    if decoded_path is None:
        return None
    if _decoding_reveals_reserved_path_delimiter(parsed.path):
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


def _has_raw_control_or_space(value: str) -> bool:
    if value != value.strip():
        return True
    return any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value)


def _decode_to_stability(value: str) -> str | None:
    previous = value
    for _ in range(_MAX_URL_DECODE_ROUNDS):
        if _has_malformed_percent_escape(previous):
            return None
        current = unquote(previous)
        if current == previous:
            return current
        previous = current
    if _has_malformed_percent_escape(previous):
        return None
    if unquote(previous) != previous:
        return None
    return previous


def _has_malformed_percent_escape(value: str) -> bool:
    for index, char in enumerate(value):
        if char != "%":
            continue
        escape = value[index + 1 : index + 3]
        if len(escape) != 2 or not all(digit in _HEX_DIGITS for digit in escape):
            return True
    return False


def _decoding_reveals_reserved_path_delimiter(path: str) -> bool:
    previous = path
    previous_counts = _reserved_path_delimiter_counts(previous)
    for _ in range(_MAX_URL_DECODE_ROUNDS):
        if _has_malformed_percent_escape(previous):
            return True
        current = unquote(previous)
        current_counts = _reserved_path_delimiter_counts(current)
        if any(
            current_count > previous_count
            for current_count, previous_count in zip(current_counts, previous_counts)
        ):
            return True
        if current == previous:
            return False
        previous = current
        previous_counts = current_counts
    return True


def _reserved_path_delimiter_counts(path: str) -> tuple[int, int, int]:
    return path.count("/"), path.count("\\"), path.count(";")


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
        segments = [
            segment.split(";", 1)[0] for segment in path.split("/") if segment
        ]
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
        if path != path.lower() or "//" in path or ";" in path:
            return False
        return _SKYSCANNER_SEARCH_PATH_RE.fullmatch(path) is not None
    return False


def _has_sensitive_query_material(query: str) -> bool:
    decoded_query = _decode_to_stability(query)
    if decoded_query is None:
        return True
    if _has_control_character(decoded_query):
        return True
    for key, value in parse_qsl(decoded_query, keep_blank_values=True):
        if _contains_sensitive_term(key) or _contains_sensitive_term(value):
            return True
    return _contains_sensitive_term(decoded_query)


def _contains_sensitive_term(value: str) -> bool:
    decoded_value = _decode_to_stability(value)
    if decoded_value is None:
        return True
    if _has_control_character(decoded_value):
        return True
    if _JWT_SHAPE_RE.search(decoded_value):
        return True
    normalized = re.sub(r"[^a-z0-9]+", "", decoded_value.lower())
    return any(term in normalized for term in _SENSITIVE_TERMS)


def _has_control_character(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
