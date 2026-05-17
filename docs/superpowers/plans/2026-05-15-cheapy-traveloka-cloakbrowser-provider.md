# Traveloka CloakBrowser Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Traveloka's HTTP-only adapter with a default-enabled CloakBrowser browser provider that captures Traveloka fare API responses and returns normalized Contract V1 offers.

**Architecture:** Keep the existing `TravelokaProvider` public interface and provider manifest. Replace the internals of `cheapy/providers/traveloka/adapter.py` with a CloakBrowser-backed browser adapter that enforces the 45-second browser deadline and returns a typed `TravelokaCaptureResult`; update `provider.py` to combine captured payloads with timeout state; update `normalizer.py` to support Traveloka `data.searchResults[]`.

**Tech Stack:** Python 3.12, uv, pytest, Pydantic Contract V1, CloakBrowser Python package with Playwright-compatible sync browser objects.

---

## File Structure

- Modify `pyproject.toml`: add `cloakbrowser` as a normal runtime dependency.
- Modify `uv.lock`: update through `uv sync --extra dev` after dependency change.
- Modify `cheapy/providers/traveloka/adapter.py`: replace HTTP adapter with URL builder, `TravelokaCaptureResult`, CloakBrowser launch, response capture, safe error classification, browser cleanup.
- Modify `cheapy/providers/traveloka/provider.py`: pass the 45-second timeout into the default adapter, consume `TravelokaCaptureResult`, normalize `capture.payload`, attach safe timeout error when `capture.timed_out` is true.
- Modify `cheapy/providers/traveloka/normalizer.py`: support `data.searchResults[]` and canonicalize Traveloka fare result objects into the existing item-normalization path.
- Modify `tests/test_traveloka_adapter.py`: replace HTTP-only tests with offline fake-browser tests for URL building, capture result behavior, endpoint filtering, timeout, and browser/error classification.
- Modify `tests/test_traveloka_provider.py`: update fake adapter to return `TravelokaCaptureResult`; test completed success, partial timeout with offers, timeout before payload, and adapter error mapping.
- Modify `tests/test_traveloka_normalizer.py`: add reduced `data.searchResults[]` one-way, round-trip, price-minor-unit, and empty-result coverage.
- Create `tests/test_live_traveloka.py`: opt-in live smoke behind `CHEAPY_RUN_LIVE_TESTS=1`, skipped by default.

## Task 1: Add CloakBrowser Dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add the runtime dependency**

Run:

```bash
uv add 'cloakbrowser>=0.3.26'
```

Expected: `pyproject.toml` gains a normal project dependency and `uv.lock` updates.

The dependency list should contain:

```toml
dependencies = [
    "cloakbrowser>=0.3.26",
    "flights>=0.8.4",
    "mcp>=1.27.1,<1.28",
    "pydantic>=2.10",
    "tomlkit>=0.15.0",
    "typer>=0.15",
]
```

- [ ] **Step 2: Verify dependency resolution**

Run:

```bash
uv sync --extra dev
```

Expected: command exits 0.

- [ ] **Step 3: Commit dependency change**

Run:

```bash
git add pyproject.toml uv.lock
git commit -m "build: add cloakbrowser dependency"
```

## Task 2: Define Traveloka Capture Contract And URL Builder

**Files:**
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `cheapy/providers/traveloka/adapter.py`

- [ ] **Step 1: Replace URL-builder tests with full-search route tests**

In `tests/test_traveloka_adapter.py`, keep the existing request helpers and replace `test_build_search_url_maps_one_way_request_to_safe_query` and `test_build_search_url_maps_round_trip_request_to_safe_query` with:

```python
def test_build_full_search_url_maps_one_way_request_to_traveloka_route() -> None:
    url = traveloka_adapter.build_full_search_url(
        _one_way_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.traveloka.com"
    assert parsed.path == "/en-en/flight/fulltwosearch"
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026"]
    assert params["ps"] == ["1.0.0"]
    assert params["sc"] == ["ECONOMY"]
    assert params["funnelSource"] == ["SEO-Homepage-SearchForm"]


def test_build_full_search_url_maps_round_trip_request_to_traveloka_route() -> None:
    url = traveloka_adapter.build_full_search_url(
        _round_trip_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    params = parse_qs(urlparse(url).query)
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026.17-7-2026"]
    assert params["ps"] == ["1.0.0"]
    assert params["sc"] == ["ECONOMY"]
```

- [ ] **Step 2: Add a capture-result unit test**

Add:

```python
def test_capture_result_carries_completion_and_timeout_state() -> None:
    result = traveloka_adapter.TravelokaCaptureResult(
        payload={"data": {"searchResults": []}},
        source_path="/api/v2/flight/search/initial",
        search_completed=False,
        timed_out=True,
    )

    assert result.payload == {"data": {"searchResults": []}}
    assert result.source_path == "/api/v2/flight/search/initial"
    assert result.search_completed is False
    assert result.timed_out is True
```

- [ ] **Step 3: Run the focused tests and confirm failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_build_full_search_url_maps_one_way_request_to_traveloka_route tests/test_traveloka_adapter.py::test_build_full_search_url_maps_round_trip_request_to_traveloka_route tests/test_traveloka_adapter.py::test_capture_result_carries_completion_and_timeout_state -v
```

Expected: FAIL because `build_full_search_url` and `TravelokaCaptureResult` do not exist yet.

- [ ] **Step 4: Implement the capture contract and URL builder**

In `cheapy/providers/traveloka/adapter.py`, add these imports and definitions:

```python
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable
from urllib.parse import urlencode, urlparse


DEFAULT_BASE_URL = "https://www.traveloka.com/en-en/flight/fulltwosearch"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_CURRENCY = "USD"
DEFAULT_LOCALE = "en-en"
INITIAL_SEARCH_PATH = "/api/v2/flight/search/initial"
POLL_SEARCH_PATH = "/api/v2/flight/search/poll"
SUPPORTED_FARE_PATHS = {INITIAL_SEARCH_PATH, POLL_SEARCH_PATH}


@dataclass(frozen=True)
class TravelokaCaptureResult:
    payload: dict[str, object]
    source_path: str
    search_completed: bool
    timed_out: bool = False
```

Replace `build_search_url` with:

```python
def build_full_search_url(
    request: ProviderRequest,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    date_part = _traveloka_date(request.departure_date)
    if isinstance(request, ProviderExactRoundTripRequest):
        date_part = f"{date_part}.{_traveloka_date(request.return_date)}"
    params = {
        "ap": f"{request.origin}.{request.destination}",
        "dt": date_part,
        "ps": _passenger_spec(request),
        "sc": "ECONOMY",
        "funnelSource": "SEO-Homepage-SearchForm",
    }
    return f"{base_url}?{urlencode(params)}"


def _traveloka_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.day}-{parsed.month}-{parsed.year}"


def _passenger_spec(request: ProviderRequest) -> str:
    passengers = request.passengers
    return (
        f"{passengers.adults}."
        f"{passengers.children}."
        f"{passengers.infants_on_lap + passengers.infants_in_seat}"
    )
```

- [ ] **Step 5: Run the focused tests and confirm pass**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_build_full_search_url_maps_one_way_request_to_traveloka_route tests/test_traveloka_adapter.py::test_build_full_search_url_maps_round_trip_request_to_traveloka_route tests/test_traveloka_adapter.py::test_capture_result_carries_completion_and_timeout_state -v
```

Expected: PASS.

- [ ] **Step 6: Commit capture contract and URL builder**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "feat: add traveloka browser capture contract"
```

## Task 3: Implement Offline-Testable Browser Capture Adapter

**Files:**
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `cheapy/providers/traveloka/adapter.py`

- [ ] **Step 1: Add fake browser test helpers**

In `tests/test_traveloka_adapter.py`, add:

```python
class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int = 200,
        payload: dict[str, object] | Exception,
    ) -> None:
        self.url = url
        self.status = status
        self._payload = payload

    def json(self) -> dict[str, object]:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakePage:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.handlers: dict[str, object] = {}
        self.goto_urls: list[str] = []
        self.wait_calls = 0

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_urls.append(url)
        handler = self.handlers["response"]
        for response in self.responses:
            handler(response)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls += 1

    def content(self) -> str:
        return "<html><body>flight search</body></html>"


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    def new_context(self, **kwargs: object) -> FakeContext:
        return self.context

    def close(self) -> None:
        self.closed = True
```

- [ ] **Step 2: Add browser capture success and endpoint-filter tests**

Add:

```python
def test_adapter_captures_completed_initial_fare_payload() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [{"id": "tv-1"}],
        }
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/log",
                payload={"ignored": True},
            ),
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            ),
        ]
    )
    context = FakeContext(page)
    browser = FakeBrowser(context)
    adapter = TravelokaAdapter(launch_browser=lambda **kwargs: browser)

    result = adapter.search_exact_one_way(_one_way_request())

    assert result == traveloka_adapter.TravelokaCaptureResult(
        payload=payload,
        source_path="/api/v2/flight/search/initial",
        search_completed=True,
        timed_out=False,
    )
    assert page.goto_urls[0].startswith(
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
    )
    assert context.closed is True
    assert browser.closed is True
```

- [ ] **Step 3: Add partial-timeout capture test**

Add:

```python
def test_adapter_returns_partial_payload_when_timeout_happens_after_offers() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": False},
            "searchResults": [{"id": "tv-1"}],
        }
    }
    page = FakePage(
        [
            FakeResponse(
                url="https://www.traveloka.com/api/v2/flight/search/initial",
                payload=payload,
            )
        ]
    )
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0,
    )

    result = adapter.search_exact_one_way(_one_way_request())

    assert result.payload == payload
    assert result.search_completed is False
    assert result.timed_out is True
```

- [ ] **Step 4: Add timeout-before-payload and browser-unavailable tests**

Add:

```python
def test_adapter_raises_timeout_when_no_fare_payload_arrives() -> None:
    page = FakePage([])
    adapter = TravelokaAdapter(
        launch_browser=lambda **kwargs: FakeBrowser(FakeContext(page)),
        timeout_seconds=0.01,
        poll_interval_seconds=0,
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True


def test_adapter_maps_browser_launch_failure_to_browser_unavailable() -> None:
    def fail_launch(**kwargs: object) -> object:
        raise RuntimeError("raw launch secret")

    adapter = TravelokaAdapter(launch_browser=fail_launch)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "browser_unavailable"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is True
    assert "raw launch secret" not in str(exc_info.value)
```

- [ ] **Step 5: Run focused tests and confirm failure**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: FAIL because the browser adapter is not implemented yet.

- [ ] **Step 6: Implement browser adapter constructor and search methods**

In `cheapy/providers/traveloka/adapter.py`, make `TravelokaAdapter` use injected launch and browser capture:

```python
BrowserLauncher = Callable[..., object]


class TravelokaAdapter:
    configured_currency = DEFAULT_CURRENCY

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = 0.25,
        launch_browser: BrowserLauncher | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must not be negative")
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._launch_browser = (
            launch_browser if launch_browser is not None else _default_launch_browser
        )

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> TravelokaCaptureResult:
        return self._search(request)

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult:
        return self._search(request)
```

- [ ] **Step 7: Implement capture state and browser cleanup**

Add:

```python
@dataclass
class _CaptureState:
    best: TravelokaCaptureResult | None = None

    @property
    def completed(self) -> bool:
        return self.best is not None and self.best.search_completed

    def handle_response(self, response: object) -> None:
        url = str(getattr(response, "url", ""))
        path = urlparse(url).path
        if path not in SUPPORTED_FARE_PATHS:
            return
        status = int(getattr(response, "status", 0))
        if status in (401, 403):
            raise _blocked_error(status, path)
        if status == 429:
            raise _rate_limited_error(path)
        if status >= 500:
            raise _transport_error("HTTPError", status, path)
        try:
            payload = response.json()
        except Exception as exc:
            raise _invalid_json_error(type(exc).__name__, path) from None
        if not _is_supported_fare_payload(payload):
            raise _unsupported_response_error(path)
        self.best = TravelokaCaptureResult(
            payload=payload,
            source_path=path,
            search_completed=_search_completed(payload),
        )
```

Implement `_search`:

```python
def _search(self, request: ProviderRequest) -> TravelokaCaptureResult:
    state = _CaptureState()
    browser = None
    context = None
    page = None
    try:
        browser = self._launch_browser(headless=True)
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        page.on("response", state.handle_response)
        page.goto(
            build_full_search_url(request, base_url=self._base_url),
            wait_until="domcontentloaded",
            timeout=round(self._timeout_seconds * 1000),
        )
        deadline = perf_counter() + self._timeout_seconds
        while perf_counter() < deadline:
            if state.completed:
                return state.best
            if state.best is not None and state.best.payload:
                page.wait_for_timeout(round(self._poll_interval_seconds * 1000))
            else:
                page.wait_for_timeout(round(self._poll_interval_seconds * 1000))
        if state.best is not None:
            return TravelokaCaptureResult(
                payload=state.best.payload,
                source_path=state.best.source_path,
                search_completed=state.best.search_completed,
                timed_out=True,
            )
        _raise_blocked_if_terminal_page(page)
        raise _timeout_error("TimeoutError")
    except TravelokaProviderError:
        raise
    except Exception as exc:
        if browser is None:
            raise _browser_unavailable_error(type(exc).__name__) from None
        raise _navigation_failed_error(type(exc).__name__) from None
    finally:
        _close_quietly(context)
        _close_quietly(browser)
```

- [ ] **Step 8: Implement safe helper functions**

Add:

```python
def _default_launch_browser(**kwargs: object) -> object:
    try:
        from cloakbrowser import launch
    except Exception as exc:
        raise _browser_unavailable_error(type(exc).__name__) from None
    try:
        return launch(**kwargs)
    except Exception as exc:
        raise _browser_unavailable_error(type(exc).__name__) from None


def _is_supported_fare_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    return isinstance(data, dict) and isinstance(data.get("searchResults"), list)


def _search_completed(payload: dict[str, object]) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    meta = data.get("meta")
    return isinstance(meta, dict) and meta.get("searchCompleted") is True


def _close_quietly(resource: object | None) -> None:
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return
```

Add structured error helpers:

```python
def _timeout_error(exception_type: str) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="timeout",
        message_en="Traveloka browser search timed out.",
        error_code=ErrorCode.PROVIDER_TIMEOUT,
        retryable=True,
        exception_type=exception_type,
    )


def _browser_unavailable_error(exception_type: str) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="browser_unavailable",
        message_en="Traveloka browser runtime is unavailable.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def _navigation_failed_error(exception_type: str) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="navigation_failed",
        message_en="Traveloka browser navigation failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        exception_type=exception_type,
    )


def _blocked_error(status_code: int | None = None, path: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="blocked",
        message_en="Traveloka blocked the browser search.",
        error_code=ErrorCode.PROVIDER_BLOCKED,
        retryable=False,
        http_status_code=status_code,
    )


def _rate_limited_error(path: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="rate_limited",
        message_en="Traveloka rate limited the browser search.",
        error_code=ErrorCode.PROVIDER_RATE_LIMITED,
        retryable=True,
        http_status_code=429,
    )


def _transport_error(
    exception_type: str,
    status_code: int | None = None,
    path: str | None = None,
) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="transport_error",
        message_en="Traveloka fare API transport failed.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=True,
        http_status_code=status_code,
        exception_type=exception_type,
    )


def _invalid_json_error(exception_type: str, path: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="invalid_json",
        message_en="Traveloka fare API returned invalid JSON.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
        exception_type=exception_type,
    )


def _unsupported_response_error(path: str | None = None) -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="unsupported_response",
        message_en="Traveloka returned an unsupported fare response.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def _raise_blocked_if_terminal_page(page: object | None) -> None:
    if page is None:
        return
    content = getattr(page, "content", None)
    if not callable(content):
        return
    try:
        body = str(content()).lower()
    except Exception:
        return
    markers = (
        "captcha-delivery",
        "please enable js",
        "access challenge",
        "robot check",
        "verify you are not a bot",
    )
    if any(marker in body for marker in markers):
        raise _blocked_error()
```

- [ ] **Step 9: Run focused tests and confirm pass**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit browser adapter**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "feat: capture traveloka fares with browser adapter"
```

## Task 4: Update Provider For Capture Results And Partial Timeout

**Files:**
- Modify: `tests/test_traveloka_provider.py`
- Modify: `cheapy/providers/traveloka/provider.py`

- [ ] **Step 1: Update fake adapter to return `TravelokaCaptureResult`**

In `tests/test_traveloka_provider.py`, import `TravelokaCaptureResult` and update `FakeAdapter`:

```python
from cheapy.providers.traveloka.adapter import (
    TravelokaCaptureResult,
    TravelokaProviderError,
)


class FakeAdapter:
    configured_currency = "USD"

    def __init__(self, result: TravelokaCaptureResult | Exception) -> None:
        self.result = result
        self.one_way_calls = 0
        self.round_trip_calls = 0

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> TravelokaCaptureResult:
        self.one_way_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> TravelokaCaptureResult:
        self.round_trip_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
```

Add a helper:

```python
def _capture(
    payload: dict[str, Any],
    *,
    search_completed: bool = True,
    timed_out: bool = False,
) -> TravelokaCaptureResult:
    return TravelokaCaptureResult(
        payload=payload,
        source_path="/api/v2/flight/search/initial",
        search_completed=search_completed,
        timed_out=timed_out,
    )
```

- [ ] **Step 2: Update existing success and partial tests to use `_capture`**

Change provider construction from:

```python
adapter = FakeAdapter(_payload())
```

to:

```python
adapter = FakeAdapter(_capture(_payload()))
```

Apply the same pattern to existing provider tests that pass payload dictionaries.

- [ ] **Step 3: Add partial-timeout provider test**

Add:

```python
def test_traveloka_provider_returns_partial_offers_with_timeout_error() -> None:
    adapter = FakeAdapter(
        _capture(_payload(), search_completed=False, timed_out=True)
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert len(result.errors) == 1
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "timeout",
        "source_path": "/api/v2/flight/search/initial",
    }
    assert result.retryable is True
```

- [ ] **Step 4: Run provider tests and confirm failure**

Run:

```bash
uv run pytest tests/test_traveloka_provider.py -v
```

Expected: FAIL because `provider.py` still treats adapter return values as raw payload dictionaries.

- [ ] **Step 5: Update provider timeout and capture handling**

In `cheapy/providers/traveloka/provider.py`, change:

```python
DEFAULT_TIMEOUT_SECONDS = 20.0
```

to:

```python
DEFAULT_TIMEOUT_SECONDS = 45.0
```

Update the default adapter construction so the adapter owns the browser deadline:

```python
self._adapter = (
    adapter
    if adapter is not None
    else TravelokaAdapter(timeout_seconds=timeout_seconds)
)
self._timeout_seconds = timeout_seconds
```

Change the adapter call handling inside `_search`:

```python
capture = await asyncio.to_thread(search_method, request)
offers, errors = normalize_payload(capture.payload, request)
if capture.timed_out and offers:
    errors.append(
        _provider_error(
            code=ErrorCode.PROVIDER_TIMEOUT,
            message_en="Traveloka provider timed out after returning partial offers.",
            failure_type="timeout",
            retryable=True,
            capability=capability,
            source_path=capture.source_path,
        )
    )
```

Do not wrap the browser adapter call with an equal-duration
`asyncio.wait_for`; the adapter must get the chance to close browser resources
and return a partial-offer `TravelokaCaptureResult` when timeout happens after
usable offers were captured.

Update `_provider_error` signature to include `source_path`:

```python
def _provider_error(
    *,
    code: ErrorCode,
    message_en: str,
    failure_type: str,
    retryable: bool,
    capability: str,
    http_status_code: int | None = None,
    exception_type: str | None = None,
    source_path: str | None = None,
) -> ErrorV1:
    details: dict[str, object] = {
        "provider": PROVIDER_NAME,
        "capability": capability,
        "failure_type": failure_type,
    }
    if http_status_code is not None:
        details["http_status_code"] = http_status_code
    if exception_type is not None:
        details["exception_type"] = exception_type
    if source_path is not None:
        details["source_path"] = source_path
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )
```

- [ ] **Step 6: Run provider tests and confirm pass**

Run:

```bash
uv run pytest tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit provider capture handling**

Run:

```bash
git add cheapy/providers/traveloka/provider.py tests/test_traveloka_provider.py
git commit -m "feat: return partial traveloka offers on timeout"
```

## Task 5: Normalize Traveloka `data.searchResults[]`

**Files:**
- Modify: `tests/test_traveloka_normalizer.py`
- Modify: `cheapy/providers/traveloka/normalizer.py`

- [ ] **Step 1: Add reduced Traveloka search-result fixture helpers**

In `tests/test_traveloka_normalizer.py`, add:

```python
def _traveloka_search_result(
    *,
    result_id: str = "tv-search-1",
    price_amount: str = "29890",
    decimal_points: str = "2",
    origin: str = "SGN",
    destination: str = "BKK",
    departure_day: str = "10",
    arrival_day: str = "10",
    flight_number: str = "VJ-801",
) -> dict[str, object]:
    return {
        "id": result_id,
        "flightMetadata": {
            "totalNumStop": "0",
            "tripDuration": "95",
            "airlineIds": ["VJ"],
            "totalCombinedPrice": {
                "currencyValue": {
                    "currency": "USD",
                    "amount": price_amount,
                },
                "numOfDecimalPoint": decimal_points,
            },
        },
        "fare": {
            "display": {
                "currencyValue": {
                    "currency": "USD",
                    "amount": price_amount,
                },
                "numOfDecimalPoint": decimal_points,
            }
        },
        "connectingFlightRoutes": [
            {
                "departureAirport": origin,
                "arrivalAirport": destination,
                "totalNumStop": "0",
                "durationInMinutes": "95",
                "segments": [
                    {
                        "departureAirport": origin,
                        "arrivalAirport": destination,
                        "flightNumber": flight_number,
                        "airlineCode": "VJ",
                        "durationMinutes": "95",
                        "departureDate": {
                            "year": "2026",
                            "month": "7",
                            "day": departure_day,
                        },
                        "departureTime": {"hour": "9", "minute": "0"},
                        "arrivalDate": {
                            "year": "2026",
                            "month": "7",
                            "day": arrival_day,
                        },
                        "arrivalTime": {"hour": "10", "minute": "35"},
                    }
                ],
            }
        ],
    }
```

- [ ] **Step 2: Add one-way search-result normalization test**

Add:

```python
def test_normalize_payload_maps_traveloka_search_results_offer() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [_traveloka_search_result()],
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:tv-search-1"
    assert offer.provider == "traveloka"
    assert offer.price_amount == 298.9
    assert offer.currency == "USD"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.actual_departure_date == "2026-07-10"
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert [(leg.origin, leg.destination, leg.flight_number) for leg in offer.legs] == [
        ("SGN", "BKK", "VJ-801")
    ]
```

- [ ] **Step 3: Add completed empty-result test**

Add:

```python
def test_normalize_payload_accepts_completed_empty_search_results() -> None:
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [],
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert offers == []
    assert errors == []
```

- [ ] **Step 4: Add round-trip search-result normalization test**

Add:

```python
def test_normalize_payload_maps_traveloka_round_trip_search_result() -> None:
    outbound = _traveloka_search_result(
        origin="SGN",
        destination="BKK",
        departure_day="10",
        arrival_day="10",
        flight_number="VJ-801",
    )["connectingFlightRoutes"][0]
    inbound = _traveloka_search_result(
        origin="BKK",
        destination="SGN",
        departure_day="17",
        arrival_day="17",
        flight_number="VJ-802",
    )["connectingFlightRoutes"][0]
    result = _traveloka_search_result(result_id="tv-rt-1", price_amount="17600")
    result["connectingFlightRoutes"] = [outbound, inbound]
    result["flightMetadata"]["tripDuration"] = "190"
    payload = {
        "data": {
            "meta": {"searchCompleted": True},
            "searchResults": [result],
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:tv-rt-1"
    assert offer.price_amount == 176.0
    assert offer.actual_return_date == "2026-07-17"
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]
```

- [ ] **Step 5: Run normalizer tests and confirm failure**

Run:

```bash
uv run pytest tests/test_traveloka_normalizer.py::test_normalize_payload_maps_traveloka_search_results_offer tests/test_traveloka_normalizer.py::test_normalize_payload_accepts_completed_empty_search_results tests/test_traveloka_normalizer.py::test_normalize_payload_maps_traveloka_round_trip_search_result -v
```

Expected: FAIL because `data.searchResults[]` is not canonicalized yet.

- [ ] **Step 6: Implement search-result canonicalization**

In `cheapy/providers/traveloka/normalizer.py`, add:

```python
from decimal import Decimal
```

Update `_itinerary_items` so `data.searchResults` wins before generic paths:

```python
def _itinerary_items(payload: object) -> list[object]:
    search_results = _list_at_path(payload, ("data", "searchResults"))
    if search_results is not None:
        return [_canonical_search_result(item) for item in search_results]
    for path in (
        ("data", "itineraries"),
        ("data", "flightSearchResult", "itineraries"),
        ("itineraries",),
        ("flightSearchResult", "itineraries"),
    ):
        items = _list_at_path(payload, path)
        if items is not None:
            return items
    return list(_recursive_offer_items(payload))
```

Add:

```python
def _canonical_search_result(item: object) -> dict[str, object]:
    if not isinstance(item, Mapping):
        raise ValueError("search result item must be a mapping")
    price = _traveloka_price_mapping(item)
    routes = item.get("connectingFlightRoutes")
    if not isinstance(routes, list):
        raise ValueError("search result has no connectingFlightRoutes")
    segments: list[dict[str, object]] = []
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("route must be a mapping")
        raw_segments = route.get("segments")
        if not isinstance(raw_segments, list):
            raise ValueError("route has no segments")
        for segment in raw_segments:
            segments.append(_canonical_segment(segment))
    return {
        "id": item.get("id"),
        "price": price,
        "durationMinutes": _metadata_int(item, "tripDuration"),
        "stops": _metadata_int(item, "totalNumStop"),
        "segments": segments,
    }


def _traveloka_price_mapping(item: Mapping[str, object]) -> dict[str, object]:
    for path in (
        ("fare", "display"),
        ("flightMetadata", "totalCombinedPrice"),
    ):
        display = _mapping_at_path(item, path)
        if display is None:
            continue
        currency_value = display.get("currencyValue")
        if not isinstance(currency_value, Mapping):
            continue
        currency = _currency_code(currency_value.get("currency"))
        if currency is None:
            continue
        amount = Decimal(str(currency_value.get("amount")))
        decimals = int(display.get("numOfDecimalPoint", 0))
        return {
            "amount": float(amount.scaleb(-decimals)),
            "currency": currency,
        }
    raise ValueError("search result has no supported price")


def _canonical_segment(segment: object) -> dict[str, object]:
    if not isinstance(segment, Mapping):
        raise ValueError("segment must be a mapping")
    return {
        "origin": _string_value(_required_value(segment, "departureAirport")),
        "destination": _string_value(_required_value(segment, "arrivalAirport")),
        "departureTime": _date_time_mapping_to_iso(
            _required_value(segment, "departureDate"),
            _required_value(segment, "departureTime"),
        ),
        "arrivalTime": _date_time_mapping_to_iso(
            _required_value(segment, "arrivalDate"),
            _required_value(segment, "arrivalTime"),
        ),
        "airlineCode": _string_value(_required_value(segment, "airlineCode")),
        "flightNumber": _string_value(_required_value(segment, "flightNumber")),
        "durationMinutes": int(_required_value(segment, "durationMinutes")),
    }
```

Add mapping helpers:

```python
def _mapping_at_path(
    payload: Mapping[str, object],
    path: tuple[str, ...],
) -> Mapping[str, object] | None:
    current: object = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if isinstance(current, Mapping):
        return current
    return None


def _metadata_int(item: Mapping[str, object], key: str) -> int | None:
    metadata = item.get("flightMetadata")
    if isinstance(metadata, Mapping) and key in metadata:
        return int(metadata[key])
    return None


def _date_time_mapping_to_iso(date_value: object, time_value: object) -> str:
    if not isinstance(date_value, Mapping) or not isinstance(time_value, Mapping):
        raise ValueError("date and time must be mappings")
    year = int(_required_value(date_value, "year"))
    month = int(_required_value(date_value, "month"))
    day = int(_required_value(date_value, "day"))
    hour = int(_required_value(time_value, "hour"))
    minute = int(_required_value(time_value, "minute"))
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:00"
```

- [ ] **Step 7: Run normalizer tests and confirm pass**

Run:

```bash
uv run pytest tests/test_traveloka_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit normalizer support**

Run:

```bash
git add cheapy/providers/traveloka/normalizer.py tests/test_traveloka_normalizer.py
git commit -m "feat: normalize traveloka search results"
```

## Task 6: Verify Provider Failure Preservation And Add Live Smoke Coverage

**Files:**
- Modify: `tests/test_search.py`
- Create: `tests/test_live_traveloka.py`

- [ ] **Step 1: Run existing provider-failure preservation test**

`tests/test_search.py` already contains
`test_search_returns_other_provider_offers_when_traveloka_times_out`, which
proves Traveloka failure does not suppress another provider's offers. Run it
before adding live smoke coverage:

```bash
uv run pytest tests/test_search.py::test_search_returns_other_provider_offers_when_traveloka_times_out -v
```

Expected: PASS.

- [ ] **Step 2: Create opt-in Traveloka live smoke test**

Create `tests/test_live_traveloka.py`:

```python
from __future__ import annotations

import asyncio
import os

import pytest

from cheapy.models import ProviderStatusCode
from cheapy.providers.base import ProviderExactRoundTripRequest
from cheapy.providers.traveloka.provider import create_provider


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CHEAPY_RUN_LIVE_TESTS") != "1",
        reason="Set CHEAPY_RUN_LIVE_TESTS=1 to run live provider smoke tests.",
    ),
]


def test_traveloka_live_cxr_han_round_trip_returns_structured_result() -> None:
    provider = create_provider()
    request = ProviderExactRoundTripRequest(
        origin="CXR",
        destination="HAN",
        departure_date="2026-05-20",
        return_date="2026-05-25",
    )

    result = asyncio.run(provider.search_exact_round_trip(request))

    assert result.provider_name == "traveloka"
    assert result.capability == "exact_round_trip"
    assert result.status in {
        ProviderStatusCode.SUCCESS,
        ProviderStatusCode.PARTIAL,
        ProviderStatusCode.FAILED,
    }
    for offer in result.offers:
        assert offer.provider == "traveloka"
        assert offer.price_amount > 0
        assert offer.currency == "USD"
```

- [ ] **Step 3: Run default tests for this task**

Run:

```bash
uv run pytest tests/test_search.py tests/test_live_traveloka.py -v
```

Expected: PASS, with `tests/test_live_traveloka.py` skipped unless `CHEAPY_RUN_LIVE_TESTS=1`.

- [ ] **Step 4: Commit live smoke coverage**

Run:

```bash
git add tests/test_live_traveloka.py
git commit -m "test: cover traveloka browser provider smoke"
```

## Task 7: Full Verification And Optional Live Check

**Files:**
- Verify all modified files

- [ ] **Step 1: Run Traveloka-focused tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_provider.py tests/test_traveloka_normalizer.py tests/test_live_traveloka.py -v
```

Expected: PASS, with live Traveloka skipped by default.

- [ ] **Step 2: Run full offline suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 3: Run package command smoke**

Run:

```bash
uv run cheapy --version
```

Expected: command exits 0 and prints the Cheapy version.

- [ ] **Step 4: Run opt-in Traveloka live smoke when local browser runtime is acceptable**

Run:

```bash
CHEAPY_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_traveloka.py -v
```

Expected: PASS if Traveloka allows the browser session. A structured provider result with status `FAILED` is acceptable when the failure is `blocked`, `timeout`, `browser_unavailable`, or `unsupported_response`; the test must not allow unhandled exceptions.

- [ ] **Step 5: Review git diff**

Run:

```bash
git diff --stat HEAD
git diff HEAD -- pyproject.toml cheapy/providers/traveloka tests
```

Expected: diff is limited to the approved Traveloka browser provider, dependency, tests, and optional live smoke coverage.

- [ ] **Step 6: Confirm no unintended changes remain**

Run:

```bash
git status --short
```

Expected: only intentional committed task changes are absent from the working
tree; user-owned unrelated deletions remain unstaged if they were present before
this work.

## Self-Review

- Spec coverage: This plan covers CloakBrowser default dependency, browser-only Traveloka runtime, no HTTP fallback, 45-second timeout, partial offers on timeout, max one `initial` and one `poll` fare payload consumed, no login/captcha solving/proxy rotation/persistent cookies/retry fanout, default offline tests, and opt-in live smoke.
- Placeholder scan: Clean. Each code-changing task includes concrete code and exact commands.
- Type consistency: `TravelokaCaptureResult` is defined in Task 2, returned by adapter methods in Task 3, consumed by provider tests and provider code in Task 4, and referenced by adapter coverage in Task 3.
