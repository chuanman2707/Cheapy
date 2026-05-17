# Cheapy Traveloka Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-enabled `traveloka` live provider that supports exact one-way and exact round-trip search through a conservative HTTP research adapter.

**Architecture:** Follow the existing `google_fli` provider shape: provider wrapper, HTTP adapter, normalizer, manifest, and focused tests. Core search and Contract V1 stay unchanged; `traveloka` enters normal search through the existing provider registry and planner capability matching.

**Tech Stack:** Python 3.12+, Pydantic v2 Contract V1 models, pytest, uv, Typer CLI, stdlib HTTP primitives or injected HTTP callables for offline tests.

---

## Working Notes

- Read `AGENTS.md` before editing.
- Read `.codex/skills/cheapy/SKILL.md` before touching MCP, CLI, contracts, packaging, or tests.
- Keep default tests offline. Never let `uv run pytest -v` call Traveloka.
- Keep `cheapy mcp` stdout protocol-clean; diagnostics and errors go to stderr or structured response fields.
- The Traveloka spec assumes the project owner has Traveloka support approval for this use, provided Cheapy does not send excessive traffic.
- Do not commit private support correspondence. Public docs can mention the permission assumption.
- The provider must be default enabled, but deployments without permission must be told to disable it before user-facing search.
- One selected Traveloka provider call must make at most one HTTP request.
- No automatic retry, no provider-internal fanout, no browser, no login, no cookies, no captcha handling, no proxy rotation.
- Traveloka timeout is 20 seconds per provider call.
- The worktree may contain unrelated staged deletions under `docs/` and unrelated `.gitignore` changes. Do not stage or commit them unless the user explicitly asks.

## File Structure

- Create `cheapy/providers/traveloka/__init__.py`
  - Exposes `PROVIDER_NAME = "traveloka"`.

- Create `cheapy/providers/traveloka/manifest.toml`
  - Registers the provider as default-enabled live provider with `exact_one_way` and `exact_round_trip`.

- Create `cheapy/providers/traveloka/adapter.py`
  - Owns Traveloka HTTP request construction, response fetching, response-size limit, status classification, and provider-local errors.
  - Provides an injectable HTTP function so tests do not open sockets.

- Create `cheapy/providers/traveloka/normalizer.py`
  - Converts parsed Traveloka payloads into Contract V1 `FlightOfferV1` values.
  - Handles item-level parse failures and currency-unavailable errors without leaking raw payloads.

- Create `cheapy/providers/traveloka/provider.py`
  - Implements `FlightProvider`.
  - Runs adapter calls through `asyncio.to_thread()` under `asyncio.wait_for(..., timeout=20)`.
  - Maps adapter and normalizer failures to `ProviderResult`.

- Create `tests/test_traveloka_adapter.py`
  - Offline tests for URL construction, HTTP status classification, response-size handling, JSON/HTML payload handling, and one-request/no-retry behavior.

- Create `tests/test_traveloka_normalizer.py`
  - Offline tests for one-way, round-trip, empty, partial parse, and currency-unavailable normalization.

- Create `tests/test_traveloka_provider.py`
  - Offline tests for provider success, partial, timeout, block/rate-limit/transport mapping, and no retry.

- Modify `tests/test_providers.py`
  - Add manifest discovery, provider loading, enabled providers, and search providers assertions for `traveloka`.

- Modify `tests/test_package_data.py`
  - Assert the Traveloka manifest is packaged and installed wheels can list/test it.

- Modify `tests/test_cli.py`
  - Add `traveloka` to provider list/test expectations.
  - Make live smoke provider-level failures report structured results instead of failing the CLI process when `--live` is explicitly enabled.

- Modify `tests/test_search.py`
  - Add a merge/status test proving Traveloka failure does not prevent another provider's offers from returning.

- Modify `README.md` and `README.vi.md`
  - Mention Traveloka as a fragile default-enabled research provider under the project permission assumption.

- Modify `cheapy/agent_hooks.py` and `.codex/skills/cheapy/SKILL.md`
  - Mention that agents should read each offer's `provider`; do not ask users to choose providers.

---

### Task 1: Provider Package And Registry Visibility

**Files:**
- Create: `cheapy/providers/traveloka/__init__.py`
- Create: `cheapy/providers/traveloka/manifest.toml`
- Create: `cheapy/providers/traveloka/provider.py`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_package_data.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing manifest and registry tests**

Add this test to `tests/test_providers.py` after `test_google_fli_manifest_is_discovered_from_package_resources`:

```python
def test_traveloka_manifest_is_discovered_from_package_resources() -> None:
    manifest = _manifest_by_name("traveloka")

    assert manifest == ProviderManifest(
        manifest_schema_version="1",
        name="traveloka",
        display_name="Traveloka research provider",
        default_enabled=True,
        provider_kind="live",
        module="cheapy.providers.traveloka.provider",
        capabilities=["exact_one_way", "exact_round_trip"],
    )
```

Update `test_provider_manifests_include_provider_kind` in `tests/test_providers.py`:

```python
def test_provider_manifests_include_provider_kind() -> None:
    manifests = discover_provider_manifests()
    kinds_by_name = {manifest.name: manifest.provider_kind for manifest in manifests}

    assert kinds_by_name["manual_fixture"] == "fixture"
    assert kinds_by_name["google_fli"] == "live"
    assert kinds_by_name["traveloka"] == "live"
```

Update `test_load_enabled_providers_loads_all_default_enabled_providers` in `tests/test_providers.py`:

```python
def test_load_enabled_providers_loads_all_default_enabled_providers() -> None:
    from cheapy.providers.registry import load_enabled_providers

    providers = load_enabled_providers()

    assert [provider.name for provider in providers] == [
        "google_fli",
        "manual_fixture",
        "traveloka",
    ]
    assert [provider.capabilities for provider in providers] == [
        ("exact_one_way", "exact_round_trip"),
        ("exact_one_way",),
        ("exact_one_way", "exact_round_trip"),
    ]
```

Update `test_load_search_providers_excludes_fixture_providers` in `tests/test_providers.py`:

```python
def test_load_search_providers_excludes_fixture_providers() -> None:
    providers = registry.load_search_providers()

    assert [provider.name for provider in providers] == ["google_fli", "traveloka"]
    assert [provider.capabilities for provider in providers] == [
        ("exact_one_way", "exact_round_trip"),
        ("exact_one_way", "exact_round_trip"),
    ]
    assert all(provider.name != "manual_fixture" for provider in providers)
```

- [ ] **Step 2: Write failing package-data and CLI expectation tests**

Update `tests/test_package_data.py` wheel assertions:

```python
assert "cheapy/providers/manual_fixture/manifest.toml" in names
assert "cheapy/providers/google_fli/manifest.toml" in names
assert "cheapy/providers/traveloka/manifest.toml" in names
```

Update the installed-resource script in `tests/test_package_data.py`:

```python
manual_manifest = files("cheapy.providers").joinpath("manual_fixture", "manifest.toml").read_text(encoding="utf-8")
google_manifest = files("cheapy.providers").joinpath("google_fli", "manifest.toml").read_text(encoding="utf-8")
traveloka_manifest = files("cheapy.providers").joinpath("traveloka", "manifest.toml").read_text(encoding="utf-8")

assert airports["version"] == 1
assert hubs["version"] == 1
assert "OurAirports" in readme
assert 'name = "manual_fixture"' in manual_manifest
assert 'provider_kind = "fixture"' in manual_manifest
assert 'name = "google_fli"' in google_manifest
assert 'provider_kind = "live"' in google_manifest
assert "default_enabled = true" in google_manifest
assert 'name = "traveloka"' in traveloka_manifest
assert 'provider_kind = "live"' in traveloka_manifest
assert "default_enabled = true" in traveloka_manifest
```

Update provider list/test assertions in `tests/test_package_data.py`:

```python
assert providers["manual_fixture"]["provider_kind"] == "fixture"
assert providers["google_fli"]["provider_kind"] == "live"
assert providers["google_fli"]["default_enabled"] is True
assert providers["traveloka"]["provider_kind"] == "live"
assert providers["traveloka"]["default_enabled"] is True
```

```python
assert providers["manual_fixture"]["status"] == "success"
assert providers["google_fli"]["live_smoke"] == "not_run"
assert providers["traveloka"]["live_smoke"] == "not_run"
```

Update `test_providers_list_prints_json` in `tests/test_cli.py` so the expected provider dictionary also contains:

```python
"traveloka": {
    "capabilities": ["exact_one_way", "exact_round_trip"],
    "default_enabled": True,
    "display_name": "Traveloka research provider",
    "enabled": True,
    "name": "traveloka",
    "provider_kind": "live",
},
```

Update `test_providers_test_prints_json`, `test_providers_test_default_does_not_run_live_provider`, and `test_providers_test_human_prints_success_report` in `tests/test_cli.py`:

```python
assert payload["providers_tested"] == 3
assert providers["traveloka"]["status"] == "skipped"
assert providers["traveloka"]["provider_kind"] == "live"
assert providers["traveloka"]["live_smoke"] == "not_run"
```

```python
assert "traveloka live exact_one_way: skipped" in result.stdout
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_providers.py::test_traveloka_manifest_is_discovered_from_package_resources tests/test_package_data.py::test_built_wheel_can_load_packaged_airport_and_provider_data tests/test_cli.py::test_providers_list_prints_json -v
```

Expected: FAIL because `cheapy.providers.traveloka` and its manifest do not exist.

- [ ] **Step 4: Create the Traveloka provider package skeleton**

Create `cheapy/providers/traveloka/__init__.py`:

```python
"""Traveloka research provider package."""

PROVIDER_NAME = "traveloka"
```

Create `cheapy/providers/traveloka/manifest.toml`:

```toml
manifest_schema_version = "1"
name = "traveloka"
display_name = "Traveloka research provider"
default_enabled = true
provider_kind = "live"
module = "cheapy.providers.traveloka.provider"
capabilities = ["exact_one_way", "exact_round_trip"]
```

Create `cheapy/providers/traveloka/provider.py` with a temporary provider that has the final shape:

```python
"""Traveloka live research provider."""

from __future__ import annotations

from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)


PROVIDER_NAME = "traveloka"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"


class TravelokaProvider:
    """Live provider backed by a conservative Traveloka HTTP research adapter."""

    name = PROVIDER_NAME
    capabilities = (EXACT_ONE_WAY_CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return _adapter_unavailable_result(EXACT_ONE_WAY_CAPABILITY)

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        return _adapter_unavailable_result(EXACT_ROUND_TRIP_CAPABILITY)


def create_provider() -> TravelokaProvider:
    return TravelokaProvider()


def _adapter_unavailable_result(capability: str) -> ProviderResult:
    started = perf_counter()
    return ProviderResult(
        provider_name=PROVIDER_NAME,
        capability=capability,
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[
            ErrorV1(
                code=ErrorCode.PROVIDER_FAILED,
                severity=Severity.ERROR,
                message_en="Traveloka provider adapter is unavailable.",
                details={
                    "provider": PROVIDER_NAME,
                    "capability": capability,
                    "failure_type": "adapter_unavailable",
                },
                retryable=False,
            )
        ],
        duration_ms=max(0, round((perf_counter() - started) * 1000)),
        retryable=False,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_providers.py::test_traveloka_manifest_is_discovered_from_package_resources tests/test_providers.py::test_load_enabled_providers_loads_all_default_enabled_providers tests/test_providers.py::test_load_search_providers_excludes_fixture_providers tests/test_package_data.py::test_built_wheel_can_load_packaged_airport_and_provider_data tests/test_cli.py::test_providers_list_prints_json tests/test_cli.py::test_providers_test_prints_json -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add cheapy/providers/traveloka tests/test_providers.py tests/test_package_data.py tests/test_cli.py
git commit -m "feat: register traveloka provider"
```

---

### Task 2: HTTP Adapter With One-Request No-Retry Behavior

**Files:**
- Create: `cheapy/providers/traveloka/adapter.py`
- Create: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Write failing adapter tests**

Create `tests/test_traveloka_adapter.py`:

```python
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka.adapter import (
    TravelokaAdapter,
    TravelokaHTTPResponse,
    TravelokaProviderError,
    build_search_url,
)


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def test_build_search_url_maps_one_way_request_to_safe_query() -> None:
    url = build_search_url(_one_way_request(), base_url="https://www.traveloka.com/en-en/flight")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.traveloka.com"
    assert parsed.path == "/en-en/flight"
    assert params["trip"] == ["oneway"]
    assert params["origin"] == ["SGN"]
    assert params["destination"] == ["BKK"]
    assert params["departureDate"] == ["2026-07-10"]
    assert params["currency"] == ["USD"]
    assert params["locale"] == ["en-en"]
    assert params["cabin"] == ["ECONOMY"]
    assert params["adults"] == ["1"]
    assert "returnDate" not in params


def test_build_search_url_maps_round_trip_request_to_safe_query() -> None:
    url = build_search_url(_round_trip_request(), base_url="https://www.traveloka.com/en-en/flight")

    params = parse_qs(urlparse(url).query)
    assert params["trip"] == ["roundtrip"]
    assert params["origin"] == ["SGN"]
    assert params["destination"] == ["BKK"]
    assert params["departureDate"] == ["2026-07-10"]
    assert params["returnDate"] == ["2026-07-17"]
    assert params["currency"] == ["USD"]


def test_adapter_fetches_once_without_retry() -> None:
    calls: list[str] = []

    def fake_http_get(url: str, headers: dict[str, str], timeout_seconds: float, max_bytes: int) -> TravelokaHTTPResponse:
        calls.append(url)
        return TravelokaHTTPResponse(
            status_code=200,
            body=b'{"data": {"itineraries": []}}',
            content_type="application/json",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    payload = adapter.search_exact_one_way(_one_way_request())

    assert payload == {"data": {"itineraries": []}}
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("status_code", "failure_type", "error_code", "retryable"),
    [
        (403, "blocked", ErrorCode.PROVIDER_BLOCKED, False),
        (429, "rate_limited", ErrorCode.PROVIDER_RATE_LIMITED, True),
        (503, "transport_error", ErrorCode.PROVIDER_FAILED, True),
    ],
)
def test_adapter_maps_http_status_to_structured_error(
    status_code: int,
    failure_type: str,
    error_code: ErrorCode,
    retryable: bool,
) -> None:
    def fake_http_get(url: str, headers: dict[str, str], timeout_seconds: float, max_bytes: int) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=status_code,
            body=b"blocked",
            content_type="text/plain",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == failure_type
    assert exc_info.value.error_code == error_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.http_status_code == status_code


def test_adapter_detects_bot_challenge_body() -> None:
    def fake_http_get(url: str, headers: dict[str, str], timeout_seconds: float, max_bytes: int) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=b"<html><title>captcha required</title></html>",
            content_type="text/html",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.retryable is False


def test_adapter_rejects_oversized_response() -> None:
    def fake_http_get(url: str, headers: dict[str, str], timeout_seconds: float, max_bytes: int) -> TravelokaHTTPResponse:
        assert max_bytes == 16
        return TravelokaHTTPResponse(
            status_code=200,
            body=b"x" * 17,
            content_type="application/json",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get, max_response_bytes=16)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "response_too_large"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: FAIL because `cheapy.providers.traveloka.adapter` does not exist.

- [ ] **Step 3: Implement the adapter**

Create `cheapy/providers/traveloka/adapter.py`:

```python
"""HTTP adapter for the Traveloka research provider."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)


DEFAULT_BASE_URL = "https://www.traveloka.com/en-en/flight"
DEFAULT_LOCALE = "en-en"
DEFAULT_CURRENCY = "USD"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
USER_AGENT = "Cheapy/0.1 TravelokaResearchProvider"

ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest
HTTPGet = Callable[[str, dict[str, str], float, int], "TravelokaHTTPResponse"]


@dataclass(frozen=True)
class TravelokaHTTPResponse:
    status_code: int
    body: bytes
    content_type: str
    final_url: str


class TravelokaProviderError(Exception):
    """Structured provider-local error safe to map into Contract V1."""

    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        error_code: ErrorCode,
        retryable: bool,
        http_status_code: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
        self.error_code = error_code
        self.retryable = retryable
        self.http_status_code = http_status_code
        self.exception_type = exception_type


class TravelokaAdapter:
    """Sync HTTP adapter around Traveloka public flight search surfaces."""

    configured_currency = DEFAULT_CURRENCY

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        http_get: HTTPGet | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._http_get = http_get if http_get is not None else _stdlib_http_get

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> dict[str, Any]:
        return self._search(request)

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> dict[str, Any]:
        return self._search(request)

    def _search(self, request: ProviderRequest) -> dict[str, Any]:
        url = build_search_url(request, base_url=self._base_url)
        headers = _headers()
        try:
            response = self._http_get(
                url,
                headers,
                self._timeout_seconds,
                self._max_response_bytes,
            )
        except TravelokaProviderError:
            raise
        except TimeoutError:
            raise
        except Exception as exc:
            raise TravelokaProviderError(
                failure_type="transport_error",
                message_en="Traveloka transport failed.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=True,
                exception_type=type(exc).__name__,
            ) from exc

        _raise_for_status(response)
        _raise_if_too_large(response.body, self._max_response_bytes)
        _raise_if_blocked_body(response.body)
        return _parse_body(response)


def build_search_url(request: ProviderRequest, *, base_url: str = DEFAULT_BASE_URL) -> str:
    trip = "roundtrip" if isinstance(request, ProviderExactRoundTripRequest) else "oneway"
    params = {
        "trip": trip,
        "origin": request.origin,
        "destination": request.destination,
        "departureDate": request.departure_date,
        "currency": DEFAULT_CURRENCY,
        "locale": DEFAULT_LOCALE,
        "cabin": "ECONOMY",
        "adults": str(request.passengers.adults),
        "children": str(request.passengers.children),
        "infantsInSeat": str(request.passengers.infants_in_seat),
        "infantsOnLap": str(request.passengers.infants_on_lap),
    }
    if isinstance(request, ProviderExactRoundTripRequest):
        params["returnDate"] = request.return_date
    return f"{base_url}?{urlencode(params)}"


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": USER_AGENT,
    }


def _stdlib_http_get(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    max_bytes: int,
) -> TravelokaHTTPResponse:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(max_bytes + 1)
            return TravelokaHTTPResponse(
                status_code=response.status,
                body=body,
                content_type=response.headers.get("content-type", ""),
                final_url=response.url,
            )
    except HTTPError as exc:
        body = exc.read(max_bytes + 1)
        return TravelokaHTTPResponse(
            status_code=exc.code,
            body=body,
            content_type=exc.headers.get("content-type", ""),
            final_url=exc.url,
        )
    except TimeoutError:
        raise
    except URLError as exc:
        raise TravelokaProviderError(
            failure_type="transport_error",
            message_en="Traveloka transport failed.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=type(exc).__name__,
        ) from exc


def _raise_for_status(response: TravelokaHTTPResponse) -> None:
    if response.status_code in {401, 403}:
        raise TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka blocked the request.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=response.status_code,
        )
    if response.status_code == 429:
        raise TravelokaProviderError(
            failure_type="rate_limited",
            message_en="Traveloka rate limited the request.",
            error_code=ErrorCode.PROVIDER_RATE_LIMITED,
            retryable=True,
            http_status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise TravelokaProviderError(
            failure_type="transport_error",
            message_en="Traveloka returned an HTTP error.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            http_status_code=response.status_code,
        )


def _raise_if_too_large(body: bytes, max_bytes: int) -> None:
    if len(body) > max_bytes:
        raise TravelokaProviderError(
            failure_type="response_too_large",
            message_en="Traveloka response exceeded the configured size limit.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )


def _raise_if_blocked_body(body: bytes) -> None:
    sample = body[:4096].decode("utf-8", errors="ignore").lower()
    blocked_markers = ("captcha", "bot challenge", "access denied", "unusual traffic")
    if any(marker in sample for marker in blocked_markers):
        raise TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka returned an access challenge.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
        )


def _parse_body(response: TravelokaHTTPResponse) -> dict[str, Any]:
    text = response.body.decode("utf-8", errors="replace")
    if "json" in response.content_type.lower() or text.lstrip().startswith(("{", "[")):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
    return {"_html": text, "_content_type": response.content_type}
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "feat: add traveloka http adapter"
```

---

### Task 3: Traveloka Payload Normalizer

**Files:**
- Create: `cheapy/providers/traveloka/normalizer.py`
- Create: `tests/test_traveloka_normalizer.py`

- [ ] **Step 1: Write failing normalizer tests**

Create `tests/test_traveloka_normalizer.py`:

```python
from __future__ import annotations

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka.normalizer import normalize_payload


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def _segment(
    *,
    origin: str = "SGN",
    destination: str = "BKK",
    departure_time: str = "2026-07-10T09:00:00",
    arrival_time: str = "2026-07-10T10:35:00",
    flight_number: str = "VJ801",
) -> dict[str, object]:
    return {
        "origin": origin,
        "destination": destination,
        "departureTime": departure_time,
        "arrivalTime": arrival_time,
        "airlineCode": "VJ",
        "flightNumber": flight_number,
        "durationMinutes": 95,
    }


def test_normalize_payload_maps_one_way_offer() -> None:
    payload = {
        "data": {
            "flightSearchResult": {
                "itineraries": [
                    {
                        "id": "tv-ow-1",
                        "price": {"amount": 88.5, "currency": "USD"},
                        "durationMinutes": 95,
                        "stops": 0,
                        "segments": [_segment()],
                    }
                ]
            }
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert errors == []
    assert len(offers) == 1
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:tv-ow-1"
    assert offer.provider == "traveloka"
    assert offer.price_amount == 88.5
    assert offer.currency == "USD"
    assert offer.requested_origin == "SGN"
    assert offer.requested_destination == "BKK"
    assert offer.actual_origin == "SGN"
    assert offer.actual_destination == "BKK"
    assert offer.requested_departure_date == "2026-07-10"
    assert offer.actual_departure_date == "2026-07-10"
    assert offer.departure_offset_days == 0
    assert offer.actual_return_date is None
    assert offer.total_duration_minutes == 95
    assert offer.stops == 0
    assert offer.flags.baggage_unknown is True
    assert [(leg.origin, leg.destination, leg.flight_number) for leg in offer.legs] == [
        ("SGN", "BKK", "VJ801")
    ]


def test_normalize_payload_maps_round_trip_offer() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "tv-rt-1",
                    "price": {"amount": 176.0, "currency": "USD"},
                    "durationMinutes": 190,
                    "stops": 0,
                    "segments": [
                        _segment(),
                        _segment(
                            origin="BKK",
                            destination="SGN",
                            departure_time="2026-07-17T11:00:00",
                            arrival_time="2026-07-17T12:35:00",
                            flight_number="VJ802",
                        ),
                    ],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _round_trip_request())

    assert errors == []
    offer = offers[0]
    assert offer.offer_id == "traveloka:SGN-BKK:2026-07-10:2026-07-17:tv-rt-1"
    assert offer.actual_return_date == "2026-07-17"
    assert offer.return_offset_days == 0
    assert [(leg.origin, leg.destination) for leg in offer.legs] == [
        ("SGN", "BKK"),
        ("BKK", "SGN"),
    ]


def test_normalize_payload_empty_result_returns_no_errors() -> None:
    offers, errors = normalize_payload({"data": {"itineraries": []}}, _one_way_request())

    assert offers == []
    assert errors == []


def test_normalize_payload_reports_currency_unavailable() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "missing-currency",
                    "price": {"amount": 88.5},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                }
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert offers == []
    assert len(errors) == 1
    assert errors[0].code == ErrorCode.PROVIDER_FAILED
    assert errors[0].details["provider"] == "traveloka"
    assert errors[0].details["failure_type"] == "currency_unavailable"


def test_normalize_payload_preserves_valid_offers_when_one_item_fails() -> None:
    payload = {
        "data": {
            "itineraries": [
                {
                    "id": "valid",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [_segment()],
                },
                {
                    "id": "invalid",
                    "price": {"amount": 100.0, "currency": "USD"},
                    "segments": [],
                },
            ]
        }
    }

    offers, errors = normalize_payload(payload, _one_way_request())

    assert [offer.offer_id for offer in offers] == ["traveloka:SGN-BKK:2026-07-10:valid"]
    assert len(errors) == 1
    assert errors[0].details["failure_type"] == "parse_error"
    assert errors[0].details["item_index"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_traveloka_normalizer.py -v
```

Expected: FAIL because `cheapy.providers.traveloka.normalizer` does not exist.

- [ ] **Step 3: Implement the normalizer**

Create `cheapy/providers/traveloka/normalizer.py`:

```python
"""Normalize Traveloka research payloads into Cheapy Contract V1 offers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from cheapy.models import (
    ErrorCode,
    ErrorV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    Severity,
)
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)


PROVIDER_NAME = "traveloka"
ProviderRequest = ProviderExactOneWayRequest | ProviderExactRoundTripRequest


class _ItemNormalizationError(Exception):
    def __init__(self, error: ErrorV1) -> None:
        super().__init__(error.message_en)
        self.error = error


def normalize_payload(
    payload: Mapping[str, Any],
    request: ProviderRequest,
) -> tuple[list[FlightOfferV1], list[ErrorV1]]:
    offers: list[FlightOfferV1] = []
    errors: list[ErrorV1] = []
    for item_index, item in enumerate(_itinerary_items(payload), start=1):
        try:
            offers.append(_normalize_item(item, request, item_index=item_index))
        except _ItemNormalizationError as exc:
            errors.append(exc.error)
    return _rank_offers(offers), errors


def _itinerary_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data", payload)
    direct = _as_mapping(data)
    if direct is not None:
        for key in ("itineraries", "flights", "results", "items"):
            value = direct.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        search_result = direct.get("flightSearchResult")
        if isinstance(search_result, Mapping):
            nested = search_result.get("itineraries")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, Mapping)]
    return list(_find_offer_like_mappings(payload))


def _find_offer_like_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if "price" in value and ("segments" in value or "legs" in value):
            yield value
        for child in value.values():
            yield from _find_offer_like_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _find_offer_like_mappings(child)


def _normalize_item(
    item: Mapping[str, Any],
    request: ProviderRequest,
    *,
    item_index: int,
) -> FlightOfferV1:
    try:
        item_id = str(item.get("id") or item.get("itineraryId") or item_index)
        price_amount, currency = _price(item)
        if currency is None:
            raise _ItemNormalizationError(_currency_error(item_index))
        legs = _legs(item)
        if not legs:
            raise ValueError("Traveloka item has no legs")
        _validate_leg_chain(legs, request)
        actual_departure_date = legs[0].departure_time[:10]
        actual_return_date = _actual_return_date(legs, request)
        departure_offset_days = _date_offset(
            actual_departure_date,
            request.requested_departure_date,
        )
        return_offset_days = (
            None
            if actual_return_date is None
            or not isinstance(request, ProviderExactRoundTripRequest)
            else _date_offset(actual_return_date, request.requested_return_date)
        )
        return FlightOfferV1(
            offer_id=_offer_id(request, item_id),
            price_amount=price_amount,
            currency=currency,
            comparable=True,
            rank_within_currency=item_index,
            global_rank=item_index,
            provider=PROVIDER_NAME,
            requested_origin=request.requested_origin,
            requested_destination=request.requested_destination,
            actual_origin=request.origin,
            actual_destination=request.destination,
            nearby_origin_distance_km=None,
            nearby_destination_distance_km=None,
            requested_departure_date=request.requested_departure_date,
            actual_departure_date=actual_departure_date,
            departure_offset_days=departure_offset_days,
            requested_return_date=(
                request.requested_return_date
                if isinstance(request, ProviderExactRoundTripRequest)
                else None
            ),
            actual_return_date=actual_return_date,
            return_offset_days=return_offset_days,
            legs=legs,
            total_duration_minutes=int(item.get("durationMinutes") or sum(leg.duration_minutes for leg in legs)),
            stops=int(item.get("stops") or max(0, len(legs) - (2 if isinstance(request, ProviderExactRoundTripRequest) else 1))),
            flags=OfferFlagsV1(
                uses_flexible_departure_date=departure_offset_days != 0,
                uses_flexible_return_date=return_offset_days not in (None, 0),
            ),
            fare_details_status="not_collected",
        )
    except _ItemNormalizationError:
        raise
    except Exception as exc:
        raise _ItemNormalizationError(_parse_error(item_index, exc)) from exc


def _price(item: Mapping[str, Any]) -> tuple[float, str | None]:
    price = item.get("price")
    if isinstance(price, Mapping):
        amount = price.get("amount") or price.get("value")
        currency = price.get("currency") or price.get("currencyCode")
    else:
        amount = item.get("priceAmount")
        currency = item.get("currency") or item.get("currencyCode")
    return float(amount), str(currency).upper() if currency is not None else None


def _legs(item: Mapping[str, Any]) -> list[FlightLegV1]:
    raw_segments = item.get("segments") or item.get("legs")
    if not isinstance(raw_segments, list):
        return []
    legs: list[FlightLegV1] = []
    for segment in raw_segments:
        if not isinstance(segment, Mapping):
            continue
        legs.append(
            FlightLegV1(
                origin=str(segment.get("origin") or segment.get("departureAirport")),
                destination=str(segment.get("destination") or segment.get("arrivalAirport")),
                departure_time=str(segment.get("departureTime")),
                arrival_time=str(segment.get("arrivalTime")),
                airline_code=str(segment.get("airlineCode") or segment.get("airline")),
                flight_number=str(segment.get("flightNumber")),
                duration_minutes=int(segment.get("durationMinutes") or 0),
            )
        )
    return legs


def _validate_leg_chain(legs: list[FlightLegV1], request: ProviderRequest) -> None:
    if legs[0].origin != request.origin:
        raise ValueError("Traveloka first leg origin does not match request")
    if isinstance(request, ProviderExactRoundTripRequest):
        if not any(leg.origin == request.destination for leg in legs[1:]):
            raise ValueError("Traveloka round-trip return leg is missing")
        if legs[-1].destination != request.origin:
            raise ValueError("Traveloka round-trip final destination does not match request")
        return
    if legs[-1].destination != request.destination:
        raise ValueError("Traveloka final destination does not match request")


def _actual_return_date(
    legs: list[FlightLegV1],
    request: ProviderRequest,
) -> str | None:
    if not isinstance(request, ProviderExactRoundTripRequest):
        return None
    for leg in legs[1:]:
        if leg.origin == request.destination:
            return leg.departure_time[:10]
    return None


def _offer_id(request: ProviderRequest, item_id: str) -> str:
    return_suffix = (
        f":{request.return_date}"
        if isinstance(request, ProviderExactRoundTripRequest)
        else ""
    )
    return (
        f"{PROVIDER_NAME}:{request.origin}-{request.destination}:"
        f"{request.departure_date}{return_suffix}:{item_id}"
    )


def _rank_offers(offers: list[FlightOfferV1]) -> list[FlightOfferV1]:
    return [
        offer.model_copy(
            update={
                "comparable": True,
                "rank_within_currency": rank,
                "global_rank": rank,
            }
        )
        for rank, offer in enumerate(offers, start=1)
    ]


def _date_offset(actual: str, requested: str) -> int:
    return (date.fromisoformat(actual) - date.fromisoformat(requested)).days


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _currency_error(item_index: int) -> ErrorV1:
    return ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Traveloka result did not include a trustworthy currency.",
        details={
            "provider": PROVIDER_NAME,
            "failure_type": "currency_unavailable",
            "item_index": item_index,
        },
        retryable=False,
    )


def _parse_error(item_index: int, exc: Exception) -> ErrorV1:
    return ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Traveloka result could not be parsed.",
        details={
            "provider": PROVIDER_NAME,
            "failure_type": "parse_error",
            "item_index": item_index,
            "exception_type": type(exc).__name__,
        },
        retryable=False,
    )
```

- [ ] **Step 4: Run normalizer tests**

Run:

```bash
uv run pytest tests/test_traveloka_normalizer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add cheapy/providers/traveloka/normalizer.py tests/test_traveloka_normalizer.py
git commit -m "feat: normalize traveloka payloads"
```

---

### Task 4: Provider Execution, Timeout, And Error Mapping

**Files:**
- Modify: `cheapy/providers/traveloka/provider.py`
- Create: `tests/test_traveloka_provider.py`

- [ ] **Step 1: Write failing provider tests**

Create `tests/test_traveloka_provider.py`:

```python
from __future__ import annotations

import asyncio
from time import sleep
from typing import Any

from cheapy.models import ErrorCode, ProviderStatusCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka.adapter import TravelokaProviderError
from cheapy.providers.traveloka.provider import TravelokaProvider


def _request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def _payload() -> dict[str, Any]:
    return {
        "data": {
            "itineraries": [
                {
                    "id": "tv-1",
                    "price": {"amount": 88.5, "currency": "USD"},
                    "durationMinutes": 95,
                    "stops": 0,
                    "segments": [
                        {
                            "origin": "SGN",
                            "destination": "BKK",
                            "departureTime": "2026-07-10T09:00:00",
                            "arrivalTime": "2026-07-10T10:35:00",
                            "airlineCode": "VJ",
                            "flightNumber": "VJ801",
                            "durationMinutes": 95,
                        }
                    ],
                }
            ]
        }
    }


class FakeAdapter:
    configured_currency = "USD"

    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.one_way_calls = 0
        self.round_trip_calls = 0

    def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> dict[str, Any]:
        self.one_way_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def search_exact_round_trip(self, request: ProviderExactRoundTripRequest) -> dict[str, Any]:
        self.round_trip_calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_traveloka_provider_returns_success_result() -> None:
    adapter = FakeAdapter(_payload())
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.provider_name == "traveloka"
    assert result.capability == "exact_one_way"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.errors == []
    assert [offer.provider for offer in result.offers] == ["traveloka"]


def test_traveloka_provider_returns_partial_result_for_item_parse_error() -> None:
    payload = _payload()
    payload["data"]["itineraries"].append(
        {
            "id": "bad",
            "price": {"amount": 100.0, "currency": "USD"},
            "segments": [],
        }
    )
    provider = TravelokaProvider(adapter=FakeAdapter(payload), timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.PARTIAL
    assert len(result.offers) == 1
    assert len(result.errors) == 1
    assert result.errors[0].details["failure_type"] == "parse_error"


def test_traveloka_provider_does_not_retry_adapter_error() -> None:
    adapter = FakeAdapter(
        TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka blocked the request.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=403,
        )
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert adapter.one_way_calls == 1
    assert result.status == ProviderStatusCode.FAILED
    assert result.retryable is False
    assert result.errors[0].code == ErrorCode.PROVIDER_BLOCKED
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "blocked",
        "http_status_code": 403,
    }


def test_traveloka_provider_maps_timeout() -> None:
    class SlowAdapter:
        configured_currency = "USD"

        def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> dict[str, Any]:
            sleep(0.1)
            return _payload()

        def search_exact_round_trip(self, request: ProviderExactRoundTripRequest) -> dict[str, Any]:
            raise AssertionError("round-trip should not be called")

    provider = TravelokaProvider(adapter=SlowAdapter(), timeout_seconds=0.01)

    result = asyncio.run(provider.search_exact_one_way(_request()))

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert result.errors[0].details["failure_type"] == "timeout"
    assert result.retryable is True


def test_traveloka_provider_routes_round_trip_to_adapter() -> None:
    adapter = FakeAdapter(
        {
            "data": {
                "itineraries": [
                    {
                        "id": "tv-rt-1",
                        "price": {"amount": 176.0, "currency": "USD"},
                        "durationMinutes": 190,
                        "stops": 0,
                        "segments": [
                            {
                                "origin": "SGN",
                                "destination": "BKK",
                                "departureTime": "2026-07-10T09:00:00",
                                "arrivalTime": "2026-07-10T10:35:00",
                                "airlineCode": "VJ",
                                "flightNumber": "VJ801",
                                "durationMinutes": 95,
                            },
                            {
                                "origin": "BKK",
                                "destination": "SGN",
                                "departureTime": "2026-07-17T11:00:00",
                                "arrivalTime": "2026-07-17T12:35:00",
                                "airlineCode": "VJ",
                                "flightNumber": "VJ802",
                                "durationMinutes": 95,
                            },
                        ],
                    }
                ]
            }
        }
    )
    provider = TravelokaProvider(adapter=adapter, timeout_seconds=1)

    result = asyncio.run(provider.search_exact_round_trip(_round_trip_request()))

    assert adapter.round_trip_calls == 1
    assert result.capability == "exact_round_trip"
    assert result.status == ProviderStatusCode.SUCCESS
    assert result.offers[0].actual_return_date == "2026-07-17"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_traveloka_provider.py -v
```

Expected: FAIL because `TravelokaProvider` still returns the skeleton failure.

- [ ] **Step 3: Implement provider execution**

Replace `cheapy/providers/traveloka/provider.py` with:

```python
"""Traveloka live research provider."""

from __future__ import annotations

import asyncio
from time import perf_counter

from cheapy.models import ErrorCode, ErrorV1, ProviderStatusCode, Severity
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
    ProviderResult,
)
from cheapy.providers.traveloka.adapter import (
    TravelokaAdapter,
    TravelokaProviderError,
)
from cheapy.providers.traveloka.normalizer import normalize_payload


PROVIDER_NAME = "traveloka"
EXACT_ONE_WAY_CAPABILITY = "exact_one_way"
EXACT_ROUND_TRIP_CAPABILITY = "exact_round_trip"
DEFAULT_TIMEOUT_SECONDS = 20.0


class TravelokaProvider:
    """Live provider backed by a conservative Traveloka HTTP research adapter."""

    name = PROVIDER_NAME
    capabilities = (EXACT_ONE_WAY_CAPABILITY, EXACT_ROUND_TRIP_CAPABILITY)

    def __init__(
        self,
        *,
        adapter: object | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._adapter = adapter if adapter is not None else TravelokaAdapter()
        self._timeout_seconds = timeout_seconds

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return await self._search(
            request,
            capability=EXACT_ONE_WAY_CAPABILITY,
            search_method_name="search_exact_one_way",
        )

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        return await self._search(
            request,
            capability=EXACT_ROUND_TRIP_CAPABILITY,
            search_method_name="search_exact_round_trip",
        )

    async def _search(
        self,
        request: ProviderExactOneWayRequest | ProviderExactRoundTripRequest,
        *,
        capability: str,
        search_method_name: str,
    ) -> ProviderResult:
        started = perf_counter()
        try:
            search_method = getattr(self._adapter, search_method_name)
            payload = await asyncio.wait_for(
                asyncio.to_thread(search_method, request),
                timeout=self._timeout_seconds,
            )
            offers, errors = normalize_payload(payload, request)
            errors = [_error_with_capability(error, capability) for error in errors]
        except TimeoutError:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    message_en="Traveloka provider timed out.",
                    failure_type="timeout",
                    retryable=True,
                    capability=capability,
                ),
            )
        except TravelokaProviderError as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=exc.error_code,
                    message_en=exc.message_en,
                    failure_type=exc.failure_type,
                    retryable=exc.retryable,
                    capability=capability,
                    http_status_code=exc.http_status_code,
                    exception_type=exc.exception_type,
                ),
            )
        except Exception as exc:
            return self._failed_result(
                started,
                capability,
                _provider_error(
                    code=ErrorCode.PROVIDER_FAILED,
                    message_en="Traveloka provider raised an unexpected exception.",
                    failure_type="unexpected_error",
                    retryable=False,
                    capability=capability,
                    exception_type=type(exc).__name__,
                ),
            )

        if errors and offers:
            status = ProviderStatusCode.PARTIAL
        elif errors:
            status = ProviderStatusCode.FAILED
        else:
            status = ProviderStatusCode.SUCCESS

        return ProviderResult(
            provider_name=self.name,
            capability=capability,
            status=status,
            offers=offers,
            warnings=[],
            errors=errors,
            duration_ms=_duration_ms(started),
            retryable=any(error.retryable for error in errors),
        )

    def _failed_result(
        self,
        started: float,
        capability: str,
        error: ErrorV1,
    ) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            capability=capability,
            status=ProviderStatusCode.FAILED,
            offers=[],
            warnings=[],
            errors=[error],
            duration_ms=_duration_ms(started),
            retryable=error.retryable,
        )


def create_provider() -> TravelokaProvider:
    return TravelokaProvider()


def _provider_error(
    *,
    code: ErrorCode,
    message_en: str,
    failure_type: str,
    retryable: bool,
    capability: str,
    http_status_code: int | None = None,
    exception_type: str | None = None,
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
    return ErrorV1(
        code=code,
        severity=Severity.ERROR,
        message_en=message_en,
        details=details,
        retryable=retryable,
    )


def _error_with_capability(error: ErrorV1, capability: str) -> ErrorV1:
    details = dict(error.details)
    details["capability"] = capability
    return error.model_copy(update={"details": details})


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
uv run pytest tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 5: Run all Traveloka provider-local tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add cheapy/providers/traveloka/provider.py tests/test_traveloka_provider.py
git commit -m "feat: execute traveloka provider calls"
```

---

### Task 5: Search And CLI Integration Semantics

**Files:**
- Modify: `tests/test_search.py`
- Modify: `tests/test_cli.py`
- Modify: `cheapy/cli.py`

- [ ] **Step 1: Write failing search integration test**

Add this helper class to `tests/test_search.py` near the existing fake providers:

```python
class _FailingTravelokaProvider:
    name = "traveloka"
    capabilities = ("exact_one_way", "exact_round_trip")

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            capability="exact_one_way",
            status=ProviderStatusCode.FAILED,
            offers=[],
            warnings=[],
            errors=[
                ErrorV1(
                    code=ErrorCode.PROVIDER_TIMEOUT,
                    severity=Severity.ERROR,
                    message_en="Traveloka provider timed out.",
                    details={
                        "provider": "traveloka",
                        "capability": "exact_one_way",
                        "failure_type": "timeout",
                    },
                    retryable=True,
                )
            ],
            duration_ms=20_000,
            retryable=True,
        )

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        raise AssertionError("round-trip should not be called")
```

Add this test to `tests/test_search.py`:

```python
def test_search_returns_other_provider_offers_when_traveloka_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    google_result = ProviderResult(
        provider_name="google_fli",
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id="google:1",
                provider="google_fli",
                currency="USD",
                price_amount=120.0,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=5,
        retryable=False,
    )
    monkeypatch.setattr(
        "cheapy.search.load_search_providers",
        lambda: [_ProviderFromResult(google_result), _FailingTravelokaProvider()],
    )

    response = search_exact(_request())

    assert response.status == SearchStatus.PARTIAL
    assert [offer.provider for offer in response.offers] == ["google_fli"]
    statuses = {status.provider_name: status for status in response.provider_statuses}
    assert statuses["google_fli"].status == ProviderStatusCode.SUCCESS
    assert statuses["traveloka"].status == ProviderStatusCode.FAILED
    assert statuses["traveloka"].errors[0].code == ErrorCode.PROVIDER_TIMEOUT
```

- [ ] **Step 2: Write failing live CLI structured-failure test**

Replace `test_providers_test_live_reports_provider_failure` in `tests/test_cli.py` with:

```python
def test_providers_test_live_reports_structured_provider_failure_without_crashing(
    monkeypatch,
) -> None:
    class FailingLiveProvider:
        name = "traveloka"
        capabilities = ("exact_one_way",)

        async def search_exact_one_way(
            self,
            request: ProviderExactOneWayRequest,
        ) -> ProviderResult:
            return ProviderResult(
                provider_name=self.name,
                capability="exact_one_way",
                status=ProviderStatusCode.FAILED,
                offers=[],
                warnings=[],
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_BLOCKED,
                        severity=Severity.ERROR,
                        message_en="Traveloka blocked the request.",
                        details={
                            "provider": "traveloka",
                            "capability": "exact_one_way",
                            "failure_type": "blocked",
                        },
                        retryable=False,
                    )
                ],
                duration_ms=1,
                retryable=False,
            )

    monkeypatch.setenv("CHEAPY_RUN_LIVE_TESTS", "1")
    monkeypatch.setattr("cheapy.cli.load_live_test_providers", lambda: [FailingLiveProvider()])

    result = runner.invoke(app, ["providers", "test", "--live"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    provider = payload["providers"][0]
    assert provider["name"] == "traveloka"
    assert provider["status"] == "failed"
    assert provider["error_count"] == 1
    assert provider["live_smoke"] == "run"
```

- [ ] **Step 3: Run tests to verify the CLI test fails**

Run:

```bash
uv run pytest tests/test_search.py::test_search_returns_other_provider_offers_when_traveloka_times_out tests/test_cli.py::test_providers_test_live_reports_structured_provider_failure_without_crashing -v
```

Expected: search test should already pass or fail only on status expectations; CLI test FAILS because `providers test --live` exits nonzero on provider-level failure.

- [ ] **Step 4: Update CLI live-failure semantics**

In `cheapy/cli.py`, replace the `failed_reports` block with:

```python
    failed_reports = [
        report
        for report in reports
        if report["status"] == ProviderStatusCode.FAILED.value
    ]
    if failed_reports and not live:
        _json_echo(
            _error_payload(
                "PROVIDER_TEST_FAILED",
                "One or more provider checks failed.",
                "Run 'cheapy providers test --human' for a concise provider report.",
            ),
            err=True,
        )
        raise typer.Exit(code=1)
```

This keeps default local provider checks strict while allowing explicitly enabled live smoke runs to report structured provider failures without treating block/timeout as a CLI crash.

- [ ] **Step 5: Run CLI and search tests**

Run:

```bash
uv run pytest tests/test_search.py::test_search_returns_other_provider_offers_when_traveloka_times_out tests/test_cli.py::test_providers_test_live_reports_structured_provider_failure_without_crashing tests/test_cli.py::test_providers_test_reports_provider_level_failure -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add tests/test_search.py tests/test_cli.py cheapy/cli.py
git commit -m "fix: report traveloka live smoke failures structurally"
```

---

### Task 6: Documentation And Agent Guidance

**Files:**
- Modify: `README.md`
- Modify: `README.vi.md`
- Modify: `cheapy/agent_hooks.py`
- Modify: `.codex/skills/cheapy/SKILL.md`
- Test: `tests/test_agent_hooks.py`
- Test: `tests/test_cheapy_skill.py`

- [ ] **Step 1: Update docs and guidance tests**

Update `tests/test_agent_hooks.py` to assert the generated instructions mention Traveloka and provider attribution without telling agents to choose providers:

```python
def test_agent_hooks_mentions_traveloka_provider_attribution() -> None:
    instructions = render_agent_instructions()

    assert "traveloka" in instructions.lower()
    assert "Use each offer's `provider` field" in instructions
    assert "Do not ask the user to choose providers." in instructions
```

Update `tests/test_cheapy_skill.py` with the same content check against `.codex/skills/cheapy/SKILL.md`:

```python
def test_cheapy_skill_mentions_traveloka_without_provider_selection() -> None:
    text = Path(".codex/skills/cheapy/SKILL.md").read_text(encoding="utf-8")

    assert "traveloka" in text.lower()
    assert "Use each offer's `provider` field" in text
    assert "Do not ask the user to choose providers." in text
```

If `Path` is not imported in `tests/test_cheapy_skill.py`, add:

```python
from pathlib import Path
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_agent_hooks.py tests/test_cheapy_skill.py -v
```

Expected: FAIL because docs/guidance do not mention Traveloka yet.

- [ ] **Step 3: Update README files**

In `README.md`, update the provider feature row to:

```markdown
| Provider registry | Packaged providers include a deterministic fixture plus Google Fli and Traveloka live provider paths. |
```

Add a short note near the live provider section:

```markdown
Traveloka is a default-enabled research provider in this codebase under the
project owner's stated Traveloka support approval. It is intentionally
conservative: no login, no browser, no retries, no provider-internal fanout,
and a 20 second per-call timeout. Deployments without Traveloka permission
should disable this provider before user-facing search.
```

In `README.vi.md`, add the Vietnamese equivalent:

```markdown
Traveloka là research provider được bật mặc định trong codebase này theo xác
nhận của project owner rằng Traveloka support đã đồng ý cho dùng, miễn là không
gửi quá nhiều request. Provider này chạy thận trọng: không login, không browser,
không retry, không fanout nội bộ, và timeout 20 giây cho mỗi provider call. Bản
deploy không có permission từ Traveloka nên tắt provider này trước khi chạy
user-facing search.
```

- [ ] **Step 4: Update agent guidance**

In `cheapy/agent_hooks.py`, update the managed instruction block so it includes:

```text
- Cheapy may call multiple enabled live providers, including google_fli and traveloka.
- Do not ask the user to choose providers.
- Use each offer's `provider` field when explaining where a fare came from.
- Traveloka is a default-enabled research provider for this codebase under the project permission assumption and may return structured timeout, block, or parse failures.
```

In `.codex/skills/cheapy/SKILL.md`, update the managed instruction block with the same bullets.

- [ ] **Step 5: Run docs/guidance tests**

Run:

```bash
uv run pytest tests/test_agent_hooks.py tests/test_cheapy_skill.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add README.md README.vi.md cheapy/agent_hooks.py .codex/skills/cheapy/SKILL.md tests/test_agent_hooks.py tests/test_cheapy_skill.py
git commit -m "docs: document traveloka provider behavior"
```

---

### Task 7: Verification

**Files:**
- No new files.
- Verify all touched test areas and full suite.

- [ ] **Step 1: Run provider and registry tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py tests/test_providers.py -v
```

Expected: PASS.

- [ ] **Step 2: Run CLI, package, search, and docs tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_package_data.py tests/test_search.py tests/test_agent_hooks.py tests/test_cheapy_skill.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS with no live network access.

- [ ] **Step 4: Verify provider list**

Run:

```bash
uv run cheapy providers list
```

Expected JSON includes:

```json
{
  "name": "traveloka",
  "display_name": "Traveloka research provider",
  "capabilities": ["exact_one_way", "exact_round_trip"],
  "default_enabled": true,
  "enabled": true,
  "provider_kind": "live"
}
```

- [ ] **Step 5: Verify default provider test skips live providers**

Run:

```bash
uv run cheapy providers test
```

Expected JSON includes:

```json
{
  "name": "traveloka",
  "provider_kind": "live",
  "capability": "exact_one_way",
  "status": "skipped",
  "offer_count": 0,
  "error_count": 0,
  "live_smoke": "not_run"
}
```

- [ ] **Step 6: Inspect git status**

Run:

```bash
git status --short
```

Expected: only intentional Traveloka implementation changes are committed. Unrelated pre-existing `.gitignore` changes or staged deletions under `docs/` may still appear; do not include them in Traveloka implementation commits.

---

## Self-Review Checklist

- Spec coverage: provider package, default-enabled manifest, HTTP-only adapter, exact one-way, exact round-trip, timeout, no retry/fanout, structured errors, packaging, CLI, docs, and offline tests are covered.
- Placeholder scan: this plan contains concrete file paths, commands, snippets, and expected results.
- Type consistency: provider methods use `ProviderExactOneWayRequest`, `ProviderExactRoundTripRequest`, and `ProviderResult`; adapter errors use existing `ErrorCode` values; normalized offers use Contract V1 models.
- Subagent review note covered: Task 2 and Task 4 test one selected provider call makes one adapter/HTTP call and does not retry.

