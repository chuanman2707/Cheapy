# Cheapy Skyscanner GraphQL Bundle Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an experimental Skyscanner JavaScript bundle scanner that finds GraphQL candidate signals without registering Skyscanner as a Cheapy provider.

**Architecture:** Add a small executable module at `cheapy/providers/skyscanner/scan_graphql_bundles.py` plus tests under `tests/skyscanner/`. The module exposes testable pure helpers for URL/origin handling, script discovery, GraphQL regex extraction, bounded HTTPS fetching, scan orchestration, and CLI JSON output, while omitting `manifest.toml` so the existing registry ignores it.

**Tech Stack:** Python 3.12 stdlib only for scanner runtime (`argparse`, `datetime`, `html.parser`, `json`, `re`, `sys`, `urllib`), pytest, uv, existing Cheapy provider registry and CLI tests.

---

## File Structure

- Create `cheapy/providers/skyscanner/__init__.py`: package marker with a short experimental-status docstring and no provider exports.
- Create `cheapy/providers/skyscanner/scan_graphql_bundles.py`: executable scanner module and importable helper functions.
- Create `tests/skyscanner/__init__.py`: test package marker.
- Create `tests/skyscanner/test_scan_graphql_bundles.py`: offline unit and CLI tests for parser, matcher, fetcher behavior through fakes, scan JSON shape, and fatal stderr JSON.
- Modify `tests/test_providers.py`: assert the Skyscanner experimental package is not discovered or loaded as a provider.
- Modify `tests/test_cli.py`: assert `cheapy providers list` and `cheapy providers test` do not include `skyscanner`.
- Modify `tests/test_mcp.py`: assert no Skyscanner discovery tool is exposed.
- Modify `tests/test_package_data.py`: assert the package imports from a built wheel and no Skyscanner manifest is packaged.

## Task 1: Package Skeleton, URL Validation, And Script Discovery

**Files:**
- Create: `cheapy/providers/skyscanner/__init__.py`
- Create: `cheapy/providers/skyscanner/scan_graphql_bundles.py`
- Create: `tests/skyscanner/__init__.py`
- Create: `tests/skyscanner/test_scan_graphql_bundles.py`

- [ ] **Step 1: Write failing tests for URL validation and same-origin script discovery**

Create `tests/skyscanner/__init__.py`:

```python
"""Skyscanner experimental scanner tests."""
```

Create `tests/skyscanner/test_scan_graphql_bundles.py`:

```python
from __future__ import annotations

import pytest

from cheapy.providers.skyscanner import scan_graphql_bundles as scanner


def test_validate_https_url_accepts_https_url_with_host() -> None:
    assert (
        scanner.validate_https_url(
            "https://www.skyscanner.net/transport/flights/sgn/bkk/"
        )
        == "https://www.skyscanner.net/transport/flights/sgn/bkk/"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.skyscanner.net/transport/flights/sgn/bkk/",
        "https:///missing-host",
        "not-a-url",
    ],
)
def test_validate_https_url_rejects_invalid_or_non_https_urls(url: str) -> None:
    with pytest.raises(scanner.ScannerFatalError) as exc_info:
        scanner.validate_https_url(url)

    error = exc_info.value.to_error_payload()
    assert error["schema_version"] == "1"
    assert error["error"] is True
    assert error["error_type"] == "invalid_url"
    assert error["message"] == "Entry URL must be an HTTPS URL with a host."
    assert error["details"] == {"target_url": url}


def test_origin_tuple_normalizes_default_https_port() -> None:
    assert scanner.origin_tuple("https://www.skyscanner.net/path") == (
        "https",
        "www.skyscanner.net",
        443,
    )
    assert scanner.origin_tuple("https://www.skyscanner.net:443/path") == (
        "https",
        "www.skyscanner.net",
        443,
    )


def test_discover_same_origin_scripts_resolves_and_filters_sources() -> None:
    html = """
    <html>
      <head>
        <script src="/assets/app.js"></script>
        <script src="https://www.skyscanner.net/assets/vendor.js"></script>
        <script src="https://cdn.example.test/analytics.js"></script>
        <script>window.inline = true;</script>
      </head>
    </html>
    """

    discovery = scanner.discover_same_origin_scripts(
        html,
        final_entry_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
    )

    assert discovery.script_count == 3
    assert discovery.same_origin_urls == [
        "https://www.skyscanner.net/assets/app.js",
        "https://www.skyscanner.net/assets/vendor.js",
    ]
    assert discovery.skipped_cross_origin_script_count == 1
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cheapy.providers.skyscanner'`.

- [ ] **Step 3: Create the experimental package and helper implementation**

Create `cheapy/providers/skyscanner/__init__.py`:

```python
"""Experimental Skyscanner discovery utilities.

This package is intentionally not a Cheapy provider. It has no manifest and is
not loaded by the provider registry.
"""

from __future__ import annotations
```

Create `cheapy/providers/skyscanner/scan_graphql_bundles.py`:

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit the skeleton and parser helpers**

Run:

```bash
git add cheapy/providers/skyscanner/__init__.py cheapy/providers/skyscanner/scan_graphql_bundles.py tests/skyscanner/__init__.py tests/skyscanner/test_scan_graphql_bundles.py
git commit -m "feat: add Skyscanner scanner skeleton" -m "AI: Codex (GPT-5)"
```

## Task 2: GraphQL Candidate Extraction

**Files:**
- Modify: `cheapy/providers/skyscanner/scan_graphql_bundles.py`
- Modify: `tests/skyscanner/test_scan_graphql_bundles.py`

- [ ] **Step 1: Write failing tests for GraphQL signal extraction**

Append these tests to `tests/skyscanner/test_scan_graphql_bundles.py`:

```python
def test_extract_graphql_matches_finds_operation_names() -> None:
    text = """
    query FlightSearchQuery($input: FlightSearchInput!) { search(input: $input) { id } }
    mutation TrackFlightSearchMutation { track { ok } }
    subscription PriceAlertSubscription { priceChanged { amount } }
    """

    matches = scanner.extract_graphql_matches(text)

    assert matches["operation_names"] == [
        "FlightSearchQuery",
        "PriceAlertSubscription",
        "TrackFlightSearchMutation",
    ]


def test_extract_graphql_matches_finds_persisted_query_ids() -> None:
    text = """
    {"sha256Hash":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}
    {"operationId":"flightSearch_abc12345"}
    {"queryId":"query_67890_xyz"}
    """

    matches = scanner.extract_graphql_matches(text)

    assert matches["persisted_query_ids"] == [
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "flightSearch_abc12345",
        "query_67890_xyz",
    ]


def test_extract_graphql_matches_finds_graphql_paths_and_deduplicates() -> None:
    text = """
    fetch("/graphql");
    fetch('/graphql');
    const endpoint = "/g/conductor/graphql";
    const url = "https://www.skyscanner.net/graphql";
    """

    matches = scanner.extract_graphql_matches(text)

    assert matches["graphql_paths"] == [
        "/g/conductor/graphql",
        "/graphql",
        "https://www.skyscanner.net/graphql",
    ]
```

- [ ] **Step 2: Run the new extraction tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py::test_extract_graphql_matches_finds_operation_names tests/skyscanner/test_scan_graphql_bundles.py::test_extract_graphql_matches_finds_persisted_query_ids tests/skyscanner/test_scan_graphql_bundles.py::test_extract_graphql_matches_finds_graphql_paths_and_deduplicates -v
```

Expected: FAIL with `AttributeError: module 'cheapy.providers.skyscanner.scan_graphql_bundles' has no attribute 'extract_graphql_matches'`.

- [ ] **Step 3: Add regex extraction helpers**

Modify `cheapy/providers/skyscanner/scan_graphql_bundles.py` to add `re` import:

```python
import re
```

Add these regex constants and helper functions after `same_origin`:

```python
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
```

- [ ] **Step 4: Run scanner tests and verify they pass**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit GraphQL extraction**

Run:

```bash
git add cheapy/providers/skyscanner/scan_graphql_bundles.py tests/skyscanner/test_scan_graphql_bundles.py
git commit -m "feat: extract Skyscanner GraphQL bundle signals" -m "AI: Codex (GPT-5)"
```

## Task 3: Bounded HTTPS Fetching And Redirect Safety

**Files:**
- Modify: `cheapy/providers/skyscanner/scan_graphql_bundles.py`
- Modify: `tests/skyscanner/test_scan_graphql_bundles.py`

- [ ] **Step 1: Write failing tests for bounded fetches and redirect blocking**

Append these fakes and tests to `tests/skyscanner/test_scan_graphql_bundles.py`:

```python
class FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, name: str, default: str = "") -> str:
        return self._values.get(name.lower(), default)


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int,
        content_type: str,
        body: bytes,
    ) -> None:
        self.url = url
        self.status = status
        self.headers = FakeHeaders({"content-type": content_type})
        self._body = body

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._body
        return self._body[:size]


class FakeOpener:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.requests: list[object] = []
        self.timeouts: list[float] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_fetch_url_reads_at_most_byte_limit_plus_one() -> None:
    opener = FakeOpener(
        FakeResponse(
            url="https://www.skyscanner.net/assets/app.js",
            status=200,
            content_type="application/javascript",
            body=b"abcdef",
        )
    )

    result = scanner.fetch_url(
        "https://www.skyscanner.net/assets/app.js",
        timeout_seconds=3,
        max_bytes=5,
        allowed_origin_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
        opener=opener,
    )

    assert isinstance(result, scanner.FetchSuccess)
    assert result.body == b"abcde"
    assert result.truncated is True
    assert result.status_code == 200
    assert result.content_type == "application/javascript"
    assert result.final_url == "https://www.skyscanner.net/assets/app.js"
    assert opener.timeouts == [3]


def test_fetch_url_blocks_cross_origin_final_url() -> None:
    opener = FakeOpener(
        FakeResponse(
            url="https://evil.example.test/assets/app.js",
            status=200,
            content_type="application/javascript",
            body=b"console.log(1)",
        )
    )

    result = scanner.fetch_url(
        "https://www.skyscanner.net/assets/app.js",
        timeout_seconds=3,
        max_bytes=100,
        allowed_origin_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
        opener=opener,
    )

    assert isinstance(result, scanner.FetchFailure)
    assert result.error_type == "cross_origin_redirect"
    assert result.url == "https://www.skyscanner.net/assets/app.js"
    assert result.details == {"final_url": "https://evil.example.test/assets/app.js"}


def test_fetch_url_reports_network_failure() -> None:
    opener = FakeOpener(TimeoutError("slow"))

    result = scanner.fetch_url(
        "https://www.skyscanner.net/assets/app.js",
        timeout_seconds=3,
        max_bytes=100,
        allowed_origin_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
        opener=opener,
    )

    assert isinstance(result, scanner.FetchFailure)
    assert result.error_type == "fetch_failed"
    assert result.message == "Fetch failed."
    assert result.details == {"exception_type": "TimeoutError"}


def test_same_origin_redirect_handler_blocks_cross_origin_redirect() -> None:
    handler = scanner.SameOriginRedirectHandler("https://www.skyscanner.net/page")

    with pytest.raises(scanner.CrossOriginRedirectError) as exc_info:
        handler.redirect_request(
            req=object(),
            fp=object(),
            code=302,
            msg="Found",
            headers={},
            newurl="https://evil.example.test/assets/app.js",
        )

    assert exc_info.value.new_url == "https://evil.example.test/assets/app.js"
```

- [ ] **Step 2: Run the fetch tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py::test_fetch_url_reads_at_most_byte_limit_plus_one tests/skyscanner/test_scan_graphql_bundles.py::test_fetch_url_blocks_cross_origin_final_url tests/skyscanner/test_scan_graphql_bundles.py::test_fetch_url_reports_network_failure tests/skyscanner/test_scan_graphql_bundles.py::test_same_origin_redirect_handler_blocks_cross_origin_redirect -v
```

Expected: FAIL with `AttributeError` for `fetch_url` or fetch result classes.

- [ ] **Step 3: Add fetch result models and bounded fetch implementation**

Modify `cheapy/providers/skyscanner/scan_graphql_bundles.py` imports:

```python
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
```

Add these dataclasses after `ScriptDiscovery`:

```python
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
```

Add this helper after `discover_same_origin_scripts`:

```python
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
```

- [ ] **Step 4: Run fetch tests and existing scanner tests**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit bounded fetching**

Run:

```bash
git add cheapy/providers/skyscanner/scan_graphql_bundles.py tests/skyscanner/test_scan_graphql_bundles.py
git commit -m "feat: add bounded Skyscanner bundle fetching" -m "AI: Codex (GPT-5)"
```

## Task 4: Scan Orchestration And Stable JSON Shape

**Files:**
- Modify: `cheapy/providers/skyscanner/scan_graphql_bundles.py`
- Modify: `tests/skyscanner/test_scan_graphql_bundles.py`

- [ ] **Step 1: Write failing tests for successful scans, bundle caps, and bundle-level errors**

Append these tests to `tests/skyscanner/test_scan_graphql_bundles.py`:

```python
def _success(
    *,
    url: str,
    content_type: str,
    body: bytes,
    status_code: int = 200,
    truncated: bool = False,
) -> scanner.FetchSuccess:
    return scanner.FetchSuccess(
        url=url,
        final_url=url,
        status_code=status_code,
        content_type=content_type,
        body=body,
        truncated=truncated,
    )


def test_scan_url_returns_stable_json_shape_for_no_match_scan() -> None:
    responses = {
        "https://www.skyscanner.net/transport/flights/sgn/bkk/": _success(
            url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
            content_type="text/html; charset=utf-8",
            body=b'<script src="/assets/app.js"></script>',
        ),
        "https://www.skyscanner.net/assets/app.js": _success(
            url="https://www.skyscanner.net/assets/app.js",
            content_type="application/javascript",
            body=b"console.log('hello');",
        ),
    }

    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        return responses[url]

    payload = scanner.scan_url(
        "https://www.skyscanner.net/transport/flights/sgn/bkk/",
        max_bundles=20,
        max_bytes_per_bundle=1000,
        timeout_seconds=15,
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    assert payload == {
        "schema_version": "1",
        "target_url": "https://www.skyscanner.net/transport/flights/sgn/bkk/",
        "fetched_at": "2026-05-18T00:00:00Z",
        "entry": {
            "status_code": 200,
            "final_url": "https://www.skyscanner.net/transport/flights/sgn/bkk/",
            "content_type": "text/html; charset=utf-8",
            "script_count": 1,
            "same_origin_script_count": 1,
            "skipped_cross_origin_script_count": 0,
        },
        "limits": {
            "max_bundles": 20,
            "max_bytes_per_bundle": 1000,
            "timeout_seconds": 15,
        },
        "bundles": [
            {
                "url": "https://www.skyscanner.net/assets/app.js",
                "final_url": "https://www.skyscanner.net/assets/app.js",
                "status_code": 200,
                "content_type": "application/javascript",
                "bytes_scanned": 21,
                "truncated": False,
                "matches": {
                    "operation_names": [],
                    "persisted_query_ids": [],
                    "graphql_paths": [],
                },
            }
        ],
        "errors": [],
    }


def test_scan_url_applies_bundle_cap_and_reports_graphql_matches() -> None:
    html = b"""
    <script src="/assets/a.js"></script>
    <script src="/assets/b.js"></script>
    """
    responses = {
        "https://www.skyscanner.net/page": _success(
            url="https://www.skyscanner.net/page",
            content_type="text/html",
            body=html,
        ),
        "https://www.skyscanner.net/assets/a.js": _success(
            url="https://www.skyscanner.net/assets/a.js",
            content_type="application/javascript",
            body=b'query FlightSearchQuery { search { id } } fetch("/graphql")',
        ),
    }

    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        return responses[url]

    payload = scanner.scan_url(
        "https://www.skyscanner.net/page",
        max_bundles=1,
        max_bytes_per_bundle=1000,
        timeout_seconds=15,
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    assert len(payload["bundles"]) == 1
    assert payload["entry"]["same_origin_script_count"] == 2
    assert payload["bundles"][0]["matches"] == {
        "operation_names": ["FlightSearchQuery"],
        "persisted_query_ids": [],
        "graphql_paths": ["/graphql"],
    }


def test_scan_url_reports_bundle_failure_and_continues() -> None:
    responses = {
        "https://www.skyscanner.net/page": _success(
            url="https://www.skyscanner.net/page",
            content_type="text/html",
            body=b'<script src="/assets/a.js"></script><script src="/assets/b.js"></script>',
        ),
        "https://www.skyscanner.net/assets/a.js": scanner.FetchFailure(
            error_type="fetch_failed",
            message="Fetch failed.",
            url="https://www.skyscanner.net/assets/a.js",
            details={"exception_type": "TimeoutError"},
        ),
        "https://www.skyscanner.net/assets/b.js": _success(
            url="https://www.skyscanner.net/assets/b.js",
            content_type="application/javascript",
            body=b"query FlightSearchQuery { search { id } }",
        ),
    }

    def fake_fetcher(
        url: str,
        **kwargs: object,
    ) -> scanner.FetchSuccess | scanner.FetchFailure:
        return responses[url]

    payload = scanner.scan_url(
        "https://www.skyscanner.net/page",
        max_bundles=20,
        max_bytes_per_bundle=1000,
        timeout_seconds=15,
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    assert payload["errors"] == [
        {
            "scope": "bundle",
            "error_type": "bundle_fetch_failed",
            "message": "Fetch failed.",
            "url": "https://www.skyscanner.net/assets/a.js",
            "status_code": None,
            "details": {"exception_type": "TimeoutError"},
        }
    ]
    assert [bundle["url"] for bundle in payload["bundles"]] == [
        "https://www.skyscanner.net/assets/b.js"
    ]


def test_scan_url_rejects_non_html_entry_response() -> None:
    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        return _success(
            url=url,
            content_type="application/json",
            body=b"{}",
        )

    with pytest.raises(scanner.ScannerFatalError) as exc_info:
        scanner.scan_url(
            "https://www.skyscanner.net/page",
            max_bundles=20,
            max_bytes_per_bundle=1000,
            timeout_seconds=15,
            fetcher=fake_fetcher,
            now=lambda: "2026-05-18T00:00:00Z",
        )

    assert exc_info.value.to_error_payload()["error_type"] == (
        "unsupported_entry_content_type"
    )


def test_scan_url_maps_entry_fetch_failure() -> None:
    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchFailure:
        return scanner.FetchFailure(
            error_type="fetch_failed",
            message="Fetch failed.",
            url=url,
            details={"exception_type": "TimeoutError"},
        )

    with pytest.raises(scanner.ScannerFatalError) as exc_info:
        scanner.scan_url(
            "https://www.skyscanner.net/page",
            max_bundles=20,
            max_bytes_per_bundle=1000,
            timeout_seconds=15,
            fetcher=fake_fetcher,
            now=lambda: "2026-05-18T00:00:00Z",
        )

    assert exc_info.value.to_error_payload()["error_type"] == "entry_fetch_failed"
```

- [ ] **Step 2: Run orchestration tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py::test_scan_url_returns_stable_json_shape_for_no_match_scan tests/skyscanner/test_scan_graphql_bundles.py::test_scan_url_applies_bundle_cap_and_reports_graphql_matches tests/skyscanner/test_scan_graphql_bundles.py::test_scan_url_reports_bundle_failure_and_continues tests/skyscanner/test_scan_graphql_bundles.py::test_scan_url_rejects_non_html_entry_response tests/skyscanner/test_scan_graphql_bundles.py::test_scan_url_maps_entry_fetch_failure -v
```

Expected: FAIL with `AttributeError` for `scan_url`.

- [ ] **Step 3: Add scan orchestration**

Modify `cheapy/providers/skyscanner/scan_graphql_bundles.py` imports:

```python
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol
```

Add these helpers after `fetch_url`:

```python
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
            **(failure.details or {}),
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
        "details": failure.details or {},
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
```

- [ ] **Step 4: Run scanner tests**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit scan orchestration**

Run:

```bash
git add cheapy/providers/skyscanner/scan_graphql_bundles.py tests/skyscanner/test_scan_graphql_bundles.py
git commit -m "feat: orchestrate Skyscanner bundle scan" -m "AI: Codex (GPT-5)"
```

## Task 5: Module CLI JSON Output

**Files:**
- Modify: `cheapy/providers/skyscanner/scan_graphql_bundles.py`
- Modify: `tests/skyscanner/test_scan_graphql_bundles.py`

- [ ] **Step 1: Write failing tests for CLI stdout and stderr JSON**

Append these tests to `tests/skyscanner/test_scan_graphql_bundles.py`:

```python
def test_main_prints_success_payload_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        if url.endswith("/page"):
            return _success(
                url=url,
                content_type="text/html",
                body=b'<script src="/assets/app.js"></script>',
            )
        return _success(
            url=url,
            content_type="application/javascript",
            body=b"query FlightSearchQuery { search { id } }",
        )

    exit_code = scanner.main(
        [
            "--url",
            "https://www.skyscanner.net/page",
            "--max-bundles",
            "1",
            "--max-bytes-per-bundle",
            "1000",
            "--timeout-seconds",
            "3",
        ],
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.stderr == ""
    assert '"schema_version": "1"' in captured.stdout
    assert '"FlightSearchQuery"' in captured.stdout


def test_main_prints_fatal_error_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = scanner.main(["--url", "http://example.test"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.stdout == ""
    assert '"error": true' in captured.stderr
    assert '"error_type": "invalid_url"' in captured.stderr


def test_main_prints_missing_url_as_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = scanner.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.stdout == ""
    assert '"error": true' in captured.stderr
    assert '"error_type": "invalid_url"' in captured.stderr
```

- [ ] **Step 2: Run CLI tests and verify they fail**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py::test_main_prints_success_payload_to_stdout tests/skyscanner/test_scan_graphql_bundles.py::test_main_prints_fatal_error_to_stderr tests/skyscanner/test_scan_graphql_bundles.py::test_main_prints_missing_url_as_json_error -v
```

Expected: FAIL with `AttributeError` for `main`.

- [ ] **Step 3: Add argparse main and module entrypoint**

Modify `cheapy/providers/skyscanner/scan_graphql_bundles.py` imports:

```python
import argparse
import json
import sys
```

Add this function near the end of the file:

```python
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
```

- [ ] **Step 4: Run all scanner tests**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py -v
```

Expected: PASS.

- [ ] **Step 5: Run module help manually**

Run:

```bash
uv run python -m cheapy.providers.skyscanner.scan_graphql_bundles --help
```

Expected: exit 0 and output includes `--url`, `--max-bundles`, `--max-bytes-per-bundle`, and `--timeout-seconds`.

- [ ] **Step 6: Commit module CLI**

Run:

```bash
git add cheapy/providers/skyscanner/scan_graphql_bundles.py tests/skyscanner/test_scan_graphql_bundles.py
git commit -m "feat: add Skyscanner scanner module CLI" -m "AI: Codex (GPT-5)"
```

## Task 6: Provider, CLI, MCP, And Wheel Isolation Regression Tests

**Files:**
- Modify: `tests/test_providers.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_mcp.py`
- Modify: `tests/test_package_data.py`

- [ ] **Step 1: Add provider registry isolation test**

In `tests/test_providers.py`, add this test after `test_traveloka_manifest_is_discovered_from_package_resources`:

```python
def test_skyscanner_experimental_scanner_is_not_discovered_as_provider() -> None:
    manifests = discover_provider_manifests()

    assert "skyscanner" not in [manifest.name for manifest in manifests]
    assert "skyscanner" not in [
        provider.name for provider in registry.load_search_providers()
    ]
```

- [ ] **Step 2: Add CLI isolation assertions**

In `tests/test_cli.py`, update `test_providers_list_prints_json` after the line that assigns `providers`:

```python
    assert "skyscanner" not in providers
```

Update `test_providers_test_prints_json` after the line that assigns `providers`:

```python
    assert "skyscanner" not in providers
```

- [ ] **Step 3: Add MCP isolation assertion**

In `tests/test_mcp.py`, add this test after `test_mcp_lists_only_search_cheapest_flights_tool`:

```python
def test_mcp_does_not_expose_skyscanner_discovery_tool() -> None:
    server = create_mcp_server()

    assert server._tool_manager.get_tool("skyscanner_graphql_bundle_scan") is None
```

- [ ] **Step 4: Add wheel packaging assertions**

In `tests/test_package_data.py`, update the first archive assertions after Traveloka manifest assertions:

```python
    assert "cheapy/providers/skyscanner/__init__.py" in names
    assert "cheapy/providers/skyscanner/scan_graphql_bundles.py" in names
    assert "cheapy/providers/skyscanner/manifest.toml" not in names
```

Update the `resource_script` string after the Traveloka manifest read:

```python
skyscanner_root = files("cheapy.providers").joinpath("skyscanner")
assert skyscanner_root.joinpath("__init__.py").is_file()
assert skyscanner_root.joinpath("scan_graphql_bundles.py").is_file()
assert not skyscanner_root.joinpath("manifest.toml").is_file()
```

Update the installed CLI provider list assertions after Traveloka assertions:

```python
    assert "skyscanner" not in providers
```

Update the installed `providers test` assertions after Traveloka assertions:

```python
    assert "skyscanner" not in providers
```

- [ ] **Step 5: Run focused isolation tests**

Run:

```bash
uv run pytest tests/test_providers.py::test_skyscanner_experimental_scanner_is_not_discovered_as_provider tests/test_cli.py::test_providers_list_prints_json tests/test_cli.py::test_providers_test_prints_json tests/test_mcp.py::test_mcp_does_not_expose_skyscanner_discovery_tool -v
```

Expected: PASS.

- [ ] **Step 6: Run package data test**

Run:

```bash
uv run pytest tests/test_package_data.py::test_built_wheel_can_load_packaged_airport_and_provider_data -v
```

Expected: PASS. If this fails because the isolated build environment cannot resolve already-locked packages offline, run `uv sync --extra dev` once and retry the same command.

- [ ] **Step 7: Commit isolation coverage**

Run:

```bash
git add tests/test_providers.py tests/test_cli.py tests/test_mcp.py tests/test_package_data.py
git commit -m "test: keep Skyscanner scanner outside provider registry" -m "AI: Codex (GPT-5)"
```

## Task 7: Final Verification

**Files:**
- Verify: all files changed in Tasks 1-6.

- [ ] **Step 1: Run scanner tests**

Run:

```bash
uv run pytest tests/skyscanner/test_scan_graphql_bundles.py -v
```

Expected: PASS.

- [ ] **Step 2: Run provider, CLI, MCP, and package regression tests**

Run:

```bash
uv run pytest tests/test_providers.py tests/test_cli.py tests/test_mcp.py tests/test_package_data.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full offline test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS. This must not run live provider tests because live tests are gated behind `CHEAPY_RUN_LIVE_TESTS=1`.

- [ ] **Step 4: Verify module help still works**

Run:

```bash
uv run python -m cheapy.providers.skyscanner.scan_graphql_bundles --help
```

Expected: exit 0 and help text lists `--url`.

- [ ] **Step 5: Inspect git status**

Run:

```bash
git status --short
```

Expected: only intentional changes from this implementation are present. Pre-existing unrelated deletions such as `Cheapy_PROJECT_STARTER_PROMPT.md` and `cheapy verdict report.md` may still appear and must not be staged with this work.
