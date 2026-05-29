# Cheapy Shared Cloakbrowser Session Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared local Cloakbrowser bootstrap layer, make Skyscanner bootstrap cookies/user-agent without a required env cookie, and let Traveloka replay same-call harvested browser requests with safe browser-capture fallback.

**Architecture:** Add provider-neutral `cheapy.browser_bootstrap` primitives with fake-browser tests and no provider knowledge. Skyscanner gets a provider-local session manager that converts bootstrap sessions into `SkyscannerConfig`. Traveloka gets a replay-from-harvest path that pairs captured first-party request/response exchanges, replays through an injectable HTTP client, and falls back only to the same exchange's captured payload.

**Tech Stack:** Python 3.12, dataclasses, Pydantic Contract V1, pytest, uv, Cloakbrowser through one local wrapper, existing curl-backed Skyscanner adapter, existing Traveloka normalization.

---

## Commit Policy

The worktree may contain unrelated changes from other agents or users. Before each commit, run:

```bash
git status --short
git diff --cached --name-status
```

Stage only files listed in the current task. Do not revert unrelated dirty changes.

Use this commit body for shared AI commits:

```text
AI-Generated-By: GPT-5 Codex
```

## Reference Material

- Project instructions: `AGENTS.md`
- Cheapy skill: `.codex/skills/cheapy/SKILL.md`
- Design spec: `docs/superpowers/specs/2026-05-29-cheapy-shared-cloakbrowser-session-bootstrap-design.md`
- Search orchestration: `cheapy/search.py`
- Skyscanner adapter/provider: `cheapy/providers/skyscanner/adapter.py`, `cheapy/providers/skyscanner/provider.py`
- Traveloka adapter/session/capture/workflow/provider: `cheapy/providers/traveloka/adapter.py`, `cheapy/providers/traveloka/session.py`, `cheapy/providers/traveloka/capture.py`, `cheapy/providers/traveloka/workflow.py`, `cheapy/providers/traveloka/provider.py`

## File Structure

Create:

- `cheapy/browser_bootstrap/__init__.py`: public exports for shared bootstrap types and functions.
- `cheapy/browser_bootstrap/types.py`: provider-neutral dataclasses and protocol type aliases.
- `cheapy/browser_bootstrap/errors.py`: safe neutral bootstrap exceptions and contexts.
- `cheapy/browser_bootstrap/cookies.py`: Playwright-compatible cookie serialization.
- `cheapy/browser_bootstrap/cloak.py`: only runtime import of `cloakbrowser`; launch, cookie bootstrap, request/response capture.
- `tests/browser_bootstrap/__init__.py`: package marker.
- `tests/browser_bootstrap/fakes.py`: fake browser/context/page/request/response objects.
- `tests/browser_bootstrap/test_cookies.py`: cookie serialization tests.
- `tests/browser_bootstrap/test_cloak.py`: fake-browser bootstrap/capture/error/deadline tests.
- `cheapy/providers/skyscanner/session.py`: provider-local bootstrap session manager and cache.
- `tests/skyscanner/test_session.py`: Skyscanner session manager tests.
- `cheapy/providers/traveloka/replay.py`: Traveloka replay request selection, header allowlist, replay HTTP client protocol, replay result mapping.
- `tests/traveloka/test_replay.py`: Traveloka replay-from-harvest tests.

Modify:

- `cheapy/providers/skyscanner/adapter.py`: add config construction from bootstrap session.
- `cheapy/providers/skyscanner/provider.py`: inject/use session manager, preserve cache across timeout clones, refresh stale sessions safely.
- `tests/skyscanner/test_adapter.py`: cover bootstrap config construction and env override redaction.
- `tests/skyscanner/test_provider.py`: cover bootstrap normal path, cache clone, force refresh, safe failures.
- `cheapy/providers/traveloka/adapter.py`: use shared `launch_browser`; add replay adapter path with injectable capture/replay dependencies.
- `cheapy/providers/traveloka/session.py`: use shared launcher type and keep behavior compatible.
- `tests/traveloka/test_adapter.py`: update default launcher test.
- `tests/traveloka/test_capture.py`: add deterministic exchange expectations where relevant.
- `tests/traveloka/test_provider.py`: cover replay success/fallback/error provider mapping.
- `tests/test_package_data.py`: include `cheapy/browser_bootstrap` package files if wheel checks require explicit paths.
- `tests/test_cli.py`, `tests/test_mcp.py`, `tests/test_markdown_report.py`, `tests/test_search.py`: add no-leak regression assertions only if current tests do not already cover the new sensitive names.

## Sensitive Test Helper

Use this helper in new tests that assert no public output leaks browser material:

```python
def assert_no_bootstrap_secrets(value: object) -> None:
    import json

    text = json.dumps(value, sort_keys=True, default=str).lower()
    for token in (
        "secret-cookie",
        "cookie:",
        "headers",
        "request_body",
        "post_data",
        "raw_payload",
        "sessionid",
        "challenge",
        "datadome",
        "aws-waf-token",
        "tvl=",
        "tvo=",
        "tvs=",
        "transport_deeplink",
        "mozilla/5.0 secret",
        "https://www.traveloka.com/api/v2/flight/search/poll?secret=1",
    ):
        assert token not in text
```

## Task 0: Preflight

**Files:**
- Read: `AGENTS.md`
- Read: `.codex/skills/cheapy/SKILL.md`
- Read: `docs/superpowers/specs/2026-05-29-cheapy-shared-cloakbrowser-session-bootstrap-design.md`

- [ ] **Step 1: Confirm branch and worktree**

Run:

```bash
git branch --show-current
git status --short
```

Expected: branch is `codex/local-sqlite-history-watchlist`. Record dirty files; do not revert unrelated changes.

- [ ] **Step 2: Run focused baseline tests**

Run:

```bash
uv run pytest tests/skyscanner/test_adapter.py tests/skyscanner/test_provider.py tests/traveloka/test_adapter.py tests/traveloka/test_session.py tests/traveloka/test_capture.py tests/traveloka/test_provider.py -v
```

Expected: PASS before edits. If a baseline test fails, record the failing test and failure message before changing code.

## Task 1: Shared Browser Bootstrap Package

**Files:**
- Create: `cheapy/browser_bootstrap/__init__.py`
- Create: `cheapy/browser_bootstrap/types.py`
- Create: `cheapy/browser_bootstrap/errors.py`
- Create: `cheapy/browser_bootstrap/cookies.py`
- Create: `cheapy/browser_bootstrap/cloak.py`
- Create: `tests/browser_bootstrap/__init__.py`
- Create: `tests/browser_bootstrap/fakes.py`
- Create: `tests/browser_bootstrap/test_cookies.py`
- Create: `tests/browser_bootstrap/test_cloak.py`
- Modify: `tests/test_package_data.py`

- [ ] **Step 1: Write failing cookie tests**

Create `tests/browser_bootstrap/__init__.py` as an empty file.

Create `tests/browser_bootstrap/test_cookies.py`:

```python
from __future__ import annotations

import pytest

from cheapy.browser_bootstrap.cookies import cookie_header_from_browser_cookies
from cheapy.browser_bootstrap.errors import BrowserBootstrapCookieUnavailable


def test_cookie_header_serializes_browser_cookies_without_repr_leak() -> None:
    cookies = [
        {"name": "datadome", "value": "secret-cookie", "domain": ".traveloka.com"},
        {"name": "tvl", "value": "session-secret", "domain": "www.traveloka.com"},
        {"name": "", "value": "ignored", "domain": "www.traveloka.com"},
    ]

    header = cookie_header_from_browser_cookies(cookies)

    assert header == "datadome=secret-cookie; tvl=session-secret"


def test_cookie_header_can_filter_by_domain_suffix() -> None:
    cookies = [
        {"name": "keep", "value": "one", "domain": ".skyscanner.com.sg"},
        {"name": "drop", "value": "two", "domain": ".example.test"},
    ]

    header = cookie_header_from_browser_cookies(
        cookies,
        domain_suffix="skyscanner.com.sg",
    )

    assert header == "keep=one"


def test_empty_cookie_header_raises_safe_error() -> None:
    with pytest.raises(BrowserBootstrapCookieUnavailable) as exc_info:
        cookie_header_from_browser_cookies([])

    assert exc_info.value.context.failure_type == "browser_cookie_unavailable"
    assert exc_info.value.context.phase == "cookie_read"
    assert "secret" not in str(exc_info.value).lower()
```

- [ ] **Step 2: Run cookie tests and verify failure**

Run:

```bash
uv run pytest tests/browser_bootstrap/test_cookies.py -v
```

Expected: FAIL with import errors for `cheapy.browser_bootstrap`.

- [ ] **Step 3: Implement bootstrap types, errors, and cookies**

Create `cheapy/browser_bootstrap/types.py`:

```python
"""Provider-neutral browser bootstrap data types."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


BrowserLauncher = Callable[..., object]
RequestPredicate = Callable[[str, str], bool]
ResponsePredicate = Callable[[str, int], bool]


@dataclass(frozen=True)
class BrowserBootstrapSession:
    cookie_header: str = field(repr=False)
    user_agent: str = field(repr=False)
    created_monotonic: float


@dataclass(frozen=True)
class CapturedRequest:
    url: str = field(repr=False)
    method: str
    sequence: int
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    post_data: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CapturedResponse:
    url: str = field(repr=False)
    status_code: int
    payload: object = field(repr=False)
    sequence: int


@dataclass(frozen=True)
class CapturedExchange:
    sequence: int
    captured_monotonic: float
    request: CapturedRequest = field(repr=False)
    response: CapturedResponse | None = field(default=None, repr=False)


@dataclass(frozen=True)
class BrowserNetworkCapture:
    cookie_header: str = field(repr=False)
    user_agent: str = field(repr=False)
    exchanges: tuple[CapturedExchange, ...] = field(repr=False)
    created_monotonic: float
```

Create `cheapy/browser_bootstrap/errors.py`:

```python
"""Safe provider-neutral browser bootstrap errors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserBootstrapErrorContext:
    failure_type: str
    phase: str
    http_status_code: int | None = None
    exception_type: str | None = None


class BrowserBootstrapError(Exception):
    def __init__(self, message_en: str, context: BrowserBootstrapErrorContext) -> None:
        super().__init__(message_en)
        self.message_en = message_en
        self.context = context


class BrowserBootstrapUnavailable(BrowserBootstrapError):
    pass


class BrowserBootstrapTimeout(BrowserBootstrapError):
    pass


class BrowserBootstrapBlocked(BrowserBootstrapError):
    pass


class BrowserBootstrapCookieUnavailable(BrowserBootstrapError):
    pass


class BrowserNetworkCaptureUnavailable(BrowserBootstrapError):
    pass


def unavailable_error(phase: str, exception_type: str | None = None) -> BrowserBootstrapUnavailable:
    return BrowserBootstrapUnavailable(
        "Browser bootstrap runtime is unavailable.",
        BrowserBootstrapErrorContext(
            failure_type="browser_bootstrap_failed",
            phase=phase,
            exception_type=exception_type,
        ),
    )


def timeout_error(phase: str, exception_type: str | None = None) -> BrowserBootstrapTimeout:
    return BrowserBootstrapTimeout(
        "Browser bootstrap timed out.",
        BrowserBootstrapErrorContext(
            failure_type="timeout",
            phase=phase,
            exception_type=exception_type,
        ),
    )


def blocked_error(phase: str, status_code: int) -> BrowserBootstrapBlocked:
    failure_type = "rate_limited" if status_code == 429 else "blocked"
    return BrowserBootstrapBlocked(
        "Browser bootstrap returned an access challenge.",
        BrowserBootstrapErrorContext(
            failure_type=failure_type,
            phase=phase,
            http_status_code=status_code,
        ),
    )


def cookie_unavailable_error() -> BrowserBootstrapCookieUnavailable:
    return BrowserBootstrapCookieUnavailable(
        "Browser bootstrap did not return usable cookies.",
        BrowserBootstrapErrorContext(
            failure_type="browser_cookie_unavailable",
            phase="cookie_read",
        ),
    )


def capture_unavailable_error() -> BrowserNetworkCaptureUnavailable:
    return BrowserNetworkCaptureUnavailable(
        "Browser bootstrap did not capture a usable network request.",
        BrowserBootstrapErrorContext(
            failure_type="network_capture_unavailable",
            phase="capture_wait",
        ),
    )
```

Create `cheapy/browser_bootstrap/cookies.py`:

```python
"""Cookie helpers for Playwright-compatible browser contexts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from cheapy.browser_bootstrap.errors import cookie_unavailable_error


def cookie_header_from_browser_cookies(
    cookies: Sequence[Mapping[str, object]],
    *,
    domain_suffix: str | None = None,
) -> str:
    parts: list[str] = []
    normalized_suffix = (
        domain_suffix.lower().lstrip(".") if domain_suffix is not None else None
    )
    for cookie in cookies:
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", "")).strip()
        if not name or not value:
            continue
        if normalized_suffix is not None:
            domain = str(cookie.get("domain", "")).lower().lstrip(".")
            if domain != normalized_suffix and not domain.endswith(f".{normalized_suffix}"):
                continue
        parts.append(f"{name}={value}")
    if not parts:
        raise cookie_unavailable_error()
    return "; ".join(parts)
```

Create `cheapy/browser_bootstrap/__init__.py`:

```python
"""Shared local browser bootstrap primitives."""

from __future__ import annotations

from cheapy.browser_bootstrap.cloak import (
    bootstrap_cookies,
    capture_first_party_requests,
    launch_browser,
)
from cheapy.browser_bootstrap.errors import (
    BrowserBootstrapBlocked,
    BrowserBootstrapCookieUnavailable,
    BrowserBootstrapError,
    BrowserBootstrapErrorContext,
    BrowserBootstrapTimeout,
    BrowserBootstrapUnavailable,
    BrowserNetworkCaptureUnavailable,
)
from cheapy.browser_bootstrap.types import (
    BrowserBootstrapSession,
    BrowserNetworkCapture,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
)

__all__ = [
    "BrowserBootstrapBlocked",
    "BrowserBootstrapCookieUnavailable",
    "BrowserBootstrapError",
    "BrowserBootstrapErrorContext",
    "BrowserBootstrapSession",
    "BrowserBootstrapTimeout",
    "BrowserBootstrapUnavailable",
    "BrowserNetworkCapture",
    "BrowserNetworkCaptureUnavailable",
    "CapturedExchange",
    "CapturedRequest",
    "CapturedResponse",
    "bootstrap_cookies",
    "capture_first_party_requests",
    "launch_browser",
]
```

Create `cheapy/browser_bootstrap/cloak.py` with only the launch wrapper for now:

```python
"""Cloakbrowser-backed local bootstrap helpers."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from cheapy.browser_bootstrap.types import BrowserLauncher


def launch_browser(**kwargs: object) -> object:
    captured_output = StringIO()
    with redirect_stdout(captured_output), redirect_stderr(captured_output):
        from cloakbrowser import launch

        return launch(**kwargs)
```

- [ ] **Step 4: Run cookie tests and verify pass**

Run:

```bash
uv run pytest tests/browser_bootstrap/test_cookies.py -v
```

Expected: PASS.

- [ ] **Step 5: Write fake browser and cloak tests**

Create `tests/browser_bootstrap/fakes.py`:

```python
from __future__ import annotations

from collections.abc import Mapping


class FakeRequest:
    def __init__(
        self,
        *,
        url: str,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        post_data: str | None = None,
    ) -> None:
        self.url = url
        self.method = method
        self.headers = dict(headers or {})
        self.post_data = post_data


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int = 200,
        payload: object | Exception = None,
    ) -> None:
        self.url = url
        self.status = status
        self._payload = payload if payload is not None else {"ok": True}

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakePage:
    def __init__(
        self,
        *,
        user_agent: str = "Mozilla/5.0 secret",
        requests: list[FakeRequest] | None = None,
        responses: list[FakeResponse] | None = None,
        goto_error: Exception | None = None,
    ) -> None:
        self.handlers: dict[str, object] = {}
        self.user_agent = user_agent
        self.requests = requests or []
        self.responses = responses or []
        self.goto_error = goto_error
        self.goto_calls: list[dict[str, object]] = []
        self.wait_calls: list[int] = []

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append({"url": url, "wait_until": wait_until, "timeout": timeout})
        if self.goto_error is not None:
            raise self.goto_error
        for request in self.requests:
            handler = self.handlers.get("request")
            if callable(handler):
                handler(request)
        for response in self.responses:
            handler = self.handlers.get("response")
            if callable(handler):
                handler(response)

    def evaluate(self, script: str) -> str:
        assert "navigator.userAgent" in script
        return self.user_agent

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls.append(milliseconds)


class FakeContext:
    def __init__(
        self,
        page: FakePage,
        *,
        cookies: list[dict[str, object]] | None = None,
    ) -> None:
        self.page = page
        self.cookies_value = cookies or [
            {"name": "session", "value": "secret-cookie", "domain": "www.traveloka.com"}
        ]
        self.closed = False
        self.context_kwargs: dict[str, object] | None = None

    def new_page(self) -> FakePage:
        return self.page

    def cookies(self) -> list[dict[str, object]]:
        return self.cookies_value

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    def new_context(self, **kwargs: object) -> FakeContext:
        self.context.context_kwargs = dict(kwargs)
        return self.context

    def close(self) -> None:
        self.closed = True
```

Create `tests/browser_bootstrap/test_cloak.py`:

```python
from __future__ import annotations

import pytest

from cheapy.browser_bootstrap import cloak
from cheapy.browser_bootstrap.errors import (
    BrowserBootstrapTimeout,
    BrowserNetworkCaptureUnavailable,
)

from .fakes import FakeBrowser, FakeContext, FakePage, FakeRequest, FakeResponse


def test_bootstrap_cookies_returns_redacted_session_and_closes_resources(monkeypatch) -> None:
    monkeypatch.setattr(cloak.time, "monotonic", lambda: 100.0)
    page = FakePage(user_agent="Mozilla/5.0 secret")
    context = FakeContext(page)
    browser = FakeBrowser(context)

    session = cloak.bootstrap_cookies(
        page_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
        deadline_monotonic=105.0,
        launch_browser=lambda **kwargs: browser,
    )

    assert session.cookie_header == "session=secret-cookie"
    assert session.user_agent == "Mozilla/5.0 secret"
    assert session.created_monotonic == 100.0
    assert "secret-cookie" not in repr(session)
    assert "Mozilla/5.0 secret" not in repr(session)
    assert context.closed is True
    assert browser.closed is True
    assert page.goto_calls[0]["timeout"] <= 5000


def test_capture_first_party_requests_pairs_request_and_response(monkeypatch) -> None:
    monkeypatch.setattr(cloak.time, "monotonic", lambda: 200.0)
    url = "https://www.traveloka.com/api/v2/flight/search/poll?secret=1"
    page = FakePage(
        requests=[
            FakeRequest(
                url=url,
                headers={"content-type": "application/json", "cookie": "secret-cookie"},
                post_data='{"searchId":"secret"}',
            )
        ],
        responses=[FakeResponse(url=url, payload={"data": {"searchResults": []}})],
    )
    browser = FakeBrowser(FakeContext(page))

    capture = cloak.capture_first_party_requests(
        page_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
        deadline_monotonic=205.0,
        request_predicate=lambda method, request_url: method == "POST" and "/poll" in request_url,
        response_predicate=lambda response_url, status: "/poll" in response_url and status == 200,
        launch_browser=lambda **kwargs: browser,
    )

    assert len(capture.exchanges) == 1
    exchange = capture.exchanges[0]
    assert exchange.sequence == 1
    assert exchange.request.method == "POST"
    assert exchange.response is not None
    assert exchange.response.payload == {"data": {"searchResults": []}}
    assert "secret-cookie" not in repr(capture)
    assert "searchId" not in repr(capture)
    assert url not in repr(capture)


def test_capture_without_matching_request_raises_safe_error(monkeypatch) -> None:
    monkeypatch.setattr(cloak.time, "monotonic", lambda: 300.0)
    page = FakePage(requests=[], responses=[])
    browser = FakeBrowser(FakeContext(page))

    with pytest.raises(BrowserNetworkCaptureUnavailable) as exc_info:
        cloak.capture_first_party_requests(
            page_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
            deadline_monotonic=301.0,
            request_predicate=lambda method, request_url: True,
            launch_browser=lambda **kwargs: browser,
        )

    assert exc_info.value.context.failure_type == "network_capture_unavailable"
    assert "traveloka.com" not in str(exc_info.value)


def test_navigation_timeout_maps_to_safe_timeout(monkeypatch) -> None:
    monkeypatch.setattr(cloak.time, "monotonic", lambda: 400.0)
    page = FakePage(goto_error=TimeoutError("secret timeout detail"))
    browser = FakeBrowser(FakeContext(page))

    with pytest.raises(BrowserBootstrapTimeout) as exc_info:
        cloak.bootstrap_cookies(
            page_url="https://www.skyscanner.com.sg",
            deadline_monotonic=401.0,
            launch_browser=lambda **kwargs: browser,
        )

    assert exc_info.value.context.failure_type == "timeout"
    assert exc_info.value.context.exception_type == "TimeoutError"
    assert "secret timeout detail" not in str(exc_info.value)
```

- [ ] **Step 6: Run cloak tests and verify failure**

Run:

```bash
uv run pytest tests/browser_bootstrap/test_cloak.py -v
```

Expected: FAIL because `bootstrap_cookies()` and `capture_first_party_requests()` are not implemented.

- [ ] **Step 7: Implement cloak helpers**

Replace `cheapy/browser_bootstrap/cloak.py` with:

```python
"""Cloakbrowser-backed local bootstrap helpers."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import time
from urllib.parse import urlparse

from cheapy.browser_bootstrap.cookies import cookie_header_from_browser_cookies
from cheapy.browser_bootstrap.errors import (
    BrowserBootstrapError,
    blocked_error,
    capture_unavailable_error,
    timeout_error,
    unavailable_error,
)
from cheapy.browser_bootstrap.types import (
    BrowserBootstrapSession,
    BrowserLauncher,
    BrowserNetworkCapture,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
    RequestPredicate,
    ResponsePredicate,
)


def launch_browser(**kwargs: object) -> object:
    captured_output = StringIO()
    with redirect_stdout(captured_output), redirect_stderr(captured_output):
        from cloakbrowser import launch

        return launch(**kwargs)


def bootstrap_cookies(
    *,
    page_url: str,
    deadline_monotonic: float,
    wait_until: str = "domcontentloaded",
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserBootstrapSession:
    browser, context = _open_context(
        deadline_monotonic=deadline_monotonic,
        user_agent=user_agent,
        launch_browser=launch_browser,
    )
    try:
        page = _new_page(context, deadline_monotonic)
        _goto(page, page_url, wait_until=wait_until, deadline_monotonic=deadline_monotonic)
        return _session_from_context(context, page, deadline_monotonic)
    finally:
        _close_quietly(context)
        _close_quietly(browser)


def capture_first_party_requests(
    *,
    page_url: str,
    deadline_monotonic: float,
    request_predicate: RequestPredicate,
    response_predicate: ResponsePredicate | None = None,
    wait_until: str = "domcontentloaded",
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserNetworkCapture:
    browser, context = _open_context(
        deadline_monotonic=deadline_monotonic,
        user_agent=user_agent,
        launch_browser=launch_browser,
    )
    exchanges: dict[str, CapturedExchange] = {}
    order: list[str] = []
    sequence = 0
    try:
        page = _new_page(context, deadline_monotonic)

        def handle_request(request: object) -> None:
            nonlocal sequence
            url = str(getattr(request, "url", ""))
            method = str(getattr(request, "method", "GET")).upper()
            if not request_predicate(method, url):
                return
            sequence += 1
            key = _exchange_key(url)
            headers = getattr(request, "headers", {}) or {}
            post_data = getattr(request, "post_data", None)
            captured = CapturedRequest(
                url=url,
                method=method,
                sequence=sequence,
                headers=dict(headers),
                post_data=post_data if isinstance(post_data, str) else None,
            )
            exchanges[key] = CapturedExchange(
                sequence=sequence,
                captured_monotonic=time.monotonic(),
                request=captured,
            )
            order.append(key)

        def handle_response(response: object) -> None:
            url = str(getattr(response, "url", ""))
            status = int(getattr(response, "status", 0))
            if response_predicate is not None and not response_predicate(url, status):
                return
            key = _exchange_key(url)
            exchange = exchanges.get(key)
            if exchange is None:
                return
            try:
                payload = response.json()  # type: ignore[attr-defined]
            except Exception:
                return
            captured = CapturedResponse(
                url=url,
                status_code=status,
                payload=payload,
                sequence=exchange.sequence,
            )
            exchanges[key] = CapturedExchange(
                sequence=exchange.sequence,
                captured_monotonic=exchange.captured_monotonic,
                request=exchange.request,
                response=captured,
            )

        page.on("request", handle_request)  # type: ignore[attr-defined]
        page.on("response", handle_response)  # type: ignore[attr-defined]
        _goto(page, page_url, wait_until=wait_until, deadline_monotonic=deadline_monotonic)
        selected = tuple(exchanges[key] for key in order if key in exchanges)
        if not selected:
            raise capture_unavailable_error()
        session = _session_from_context(context, page, deadline_monotonic)
        return BrowserNetworkCapture(
            cookie_header=session.cookie_header,
            user_agent=session.user_agent,
            exchanges=selected,
            created_monotonic=session.created_monotonic,
        )
    finally:
        _close_quietly(context)
        _close_quietly(browser)


def _open_context(
    *,
    deadline_monotonic: float,
    user_agent: str | None,
    launch_browser: BrowserLauncher | None,
) -> tuple[object, object]:
    launcher = launch_browser or globals()["launch_browser"]
    try:
        browser = launcher(headless=True, timeout=_remaining_timeout_ms(deadline_monotonic))
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        if _is_timeout_exception(exc):
            raise timeout_error("launch", type(exc).__name__) from None
        raise unavailable_error("launch", type(exc).__name__) from None
    try:
        context_kwargs = {"user_agent": user_agent} if user_agent is not None else {}
        context = browser.new_context(**context_kwargs)  # type: ignore[attr-defined]
        return browser, context
    except Exception as exc:
        _close_quietly(browser)
        if _is_timeout_exception(exc):
            raise timeout_error("context_page_setup", type(exc).__name__) from None
        raise unavailable_error("context_page_setup", type(exc).__name__) from None


def _new_page(context: object, deadline_monotonic: float) -> object:
    _remaining_timeout_ms(deadline_monotonic)
    try:
        return context.new_page()  # type: ignore[attr-defined]
    except Exception as exc:
        if _is_timeout_exception(exc):
            raise timeout_error("context_page_setup", type(exc).__name__) from None
        raise unavailable_error("context_page_setup", type(exc).__name__) from None


def _goto(page: object, page_url: str, *, wait_until: str, deadline_monotonic: float) -> None:
    try:
        page.goto(  # type: ignore[attr-defined]
            page_url,
            wait_until=wait_until,
            timeout=_remaining_timeout_ms(deadline_monotonic),
        )
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        status_code = getattr(exc, "status", None)
        if isinstance(status_code, int) and status_code in {401, 403, 429}:
            raise blocked_error("navigation", status_code) from None
        if _is_timeout_exception(exc):
            raise timeout_error("navigation", type(exc).__name__) from None
        raise unavailable_error("navigation", type(exc).__name__) from None


def _session_from_context(
    context: object,
    page: object,
    deadline_monotonic: float,
) -> BrowserBootstrapSession:
    _remaining_timeout_ms(deadline_monotonic)
    try:
        cookies = context.cookies()  # type: ignore[attr-defined]
        cookie_header = cookie_header_from_browser_cookies(cookies)
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        raise unavailable_error("cookie_read", type(exc).__name__) from None
    try:
        user_agent = page.evaluate("() => navigator.userAgent")  # type: ignore[attr-defined]
    except Exception as exc:
        raise unavailable_error("user_agent_read", type(exc).__name__) from None
    return BrowserBootstrapSession(
        cookie_header=cookie_header,
        user_agent=str(user_agent),
        created_monotonic=time.monotonic(),
    )


def _remaining_timeout_ms(deadline_monotonic: float) -> int:
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise timeout_error("capture_wait")
    return max(1, round(remaining * 1000))


def _exchange_key(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{parsed.query}"


def _is_timeout_exception(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def _close_quietly(target: object | None) -> None:
    if target is None:
        return
    close = getattr(target, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return
```

- [ ] **Step 8: Run shared bootstrap tests**

Run:

```bash
uv run pytest tests/browser_bootstrap -v
```

Expected: PASS.

- [ ] **Step 9: Update package-data tests**

Modify `tests/test_package_data.py` inside
`test_built_wheel_can_load_packaged_airport_and_provider_data()` and add these
wheel-name assertions after the Skyscanner provider assertions:

```python
assert "cheapy/browser_bootstrap/__init__.py" in names
assert "cheapy/browser_bootstrap/cloak.py" in names
assert "cheapy/browser_bootstrap/cookies.py" in names
assert "cheapy/browser_bootstrap/errors.py" in names
assert "cheapy/browser_bootstrap/types.py" in names
```

Add these resource-script assertions after the `skyscanner_root` assertions:

```python
bootstrap_root = files("cheapy").joinpath("browser_bootstrap")
assert bootstrap_root.joinpath("__init__.py").is_file()
assert bootstrap_root.joinpath("cloak.py").is_file()
assert bootstrap_root.joinpath("cookies.py").is_file()
assert bootstrap_root.joinpath("errors.py").is_file()
assert bootstrap_root.joinpath("types.py").is_file()
```

Run:

```bash
uv run pytest tests/test_package_data.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add cheapy/browser_bootstrap tests/browser_bootstrap tests/test_package_data.py
git diff --cached --name-status
git commit -m "feat: add shared browser bootstrap primitives" -m "AI-Generated-By: GPT-5 Codex"
```

## Task 2: Skyscanner Bootstrap Session Manager

**Files:**
- Create: `cheapy/providers/skyscanner/session.py`
- Create: `tests/skyscanner/test_session.py`
- Modify: `cheapy/providers/skyscanner/adapter.py`
- Modify: `cheapy/providers/skyscanner/provider.py`
- Modify: `tests/skyscanner/test_adapter.py`
- Modify: `tests/skyscanner/test_provider.py`

- [ ] **Step 1: Write Skyscanner session tests**

Create `tests/skyscanner/test_session.py`:

```python
from __future__ import annotations

import pytest

from cheapy.browser_bootstrap.errors import timeout_error
from cheapy.browser_bootstrap.types import BrowserBootstrapSession
from cheapy.models import ErrorCode
from cheapy.providers.skyscanner import session as sky_session


def _session(cookie: str = "sky=secret-cookie", user_agent: str = "Mozilla/5.0 secret") -> BrowserBootstrapSession:
    return BrowserBootstrapSession(
        cookie_header=cookie,
        user_agent=user_agent,
        created_monotonic=100.0,
    )


def test_manager_uses_env_cookie_override_without_bootstrap() -> None:
    calls: list[object] = []
    manager = sky_session.SkyscannerSessionManager(
        bootstrap_cookies=lambda **kwargs: calls.append(kwargs) or _session(),
        monotonic=lambda: 100.0,
    )

    config, source = manager.config_for_call(
        env={
            "CHEAPY_SKYSCANNER_COOKIE": "env-cookie=secret-cookie",
            "CHEAPY_SKYSCANNER_USER_AGENT": "Mozilla/5.0 secret",
        },
        timeout_seconds=5.0,
        deadline_monotonic=105.0,
    )

    assert source == "env"
    assert config.cookie == "env-cookie=secret-cookie"
    assert config.user_agent == "Mozilla/5.0 secret"
    assert calls == []


def test_manager_bootstraps_and_reuses_cached_session() -> None:
    calls: list[dict[str, object]] = []

    def fake_bootstrap(**kwargs: object) -> BrowserBootstrapSession:
        calls.append(kwargs)
        return _session()

    manager = sky_session.SkyscannerSessionManager(
        bootstrap_cookies=fake_bootstrap,
        monotonic=lambda: 120.0,
    )

    first, first_source = manager.config_for_call(
        env={},
        timeout_seconds=5.0,
        deadline_monotonic=125.0,
    )
    second, second_source = manager.config_for_call(
        env={},
        timeout_seconds=5.0,
        deadline_monotonic=125.0,
    )

    assert first.cookie == "sky=secret-cookie"
    assert second.cookie == "sky=secret-cookie"
    assert first_source == "bootstrap"
    assert second_source == "cache"
    assert len(calls) == 1
    assert calls[0]["deadline_monotonic"] == 125.0


def test_manager_refreshes_after_ttl() -> None:
    now = iter([100.0, 401.0])
    cookies = iter([_session("sky=first"), _session("sky=second")])
    manager = sky_session.SkyscannerSessionManager(
        bootstrap_cookies=lambda **kwargs: next(cookies),
        monotonic=lambda: next(now),
        ttl_seconds=300.0,
    )

    first, _ = manager.config_for_call(env={}, timeout_seconds=5.0, deadline_monotonic=105.0)
    second, source = manager.config_for_call(env={}, timeout_seconds=5.0, deadline_monotonic=406.0)

    assert first.cookie == "sky=first"
    assert second.cookie == "sky=second"
    assert source == "bootstrap"


def test_manager_maps_bootstrap_errors_to_skyscanner_provider_error() -> None:
    manager = sky_session.SkyscannerSessionManager(
        bootstrap_cookies=lambda **kwargs: (_ for _ in ()).throw(timeout_error("navigation")),
        monotonic=lambda: 100.0,
    )

    with pytest.raises(sky_session.SkyscannerSessionError) as exc_info:
        manager.config_for_call(env={}, timeout_seconds=5.0, deadline_monotonic=105.0)

    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.retryable is True
    assert "secret" not in str(exc_info.value)
```

- [ ] **Step 2: Run session tests and verify failure**

Run:

```bash
uv run pytest tests/skyscanner/test_session.py -v
```

Expected: FAIL with missing `cheapy.providers.skyscanner.session`.

- [ ] **Step 3: Add adapter config constructor**

Add this import to `cheapy/providers/skyscanner/adapter.py`:

```python
from cheapy.browser_bootstrap.types import BrowserBootstrapSession
```

Add this function near `config_from_env()`:

```python
def config_from_bootstrap_session(
    session: BrowserBootstrapSession,
    *,
    market: str = "SG",
    locale: str = "en-GB",
    currency: str = "SGD",
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    deadline_monotonic: float | None = None,
) -> SkyscannerConfig:
    cookie = session.cookie_header.strip()
    if not cookie:
        raise SkyscannerProviderError(
            failure_type="browser_cookie_unavailable",
            message_en="Skyscanner browser session did not return usable cookies.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
        )
    return SkyscannerConfig(
        base_url=base_url.rstrip("/"),
        market=market,
        locale=locale,
        currency=currency,
        cookie=cookie,
        timeout_seconds=timeout_seconds,
        user_agent=session.user_agent.strip() or DEFAULT_USER_AGENT,
        deadline_monotonic=deadline_monotonic,
    )
```

- [ ] **Step 4: Implement Skyscanner session manager**

Create `cheapy/providers/skyscanner/session.py`:

```python
"""Skyscanner browser bootstrap session manager."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import time

from cheapy.browser_bootstrap import (
    BrowserBootstrapBlocked,
    BrowserBootstrapCookieUnavailable,
    BrowserBootstrapError,
    BrowserBootstrapTimeout,
    BrowserBootstrapUnavailable,
    BrowserBootstrapSession,
    bootstrap_cookies,
)
from cheapy.models import ErrorCode
from cheapy.providers.skyscanner.adapter import (
    DEFAULT_BASE_URL,
    SkyscannerConfig,
    SkyscannerProviderError,
    config_from_bootstrap_session,
    config_from_env,
)


BootstrapCookies = Callable[..., BrowserBootstrapSession]


class SkyscannerSessionError(SkyscannerProviderError):
    pass


@dataclass
class _CachedSession:
    session: BrowserBootstrapSession
    expires_monotonic: float


class SkyscannerSessionManager:
    def __init__(
        self,
        *,
        bootstrap_cookies: BootstrapCookies = bootstrap_cookies,
        monotonic: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 300.0,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._bootstrap_cookies = bootstrap_cookies
        self._monotonic = monotonic
        self._ttl_seconds = ttl_seconds
        self._base_url = base_url.rstrip("/")
        self._cached: _CachedSession | None = None

    def config_for_call(
        self,
        *,
        env: Mapping[str, str],
        timeout_seconds: float,
        deadline_monotonic: float,
        force_refresh: bool = False,
    ) -> tuple[SkyscannerConfig, str]:
        if env.get("CHEAPY_SKYSCANNER_COOKIE", "").strip():
            return (
                config_from_env(
                    env,
                    timeout_seconds=timeout_seconds,
                    deadline_monotonic=deadline_monotonic,
                ),
                "env",
            )
        now = self._monotonic()
        if not force_refresh and self._cached is not None and self._cached.expires_monotonic > now:
            return (
                config_from_bootstrap_session(
                    self._cached.session,
                    timeout_seconds=timeout_seconds,
                    deadline_monotonic=deadline_monotonic,
                ),
                "cache",
            )
        try:
            session = self._bootstrap_cookies(
                page_url=self._bootstrap_url(),
                deadline_monotonic=deadline_monotonic,
                user_agent=(env.get("CHEAPY_SKYSCANNER_USER_AGENT") or None),
            )
        except BrowserBootstrapError as exc:
            raise _session_error_from_bootstrap_error(exc) from None
        self._cached = _CachedSession(
            session=session,
            expires_monotonic=now + self._ttl_seconds,
        )
        return (
            config_from_bootstrap_session(
                session,
                timeout_seconds=timeout_seconds,
                deadline_monotonic=deadline_monotonic,
            ),
            "bootstrap",
        )

    def _bootstrap_url(self) -> str:
        return f"{self._base_url}/transport/flights/"


def _session_error_from_bootstrap_error(exc: BrowserBootstrapError) -> SkyscannerSessionError:
    context = exc.context
    if isinstance(exc, BrowserBootstrapTimeout):
        code = ErrorCode.PROVIDER_TIMEOUT
        retryable = True
    elif isinstance(exc, BrowserBootstrapBlocked):
        code = (
            ErrorCode.PROVIDER_RATE_LIMITED
            if context.failure_type == "rate_limited"
            else ErrorCode.PROVIDER_BLOCKED
        )
        retryable = context.failure_type == "rate_limited"
    elif isinstance(exc, BrowserBootstrapCookieUnavailable):
        code = ErrorCode.PROVIDER_FAILED
        retryable = True
    elif isinstance(exc, BrowserBootstrapUnavailable):
        code = ErrorCode.PROVIDER_FAILED
        retryable = True
    else:
        code = ErrorCode.PROVIDER_FAILED
        retryable = True
    return SkyscannerSessionError(
        failure_type=context.failure_type,
        message_en=exc.message_en,
        error_code=code,
        retryable=retryable,
        http_status_code=context.http_status_code,
        exception_type=context.exception_type,
    )
```

- [ ] **Step 5: Run session and adapter tests**

Run:

```bash
uv run pytest tests/skyscanner/test_session.py tests/skyscanner/test_adapter.py -v
```

Expected: PASS.

- [ ] **Step 6: Write provider tests for bootstrap path and clone cache**

Append to `tests/skyscanner/test_provider.py`:

```python
def test_default_provider_bootstraps_cookie_when_env_cookie_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeSessionManager:
        def config_for_call(self, **kwargs: object) -> tuple[object, str]:
            captured.update(kwargs)
            return object(), "bootstrap"

    class FakeAdapterFromConfig(FakeAdapter):
        def __init__(self, *, config: object, client: object | None = None) -> None:
            captured["config"] = config
            super().__init__([_candidate()])

    monkeypatch.setattr(skyscanner_provider, "monotonic", lambda: 50.0)
    monkeypatch.setattr(
        "cheapy.providers.skyscanner.provider.SkyscannerAdapter",
        FakeAdapterFromConfig,
    )
    provider = SkyscannerProvider(
        env={},
        timeout_seconds=2.5,
        session_manager=FakeSessionManager(),
    )

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.SUCCESS
    assert captured["timeout_seconds"] == 2.5
    assert captured["deadline_monotonic"] == 52.5
    assert captured["force_refresh"] is False


def test_timeout_clone_preserves_skyscanner_session_manager() -> None:
    manager = object()
    provider = SkyscannerProvider(session_manager=manager, timeout_seconds=10)

    clone = provider.with_timeout_seconds(1)

    assert clone._session_manager is manager
```

- [ ] **Step 7: Run provider tests and verify failure**

Run:

```bash
uv run pytest tests/skyscanner/test_provider.py::test_default_provider_bootstraps_cookie_when_env_cookie_missing tests/skyscanner/test_provider.py::test_timeout_clone_preserves_skyscanner_session_manager -v
```

Expected: FAIL because provider does not accept/use `session_manager`.

- [ ] **Step 8: Modify Skyscanner provider to use session manager**

In `cheapy/providers/skyscanner/provider.py`:

Add imports:

```python
from cheapy.providers.skyscanner.session import SkyscannerSessionManager
```

Update `__init__`:

```python
def __init__(
    self,
    *,
    adapter: object | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
    session_manager: object | None = None,
) -> None:
    self._adapter = adapter
    self._timeout_seconds = timeout_seconds
    self._env = dict(os.environ if env is None else env)
    self._session_manager = session_manager or SkyscannerSessionManager()
```

Update `with_timeout_seconds()`:

```python
return SkyscannerProvider(
    adapter=self._adapter,
    timeout_seconds=max(0.001, timeout_seconds),
    env=self._env,
    session_manager=self._session_manager,
)
```

Replace `_adapter_for_call()` with:

```python
def _adapter_for_call(self, *, force_refresh: bool = False) -> tuple[object, str]:
    if self._adapter is not None:
        return self._adapter, "injected"
    config, source = self._session_manager.config_for_call(
        env=self._env,
        timeout_seconds=self._timeout_seconds,
        deadline_monotonic=monotonic() + self._timeout_seconds,
        force_refresh=force_refresh,
    )
    return SkyscannerAdapter.from_config(config), source
```

Replace `_search_sync()` with:

```python
def _search_sync(
    self,
    request: ProviderRequest,
    *,
    search_method_name: str,
) -> list[object]:
    adapter, source = self._adapter_for_call()
    search_method = getattr(adapter, search_method_name)
    try:
        return search_method(request)
    except SkyscannerProviderError as exc:
        if (
            source == "cache"
            and exc.failure_type in {"blocked", "rate_limited", "no_usable_results"}
        ):
            refreshed_adapter, _ = self._adapter_for_call(force_refresh=True)
            refreshed_method = getattr(refreshed_adapter, search_method_name)
            return refreshed_method(request)
        raise
```

Add this classmethod to `SkyscannerAdapter` in `adapter.py`:

```python
@classmethod
def from_config(cls, config: SkyscannerConfig, *, client: HttpClient | None = None) -> "SkyscannerAdapter":
    return cls(config=config, client=client or CurlClient())
```

- [ ] **Step 9: Run Skyscanner focused tests**

Run:

```bash
uv run pytest tests/skyscanner/test_session.py tests/skyscanner/test_adapter.py tests/skyscanner/test_provider.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add cheapy/providers/skyscanner/session.py cheapy/providers/skyscanner/adapter.py cheapy/providers/skyscanner/provider.py tests/skyscanner/test_session.py tests/skyscanner/test_adapter.py tests/skyscanner/test_provider.py
git diff --cached --name-status
git commit -m "feat: bootstrap skyscanner sessions with cloakbrowser" -m "AI-Generated-By: GPT-5 Codex"
```

## Task 3: Traveloka Shared Launcher And Replay Module

**Files:**
- Create: `cheapy/providers/traveloka/replay.py`
- Create: `tests/traveloka/test_replay.py`
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/traveloka/test_adapter.py`

- [ ] **Step 1: Write replay tests**

Create `tests/traveloka/test_replay.py`:

```python
from __future__ import annotations

import pytest

from cheapy.browser_bootstrap.types import (
    BrowserNetworkCapture,
    BrowserBootstrapSession,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
)
from cheapy.providers.traveloka import replay
from cheapy.providers.traveloka.errors import TravelokaProviderError


def _capture(
    *,
    request_url: str = "https://www.traveloka.com/api/v2/flight/search/poll?secret=1",
    response_payload: object | None = None,
) -> BrowserNetworkCapture:
    request = CapturedRequest(
        url=request_url,
        method="POST",
        sequence=1,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "cookie": "secret-cookie",
            "authorization": "Bearer secret",
            "sec-ch-ua": "fingerprint",
        },
        post_data='{"searchId":"secret"}',
    )
    response = CapturedResponse(
        url=request_url,
        status_code=200,
        payload=response_payload or {"data": {"meta": {"searchCompleted": True}, "searchResults": []}},
        sequence=1,
    )
    exchange = CapturedExchange(
        sequence=1,
        captured_monotonic=100.0,
        request=request,
        response=response,
    )
    return BrowserNetworkCapture(
        cookie_header="datadome=secret-cookie",
        user_agent="Mozilla/5.0 secret",
        exchanges=(exchange,),
        created_monotonic=100.0,
    )


class FakeReplayClient:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[replay.TravelokaReplayRequest] = []

    def post(self, request: replay.TravelokaReplayRequest, *, timeout: float) -> replay.TravelokaReplayResponse:
        self.calls.append(request)
        return replay.TravelokaReplayResponse(status_code=self.status_code, payload=self.payload)


def test_select_replay_request_uses_single_exchange_and_redacts_repr() -> None:
    selected = replay.select_replay_request(_capture())

    assert selected.method == "POST"
    assert selected.path_and_query == "/api/v2/flight/search/poll?secret=1"
    assert selected.headers == {
        "accept": "application/json",
        "content-type": "application/json",
    }
    assert selected.cookie_header == "datadome=secret-cookie"
    assert selected.user_agent == "Mozilla/5.0 secret"
    assert "secret" not in repr(selected)
    assert "Mozilla/5.0 secret" not in repr(selected)


def test_select_replay_request_rejects_non_traveloka_url() -> None:
    with pytest.raises(TravelokaProviderError) as exc_info:
        replay.select_replay_request(
            _capture(request_url="https://evil.example/api/v2/flight/search/poll")
        )

    assert exc_info.value.failure_type == "network_capture_unavailable"
    assert "evil.example" not in str(exc_info.value)


def test_replay_success_returns_replay_payload() -> None:
    payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "replay"}]}}
    client = FakeReplayClient(payload)

    result = replay.replay_or_fallback(
        _capture(response_payload={"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "capture"}]}}),
        client=client,
        timeout_seconds=3.0,
    )

    assert result.payload == payload
    assert result.source == "replay"
    assert len(client.calls) == 1


def test_replay_safe_failure_falls_back_to_same_exchange_payload() -> None:
    capture_payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "capture"}]}}
    client = FakeReplayClient({"error": "blocked"}, status_code=403)

    result = replay.replay_or_fallback(
        _capture(response_payload=capture_payload),
        client=client,
        timeout_seconds=3.0,
    )

    assert result.payload == capture_payload
    assert result.source == "browser_capture"


def test_replay_and_capture_failure_returns_safe_error() -> None:
    client = FakeReplayClient({"error": "blocked"}, status_code=403)
    capture = _capture(response_payload={"data": {"calendarPrices": []}})

    with pytest.raises(TravelokaProviderError) as exc_info:
        replay.replay_or_fallback(capture, client=client, timeout_seconds=3.0)

    assert exc_info.value.failure_type == "blocked"
    assert "secret" not in str(exc_info.value)
```

- [ ] **Step 2: Run replay tests and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_replay.py -v
```

Expected: FAIL with missing `cheapy.providers.traveloka.replay`.

- [ ] **Step 3: Implement Traveloka replay module**

Create `cheapy/providers/traveloka/replay.py`:

```python
"""HTTP replay helpers for same-call Traveloka browser harvests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from cheapy.browser_bootstrap.types import BrowserNetworkCapture, CapturedExchange
from cheapy.models import ErrorCode
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import errors as traveloka_errors


ALLOWED_REPLAY_HEADERS = frozenset(
    {"accept", "accept-language", "content-type", "origin", "referer"}
)
SEARCH_PATHS = {
    traveloka_capture.INITIAL_SEARCH_PATH,
    traveloka_capture.POLL_SEARCH_PATH,
}


@dataclass(frozen=True)
class TravelokaReplayRequest:
    path_and_query: str = field(repr=False)
    method: str
    headers: dict[str, str] = field(repr=False)
    body: str = field(repr=False)
    cookie_header: str = field(repr=False)
    user_agent: str = field(repr=False)


@dataclass(frozen=True)
class TravelokaReplayResponse:
    status_code: int
    payload: object = field(repr=False)


@dataclass(frozen=True)
class TravelokaReplayResult:
    payload: dict[str, object] = field(repr=False)
    source: str


class TravelokaReplayClient(Protocol):
    def post(
        self,
        request: TravelokaReplayRequest,
        *,
        timeout: float,
    ) -> TravelokaReplayResponse: ...


def select_replay_request(capture: BrowserNetworkCapture) -> TravelokaReplayRequest:
    exchange = _select_exchange(capture)
    parsed = urlsplit(exchange.request.url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "www.traveloka.com":
        raise traveloka_errors.TravelokaProviderError(
            failure_type="network_capture_unavailable",
            message_en="Traveloka browser capture did not include a replayable request.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
        )
    if parsed.path not in SEARCH_PATHS or exchange.request.method.upper() != "POST":
        raise traveloka_errors.unsupported_response_error()
    body = exchange.request.post_data
    if body is None:
        raise traveloka_errors.TravelokaProviderError(
            failure_type="network_capture_unavailable",
            message_en="Traveloka browser capture did not include a replay body.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
        )
    headers = _safe_replay_headers(exchange.request.headers)
    return TravelokaReplayRequest(
        path_and_query=f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path,
        method="POST",
        headers=headers,
        body=body,
        cookie_header=capture.cookie_header,
        user_agent=capture.user_agent,
    )


def replay_or_fallback(
    capture: BrowserNetworkCapture,
    *,
    client: TravelokaReplayClient,
    timeout_seconds: float,
) -> TravelokaReplayResult:
    exchange = _select_exchange(capture)
    request = select_replay_request(capture)
    replay_error: traveloka_errors.TravelokaProviderError | None = None
    try:
        response = client.post(request, timeout=timeout_seconds)
        replay_payload = _payload_from_replay_response(response)
        return TravelokaReplayResult(payload=replay_payload, source="replay")
    except traveloka_errors.TravelokaProviderError as exc:
        replay_error = exc
    fallback_payload = _supported_payload_from_exchange(exchange)
    if fallback_payload is not None:
        return TravelokaReplayResult(payload=fallback_payload, source="browser_capture")
    if replay_error is not None:
        raise replay_error
    raise traveloka_errors.unsupported_response_error()


def _select_exchange(capture: BrowserNetworkCapture) -> CapturedExchange:
    supported = [
        exchange
        for exchange in capture.exchanges
        if _is_search_exchange(exchange)
    ]
    if not supported:
        raise traveloka_errors.TravelokaProviderError(
            failure_type="network_capture_unavailable",
            message_en="Traveloka browser capture did not include a replayable request.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
        )
    return sorted(
        supported,
        key=lambda exchange: (
            _path_rank(urlsplit(exchange.request.url).path),
            exchange.sequence,
        ),
    )[-1]


def _is_search_exchange(exchange: CapturedExchange) -> bool:
    parsed = urlsplit(exchange.request.url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "www.traveloka.com"
        and parsed.path in SEARCH_PATHS
        and exchange.request.method.upper() == "POST"
    )


def _path_rank(path: str) -> int:
    return 2 if path == traveloka_capture.POLL_SEARCH_PATH else 1


def _safe_replay_headers(headers: object) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    safe: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key).strip().lower()
        if name not in ALLOWED_REPLAY_HEADERS:
            continue
        text = str(value).strip()
        if not text or "\r" in text or "\n" in text:
            continue
        if name == "referer" and not text.startswith("https://www.traveloka.com/"):
            continue
        safe[name] = text
    return safe


def _payload_from_replay_response(response: TravelokaReplayResponse) -> dict[str, object]:
    status = response.status_code
    if status in {401, 403}:
        raise traveloka_errors.blocked_error(status)
    if status == 429:
        raise traveloka_errors.rate_limited_error(status)
    if status >= 500:
        raise traveloka_errors.transport_error(status)
    payload = response.payload
    if not isinstance(payload, dict):
        raise traveloka_errors.invalid_json_error("InvalidReplayPayload")
    if not _is_supported_payload(payload):
        raise traveloka_errors.unsupported_response_error()
    return payload


def _supported_payload_from_exchange(exchange: CapturedExchange) -> dict[str, object] | None:
    if exchange.response is None:
        return None
    payload = exchange.response.payload
    if isinstance(payload, dict) and _is_supported_payload(payload):
        return payload
    return None


def _is_supported_payload(payload: dict[str, object]) -> bool:
    data = payload.get("data")
    return isinstance(data, dict) and isinstance(data.get("searchResults"), list)
```

- [ ] **Step 4: Run replay tests**

Run:

```bash
uv run pytest tests/traveloka/test_replay.py -v
```

Expected: PASS.

- [ ] **Step 5: Move Traveloka launcher to shared bootstrap**

Modify `cheapy/providers/traveloka/adapter.py`.

Add import:

```python
from cheapy.browser_bootstrap import launch_browser as default_launch_browser
```

Change default launcher assignment in `TravelokaAdapter.__init__`:

```python
self._launch_browser = (
    launch_browser if launch_browser is not None else default_launch_browser
)
```

Delete `_default_launch_browser()` and remove unused `redirect_stdout`, `redirect_stderr`, and `StringIO` imports.

Update `tests/traveloka/test_adapter.py` default launcher test:

```python
def test_adapter_uses_shared_default_launch_browser() -> None:
    adapter = TravelokaAdapter()

    assert adapter._launch_browser is traveloka_adapter.default_launch_browser
```

The existing console-noise suppression test should move to `tests/browser_bootstrap/test_cloak.py`:

```python
def test_launch_browser_suppresses_dependency_console_noise(monkeypatch, capsys) -> None:
    import sys
    import types

    fake_module = types.ModuleType("cloakbrowser")

    def fake_launch(**kwargs: object) -> dict[str, object]:
        print("Update available: cloakbrowser 0.3.28 -> 0.3.31")
        print("debug browser setup", file=sys.stderr)
        return {"kwargs": kwargs}

    fake_module.launch = fake_launch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cloakbrowser", fake_module)

    result = cloak.launch_browser(headless=True, timeout=123)

    assert result == {"kwargs": {"headless": True, "timeout": 123}}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
```

- [ ] **Step 6: Run Traveloka launcher tests**

Run:

```bash
uv run pytest tests/browser_bootstrap/test_cloak.py tests/traveloka/test_adapter.py tests/traveloka/test_session.py -v
```

Expected: PASS.

- [ ] **Step 7: Verify only shared bootstrap imports cloakbrowser**

Run:

```bash
rg -n "from cloakbrowser|import cloakbrowser" cheapy
```

Expected: only `cheapy/browser_bootstrap/cloak.py`.

- [ ] **Step 8: Commit**

Run:

```bash
git add cheapy/providers/traveloka/replay.py cheapy/providers/traveloka/adapter.py tests/traveloka/test_replay.py tests/traveloka/test_adapter.py tests/browser_bootstrap/test_cloak.py
git diff --cached --name-status
git commit -m "feat: add traveloka replay helpers" -m "AI-Generated-By: GPT-5 Codex"
```

## Task 4: Traveloka Replay-From-Harvest Adapter Path

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `cheapy/providers/traveloka/workflow.py`
- Modify: `tests/traveloka/test_adapter.py`
- Modify: `tests/traveloka/test_provider.py`
- Modify: `tests/traveloka/test_capture.py`

- [ ] **Step 1: Add adapter tests for replay success and fallback**

Append to `tests/traveloka/test_adapter.py`:

```python
from cheapy.browser_bootstrap.types import BrowserNetworkCapture, CapturedExchange, CapturedRequest, CapturedResponse
from cheapy.providers.traveloka import replay as traveloka_replay


def _network_capture(payload: dict[str, object]) -> BrowserNetworkCapture:
    url = "https://www.traveloka.com/api/v2/flight/search/poll"
    request = CapturedRequest(
        url=url,
        method="POST",
        sequence=1,
        headers={"content-type": "application/json"},
        post_data='{"searchId":"secret"}',
    )
    response = CapturedResponse(url=url, status_code=200, payload=payload, sequence=1)
    return BrowserNetworkCapture(
        cookie_header="datadome=secret-cookie",
        user_agent="Mozilla/5.0 secret",
        exchanges=(
            CapturedExchange(
                sequence=1,
                captured_monotonic=100.0,
                request=request,
                response=response,
            ),
        ),
        created_monotonic=100.0,
    )


class ReplayClient:
    def __init__(self, response: traveloka_replay.TravelokaReplayResponse) -> None:
        self.response = response
        self.calls: list[traveloka_replay.TravelokaReplayRequest] = []

    def post(
        self,
        request: traveloka_replay.TravelokaReplayRequest,
        *,
        timeout: float,
    ) -> traveloka_replay.TravelokaReplayResponse:
        self.calls.append(request)
        return self.response


def test_adapter_prefers_replay_payload_from_harvest() -> None:
    capture_payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "capture"}]}}
    replay_payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "replay"}]}}
    replay_client = ReplayClient(traveloka_replay.TravelokaReplayResponse(200, replay_payload))
    adapter = TravelokaAdapter(
        capture_network=lambda **kwargs: _network_capture(capture_payload),
        replay_client=replay_client,
        timeout_seconds=5.0,
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == replay_payload
    assert result.source_path == "/api/v2/flight/search/poll"
    assert len(replay_client.calls) == 1


def test_adapter_falls_back_to_browser_capture_when_replay_is_blocked() -> None:
    capture_payload = {"data": {"meta": {"searchCompleted": True}, "searchResults": [{"id": "capture"}]}}
    replay_client = ReplayClient(traveloka_replay.TravelokaReplayResponse(403, {"error": "blocked"}))
    adapter = TravelokaAdapter(
        capture_network=lambda **kwargs: _network_capture(capture_payload),
        replay_client=replay_client,
        timeout_seconds=5.0,
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == capture_payload
```

- [ ] **Step 2: Run adapter replay tests and verify failure**

Run:

```bash
uv run pytest tests/traveloka/test_adapter.py::test_adapter_prefers_replay_payload_from_harvest tests/traveloka/test_adapter.py::test_adapter_falls_back_to_browser_capture_when_replay_is_blocked -v
```

Expected: FAIL because `TravelokaAdapter` does not accept `capture_network` or `replay_client`.

- [ ] **Step 3: Implement adapter replay path**

Modify `cheapy/providers/traveloka/adapter.py`:

Add imports:

```python
from time import monotonic
from urllib.parse import urlparse

from cheapy.browser_bootstrap import capture_first_party_requests
from cheapy.providers.traveloka import capture as traveloka_capture
from cheapy.providers.traveloka import replay as traveloka_replay
```

Add type aliases:

```python
NetworkCapture = Callable[..., object]
```

Update `TravelokaAdapter.__init__` signature:

```python
def __init__(
    self,
    *,
    base_url: str = traveloka_urls.DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.25,
    launch_browser: BrowserLauncher | None = None,
    capture_network: NetworkCapture | None = None,
    replay_client: object | None = None,
) -> None:
```

Set:

```python
self._capture_network = capture_network
self._replay_client = replay_client
```

At the start of `_search()` before browser workflow fallback:

```python
if self._capture_network is not None and self._replay_client is not None:
    return self._search_with_replay(request)
```

Add:

```python
def _search_with_replay(self, request: ProviderExactOneWayRequest) -> TravelokaCaptureResult:
    deadline = monotonic() + self._timeout_seconds
    capture_network = self._capture_network or capture_first_party_requests
    network_capture = capture_network(
        page_url=traveloka_urls.build_full_search_url(request, base_url=self._base_url),
        deadline_monotonic=deadline,
        request_predicate=_is_traveloka_search_request,
        response_predicate=_is_traveloka_search_response,
        launch_browser=self._launch_browser,
    )
    replay_result = traveloka_replay.replay_or_fallback(
        network_capture,
        client=self._replay_client,
        timeout_seconds=self._timeout_seconds,
    )
    return TravelokaCaptureResult(
        payload=replay_result.payload,
        source_path=traveloka_capture.POLL_SEARCH_PATH,
        search_completed=traveloka_capture.search_completed(replay_result.payload),
        timed_out=False,
    )
```

Add this public helper to `cheapy/providers/traveloka/capture.py`:

```python
def search_completed(payload: dict[str, object]) -> bool:
    return _search_completed(payload)
```

Add module helpers:

```python
def _is_traveloka_search_request(method: str, url: str) -> bool:
    return (
        method.upper() == "POST"
        and traveloka_capture.is_traveloka_first_party_url(url)
        and urlparse(url).path in traveloka_capture.SUPPORTED_FARE_PATHS
    )


def _is_traveloka_search_response(url: str, status: int) -> bool:
    return (
        traveloka_capture.is_traveloka_first_party_url(url)
        and urlparse(url).path in traveloka_capture.SUPPORTED_FARE_PATHS
        and status < 500
    )
```

- [ ] **Step 4: Run adapter replay tests**

Run:

```bash
uv run pytest tests/traveloka/test_adapter.py::test_adapter_prefers_replay_payload_from_harvest tests/traveloka/test_adapter.py::test_adapter_falls_back_to_browser_capture_when_replay_is_blocked -v
```

Expected: PASS.

- [ ] **Step 5: Keep round-trip browser workflow fallback**

Add a test to `tests/traveloka/test_adapter.py`:

```python
def test_round_trip_without_replay_dependencies_keeps_browser_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_search_selected_round_trip(request: object, **kwargs: object) -> TravelokaCaptureResult:
        seen["request"] = request
        seen["kwargs"] = kwargs
        return _capture()

    monkeypatch.setattr(
        traveloka_workflow,
        "search_selected_round_trip",
        fake_search_selected_round_trip,
    )

    adapter = TravelokaAdapter(timeout_seconds=7, launch_browser=lambda **kwargs: object())

    result = adapter.search_exact_round_trip(_round_trip_request())

    assert result.search_completed is True
    assert seen["request"] == _round_trip_request()
```

Run:

```bash
uv run pytest tests/traveloka/test_adapter.py::test_round_trip_without_replay_dependencies_keeps_browser_workflow -v
```

Expected: PASS.

- [ ] **Step 6: Run Traveloka focused tests**

Run:

```bash
uv run pytest tests/traveloka/test_replay.py tests/traveloka/test_adapter.py tests/traveloka/test_capture.py tests/traveloka/test_session.py tests/traveloka/test_provider.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py cheapy/providers/traveloka/workflow.py tests/traveloka/test_adapter.py tests/traveloka/test_provider.py tests/traveloka/test_capture.py
git diff --cached --name-status
git commit -m "feat: replay traveloka harvested search requests" -m "AI-Generated-By: GPT-5 Codex"
```

## Task 5: No-Leak, Protocol, And Full Regression

**Files:**
- Modify: `tests/test_markdown_report.py`

- [ ] **Step 1: Add Markdown no-leak regression for bootstrap material**

Modify `tests/test_markdown_report.py` and extend `INTERNAL_OUTPUT_DENYLIST`:

```python
INTERNAL_OUTPUT_DENYLIST = (
    "/transport_deeplink/",
    "transport_deeplink",
    "sessionId",
    "session_id",
    "cookie",
    "headers",
    "request_body",
    "post_data",
    "raw_payload",
    "challenge",
    "datadome",
    "aws-waf-token",
    "tvl=",
    "tvo=",
    "tvs=",
    "mozilla/5.0 secret",
)
```

Append this test:

```python
def test_browser_bootstrap_material_is_redacted_with_safe_reason() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en=(
            "Traveloka replay failed with datadome secret-cookie, "
            "post_data, aws-waf-token, and Mozilla/5.0 secret."
        ),
        details={
            "provider": "traveloka",
            "capability": "exact_one_way",
            "failure_type": "network_capture_unavailable",
            "headers": {"cookie": "datadome=secret-cookie"},
            "post_data": '{"searchId":"secret"}',
        },
        retryable=True,
    )
    response = _response(
        errors=[error],
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.FAILED,
                succeeded_call_count=0,
                failed_call_count=1,
                errors=[error],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "[redacted]" in report
    for unsafe_text in INTERNAL_OUTPUT_DENYLIST:
        assert unsafe_text not in report
```

Run:

```bash
uv run pytest tests/test_markdown_report.py::test_browser_bootstrap_material_is_redacted_with_safe_reason -v
```

Expected: PASS.

- [ ] **Step 2: Verify no direct provider-level Cloakbrowser imports**

Run:

```bash
rg -n "from cloakbrowser|import cloakbrowser" cheapy
```

Expected: only `cheapy/browser_bootstrap/cloak.py`.

- [ ] **Step 3: Verify no Browserless runtime regression**

Run:

```bash
rg -n "Browserless|browserless|BROWSERLESS|production-sfo" cheapy
```

Expected: no provider or bootstrap runtime Browserless dependency path. The only acceptable match is a sanitizer/redaction token in `cheapy/markdown_report.py`.

- [ ] **Step 4: Run focused suites**

Run:

```bash
uv run pytest tests/browser_bootstrap -v
uv run pytest tests/skyscanner -v
uv run pytest tests/traveloka -v
uv run pytest tests/test_search.py tests/test_markdown_report.py tests/test_cli.py tests/test_mcp.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 6: Commit final regression test**

Run:

```bash
git add tests/test_markdown_report.py
git diff --cached --name-status
git commit -m "test: cover browser bootstrap leak safety" -m "AI-Generated-By: GPT-5 Codex"
```

## Final Review Checklist

- [ ] `uv run pytest -v` passes.
- [ ] `rg -n "from cloakbrowser|import cloakbrowser" cheapy` returns only `cheapy/browser_bootstrap/cloak.py`.
- [ ] `rg -n "Browserless|browserless|BROWSERLESS|production-sfo" cheapy` shows no runtime Browserless dependency path.
- [ ] Skyscanner without `CHEAPY_SKYSCANNER_COOKIE` uses bootstrap path in fake tests.
- [ ] Skyscanner with env cookie bypasses bootstrap.
- [ ] Traveloka replay success uses replay payload.
- [ ] Traveloka replay safe failure falls back only to same-exchange browser-captured payload.
- [ ] Contract V1 models are unchanged.
- [ ] No cookies, headers, request bodies, user agents, challenge URLs, raw payloads, internal provider URLs, or browser session data appear in stdout, stderr, reports, Contract details, CLI/MCP output, SQLite history, reprs, or command argv.
