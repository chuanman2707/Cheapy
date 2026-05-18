"""Experimental Skyscanner GraphQL signal scanner.

This module scans same-origin JavaScript bundles referenced by a supplied
Skyscanner page URL. It is not a Cheapy provider and is not registered in the
provider registry.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


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


@dataclass(frozen=True)
class FetchSuccess:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    truncated: bool


@dataclass(frozen=True)
class FetchFailure:
    error_type: str
    message: str
    url: str
    status_code: int | None = None
    details: dict[str, object] | None = None

    def to_error_payload(self, *, scope: str) -> dict[str, object]:
        return {
            "scope": scope,
            "error_type": self.error_type,
            "message": self.message,
            "url": self.url,
            "status_code": self.status_code,
            "details": self.details or {},
        }


class CrossOriginRedirectError(Exception):
    """Raised when urllib sees a redirect outside the allowed origin."""

    def __init__(self, new_url: str) -> None:
        super().__init__(new_url)
        self.new_url = new_url


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Redirect handler that refuses to follow cross-origin redirects."""

    def __init__(self, allowed_origin_url: str) -> None:
        super().__init__()
        self._allowed_origin_url = allowed_origin_url

    def redirect_request(
        self,
        req: object,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> object:
        if not same_origin(newurl, self._allowed_origin_url):
            raise CrossOriginRedirectError(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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


_OPERATION_NAME_RE = re.compile(
    r"\b(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_PERSISTED_ID_RE = re.compile(
    r"""(?x)
    ["'](?:sha256Hash|operationId|queryId)["']\s*:\s*
    ["']([A-Za-z0-9_-]{8,128})["']
    """
)
_GRAPHQL_STRING_RE = re.compile(r"""["']([^"']*graphql[^"']*)["']""", re.IGNORECASE)


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


def extract_graphql_matches(text: str) -> dict[str, list[str]]:
    operation_names = _OPERATION_NAME_RE.findall(text)
    persisted_query_ids = _PERSISTED_ID_RE.findall(text)
    graphql_paths = [
        value
        for value in _GRAPHQL_STRING_RE.findall(text)
        if value.startswith("/") or value.startswith("https://")
    ]
    return {
        "operation_names": _sorted_unique(operation_names),
        "persisted_query_ids": _sorted_unique(persisted_query_ids),
        "graphql_paths": _sorted_unique(graphql_paths),
    }


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


def fetch_url(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    allowed_origin_url: str,
    opener: object | None = None,
) -> FetchSuccess | FetchFailure:
    opener = (
        opener
        if opener is not None
        else build_opener(SameOriginRedirectHandler(allowed_origin_url))
    )
    request = Request(
        url,
        headers={"User-Agent": "Cheapy experimental Skyscanner scanner/1"},
    )

    try:
        response = opener.open(request, timeout=timeout_seconds)  # type: ignore[attr-defined]
        final_url = str(response.geturl())
        if not same_origin(final_url, allowed_origin_url):
            return FetchFailure(
                error_type="cross_origin_redirect",
                message="Fetch redirected to a different origin.",
                url=url,
                status_code=None,
                details={"final_url": final_url},
            )
        status_code = int(getattr(response, "status", 200))
        raw = response.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        body = raw[:max_bytes]
        return FetchSuccess(
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=response.headers.get("content-type", ""),
            body=body,
            truncated=truncated,
        )
    except HTTPError as exc:
        return FetchFailure(
            error_type="http_error",
            message="Fetch returned an HTTP error.",
            url=url,
            status_code=exc.code,
            details={},
        )
    except (TimeoutError, URLError, OSError) as exc:
        return FetchFailure(
            error_type="fetch_failed",
            message="Fetch failed.",
            url=url,
            status_code=None,
            details={"exception_type": type(exc).__name__},
        )
    except CrossOriginRedirectError as exc:
        return FetchFailure(
            error_type="cross_origin_redirect",
            message="Fetch redirected to a different origin.",
            url=url,
            status_code=None,
            details={"final_url": exc.new_url},
        )


class Fetcher(Protocol):
    def __call__(self, url: str, **kwargs: object) -> FetchSuccess | FetchFailure:
        raise NotImplementedError


Clock = Callable[[], str]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _decode_bytes(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _safe_error_details(details: dict[str, object] | None) -> dict[str, object]:
    safe_keys = {"exception_type", "final_url"}
    return {
        key: value
        for key, value in (details or {}).items()
        if key in safe_keys and isinstance(value, str | int | float | bool | None)
    }


def _entry_error_from_failure(failure: FetchFailure) -> ScannerFatalError:
    error_type = failure.error_type
    if failure.status_code in {401, 403}:
        error_type = "blocked"
    elif failure.status_code == 429:
        error_type = "rate_limited"
    elif failure.error_type == "fetch_failed":
        error_type = "entry_fetch_failed"
    elif failure.error_type == "http_error":
        error_type = "entry_fetch_failed"
    return ScannerFatalError(
        error_type=error_type,
        message=failure.message,
        details={
            "target_url": failure.url,
            "status_code": failure.status_code,
            **_safe_error_details(failure.details),
        },
    )


def _bundle_error_type(failure: FetchFailure) -> str:
    if failure.status_code in {401, 403}:
        return "bundle_blocked"
    if failure.status_code == 429:
        return "bundle_rate_limited"
    if failure.error_type == "fetch_failed":
        return "bundle_fetch_failed"
    return failure.error_type


def _bundle_error_payload(failure: FetchFailure) -> dict[str, object]:
    return {
        "scope": "bundle",
        "error_type": _bundle_error_type(failure),
        "message": failure.message,
        "url": failure.url,
        "status_code": failure.status_code,
        "details": _safe_error_details(failure.details),
    }


def scan_url(
    target_url: str,
    *,
    max_bundles: int,
    max_bytes_per_bundle: int,
    timeout_seconds: float,
    fetcher: Fetcher = fetch_url,
    now: Clock = utc_now_iso,
) -> dict[str, Any]:
    validated_url = validate_https_url(target_url)
    entry_result = fetcher(
        validated_url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes_per_bundle,
        allowed_origin_url=validated_url,
    )
    if isinstance(entry_result, FetchFailure):
        raise _entry_error_from_failure(entry_result)

    if "html" not in entry_result.content_type.lower():
        raise ScannerFatalError(
            error_type="unsupported_entry_content_type",
            message="Entry response must be HTML.",
            details={
                "target_url": validated_url,
                "content_type": entry_result.content_type,
                "status_code": entry_result.status_code,
            },
        )

    html = _decode_bytes(entry_result.body)
    discovery = discover_same_origin_scripts(
        html,
        final_entry_url=entry_result.final_url,
    )

    bundles: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for bundle_url in discovery.same_origin_urls[:max_bundles]:
        bundle_result = fetcher(
            bundle_url,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes_per_bundle,
            allowed_origin_url=entry_result.final_url,
        )
        if isinstance(bundle_result, FetchFailure):
            errors.append(_bundle_error_payload(bundle_result))
            continue

        text = _decode_bytes(bundle_result.body)
        bundles.append(
            {
                "url": bundle_result.url,
                "final_url": bundle_result.final_url,
                "status_code": bundle_result.status_code,
                "content_type": bundle_result.content_type,
                "bytes_scanned": len(bundle_result.body),
                "truncated": bundle_result.truncated,
                "matches": extract_graphql_matches(text),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "target_url": validated_url,
        "fetched_at": now(),
        "entry": {
            "status_code": entry_result.status_code,
            "final_url": entry_result.final_url,
            "content_type": entry_result.content_type,
            "script_count": discovery.script_count,
            "same_origin_script_count": len(discovery.same_origin_urls),
            "skipped_cross_origin_script_count": (
                discovery.skipped_cross_origin_script_count
            ),
        },
        "limits": {
            "max_bundles": max_bundles,
            "max_bytes_per_bundle": max_bytes_per_bundle,
            "timeout_seconds": timeout_seconds,
        },
        "bundles": bundles,
        "errors": errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan same-origin Skyscanner JavaScript bundles for GraphQL signals.",
    )
    parser.add_argument("--url", help="HTTPS Skyscanner entry URL.")
    parser.add_argument("--max-bundles", type=int, default=DEFAULT_MAX_BUNDLES)
    parser.add_argument(
        "--max-bytes-per-bundle",
        type=int,
        default=DEFAULT_MAX_BYTES_PER_BUNDLE,
    )
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    fetcher: Fetcher = fetch_url,
    now: Clock = utc_now_iso,
) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.url is None:
            raise ScannerFatalError(
                error_type="invalid_url",
                message="Entry URL must be an HTTPS URL with a host.",
                details={"target_url": ""},
            )
        payload = scan_url(
            args.url,
            max_bundles=args.max_bundles,
            max_bytes_per_bundle=args.max_bytes_per_bundle,
            timeout_seconds=args.timeout_seconds,
            fetcher=fetcher,
            now=now,
        )
    except ScannerFatalError as exc:
        print(json.dumps(exc.to_error_payload(), sort_keys=True), file=sys.stderr)
        return 1

    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
