"""Experimental Skyscanner GraphQL signal scanner.

This module scans same-origin JavaScript bundles referenced by a supplied
Skyscanner page URL. It is not a Cheapy provider and is not registered in the
provider registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse


SCHEMA_VERSION = "1"
DEFAULT_MAX_BUNDLES = 20
DEFAULT_MAX_BYTES_PER_BUNDLE = 5_000_000
DEFAULT_TIMEOUT_SECONDS = 15.0


class ScannerFatalError(Exception):
    """Fatal scanner error that should be emitted as JSON to stderr."""

    def __init__(
        self,
        *,
        error_type: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.details = details or {}

    def to_error_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "error": True,
            "error_type": self.error_type,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class ScriptDiscovery:
    script_count: int
    same_origin_urls: list[str]
    skipped_cross_origin_script_count: int


class _ScriptSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "script":
            return
        for name, value in attrs:
            if name.lower() == "src" and value:
                self.sources.append(value)
                return


def validate_https_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ScannerFatalError(
            error_type="invalid_url",
            message="Entry URL must be an HTTPS URL with a host.",
            details={"target_url": url},
        )
    return url


def origin_tuple(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    if parsed.scheme == "" or parsed.hostname is None:
        raise ValueError(f"URL has no origin: {url!r}")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return (parsed.scheme, parsed.hostname.lower(), port)


def same_origin(left_url: str, right_url: str) -> bool:
    return origin_tuple(left_url) == origin_tuple(right_url)


def discover_same_origin_scripts(
    html: str,
    *,
    final_entry_url: str,
) -> ScriptDiscovery:
    parser = _ScriptSrcParser()
    parser.feed(html)

    same_origin_urls: list[str] = []
    skipped_cross_origin_count = 0
    for source in parser.sources:
        resolved = urljoin(final_entry_url, source)
        if same_origin(resolved, final_entry_url):
            same_origin_urls.append(resolved)
        else:
            skipped_cross_origin_count += 1

    return ScriptDiscovery(
        script_count=len(parser.sources),
        same_origin_urls=same_origin_urls,
        skipped_cross_origin_script_count=skipped_cross_origin_count,
    )
