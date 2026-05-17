# Cheapy Traveloka Client API Replayer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Traveloka page-shell fetching with a discovery-backed HTTP-only client API replayer, while failing closed when a replayable endpoint cannot be discovered safely.

**Architecture:** This plan covers Phase 1 discovery, fail-fast correctness, and HTTP request-budget safety. Full replayer implementation is intentionally gated: after discovery commits an exact API contract, write a second plan using that concrete endpoint/body/schema. This avoids implementing against guessed API details.

**Tech Stack:** Python 3.12+, stdlib `urllib`, Pydantic Contract V1, pytest, Typer CLI, Playwright CLI only for discovery.

---

## File Structure

- `docs/superpowers/specs/2026-05-14-cheapy-traveloka-discovered-api-contract.md`
  - New discovery artifact. Records either a replayable Traveloka API contract or the hard blocker that prevents safe replay.
- `cheapy/providers/traveloka/adapter.py`
  - Existing HTTP adapter. Fail-fast response classification and redirect/request-budget safety live here for this phase.
- `cheapy/providers/traveloka/provider.py`
  - Existing provider wrapper. Verifies structured adapter failures map into `ProviderResult`.
- `tests/test_traveloka_adapter.py`
  - Adapter response classification, request-budget, redirect, and no-retry coverage.
- `tests/test_traveloka_provider.py`
  - Provider-level mapping for unsupported Traveloka responses.

---

### Task 1: Discover And Commit Traveloka API Contract

**Files:**
- Create: `docs/superpowers/specs/2026-05-14-cheapy-traveloka-discovered-api-contract.md`

- [ ] **Step 1: Start from a clean browser capture session**

Run:

```bash
rm -rf .playwright-cli
export PWCLI=/Users/binhan/.codex/skills/playwright/scripts/playwright_cli.sh
"$PWCLI" close-all || true
```

Expected: no committed files change. It is acceptable if `close-all` reports no sessions.

- [ ] **Step 2: Capture the user route without direct fullsearch replay**

Run:

```bash
export PWCLI=/Users/binhan/.codex/skills/playwright/scripts/playwright_cli.sh
"$PWCLI" open 'https://www.traveloka.com/en-en/flight?trip=roundtrip&origin=CXR&destination=HAN&departureDate=2026-05-20&currency=USD&locale=en-en&cabin=ECONOMY&adults=1&children=0&infantsInSeat=0&infantsOnLap=0&returnDate=2026-05-25'
"$PWCLI" requests --json > /tmp/traveloka-landing-requests.json
"$PWCLI" snapshot --json > /tmp/traveloka-landing-snapshot.json
```

Expected: page loads without adding repo files. The capture may show only landing-page, analytics, airport, coupon, and variant endpoints. Do not commit `/tmp` files.

- [ ] **Step 3: Discover search behavior through normal UI interaction**

Run:

```bash
export PWCLI=/Users/binhan/.codex/skills/playwright/scripts/playwright_cli.sh
"$PWCLI" snapshot --json
```

Use the fresh snapshot to interact with the visible Traveloka form. Fill:

- origin: `Nha Trang` or `CXR`
- destination: `Hanoi` or `HAN`
- departure date: `May 20, 2026`
- return date: `May 25, 2026`

Then click `Search Flights` and run:

```bash
"$PWCLI" requests --json > /tmp/traveloka-search-requests.json
```

Expected: either a Traveloka first-party search API appears, or Traveloka blocks with captcha/interstitial. Record the exact interaction notes in the discovery artifact. Do not commit browser snapshots.

- [ ] **Step 4: Inspect candidate first-party API requests only**

Run:

```bash
python - <<'PY'
from pathlib import Path
import re

text = Path('/tmp/traveloka-search-requests.json').read_text(encoding='utf-8')
skip_fragments = (
    '/api/log',
    '/api/v1/tvlk/events',
    '/api/v1/metrics',
    '/api/dfp/',
    '/api/sen/',
    'google.com',
    'analytics',
    'amplitude',
    'contentsquare',
)
for line in text.splitlines():
    lowered = line.lower()
    if 'www.traveloka.com' not in lowered:
        continue
    if any(fragment in lowered for fragment in skip_fragments):
        continue
    if re.search(r'flight|search|fare|price|schedule|availability|booking|fullsearch|api', line, flags=re.I):
        print(line)
PY
```

Expected: a short candidate list. If no candidate exists and captcha/fullsearch is the only first-party result path, discovery is blocked.

- [ ] **Step 5: Inspect request and response metadata for each printed candidate**

For every candidate request number printed in Step 4, run the Playwright CLI request-inspection commands using that actual numeric request ID. For example, if Step 4 prints request `364`, run:

```bash
export PWCLI=/Users/binhan/.codex/skills/playwright/scripts/playwright_cli.sh
"$PWCLI" request-headers 364 > /tmp/traveloka-candidate-364-headers.txt
"$PWCLI" request-body 364 > /tmp/traveloka-candidate-364-request-body.txt
"$PWCLI" response-headers 364 > /tmp/traveloka-candidate-364-response-headers.txt
"$PWCLI" response-body 364 > /tmp/traveloka-candidate-364-response-body.txt
```

Keep each inspected request in its own numbered `/tmp/traveloka-candidate-...` files. Do not commit these `/tmp` files.

Expected: metadata sufficient to classify the candidate. Redact cookies, tokens, visitor IDs, WAF/captcha values, full request headers, and full raw response bodies from any committed artifact.

- [ ] **Step 6: Classify replayability**

Classify the endpoint as replayable only when all of these are true:

- first-party HTTPS host is `www.traveloka.com`
- endpoint is not analytics, logging, tracking, metrics, coupon, airport autocomplete, variant evaluation, or user profile
- no login is required
- no captcha/WAF artifact is required
- no persisted cookie or token is required
- total request budget is one optional bootstrap request plus one API request, including redirects
- response body is JSON with a stable search-result envelope or an explicit no-results envelope

Expected: one of two outcomes is clear:

- `Replayable with HTTP-only runtime.`
- `Blocked. No replayable HTTP-only client API endpoint was discovered within the approved constraints.`

- [ ] **Step 7: Write the discovery artifact**

Create `docs/superpowers/specs/2026-05-14-cheapy-traveloka-discovered-api-contract.md`.

If replayable, the artifact must include concrete discovered values for:

- endpoint method and safe URL path
- whether bootstrap is required
- request budget, including redirects
- allowed first-party ephemeral bootstrap artifacts
- redacted request schema for one-way and round-trip
- response envelope keys
- offer collection path
- no-results path and value
- price, segment, airline, duration, and stop-count paths
- block, captcha, rate-limit, and unsupported-response indicators
- redaction notes

If blocked, the artifact must include concrete safe evidence for:

- first blocked URL path or first missing required artifact
- HTTP status or sanitized marker name
- which approved constraint would be violated
- required runtime behavior: structured `blocked`, `bootstrap_unavailable`, or `unsupported_response` failure
- redaction notes

Expected: artifact contains no raw cookies, tokens, full headers, full bodies, or browser snapshots.

- [ ] **Step 8: Remove browser artifacts**

Run:

```bash
rm -rf .playwright-cli
rm -f /tmp/traveloka-landing-requests.json /tmp/traveloka-landing-snapshot.json
rm -f /tmp/traveloka-search-requests.json
rm -f /tmp/traveloka-candidate-*-headers.txt
rm -f /tmp/traveloka-candidate-*-request-body.txt
rm -f /tmp/traveloka-candidate-*-response-headers.txt
rm -f /tmp/traveloka-candidate-*-response-body.txt
```

Expected: `git status --short` shows only the discovery artifact plus unrelated pre-existing deletions.

- [ ] **Step 9: Commit discovery artifact**

Run:

```bash
git add -f docs/superpowers/specs/2026-05-14-cheapy-traveloka-discovered-api-contract.md
git commit -m "docs: record traveloka client api discovery"
```

Expected: commit includes only the discovery artifact.

---

### Task 2: Fail Fast On Unsupported Traveloka HTML And Invalid API Bodies

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/test_traveloka_adapter.py`
- Modify: `tests/test_traveloka_provider.py`

- [ ] **Step 1: Add failing adapter tests for unsupported response classification**

Add these tests to `tests/test_traveloka_adapter.py`:

```python
def test_adapter_rejects_html_app_shell_without_supported_api_payload() -> None:
    def fake_http_get(url: str, headers: dict[str, str], timeout_seconds: float, max_bytes: int) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=(
                b'<!DOCTYPE html><html><head><title>Cheap Flights</title></head>'
                b'<body><script id="__NEXT_DATA__">{}</script></body></html>'
            ),
            content_type="text/html; charset=utf-8",
            final_url="https://www.traveloka.com/en-en/flight",
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_round_trip(_round_trip_request())

    assert exc_info.value.failure_type == "unsupported_response"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is False
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_adapter_rejects_invalid_json_api_body() -> None:
    def fake_http_get(url: str, headers: dict[str, str], timeout_seconds: float, max_bytes: int) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=b'{"data": ',
            content_type="application/json",
            final_url="https://www.traveloka.com/api/flight/search",
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "invalid_json"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_adapter_rejects_html_app_shell_without_supported_api_payload tests/test_traveloka_adapter.py::test_adapter_rejects_invalid_json_api_body -v
```

Expected: FAIL because the current adapter returns HTML fallback payloads instead of structured provider errors.

- [ ] **Step 3: Implement structured unsupported and invalid JSON errors**

In `cheapy/providers/traveloka/adapter.py`, add:

```python
def _unsupported_response_error() -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="unsupported_response",
        message_en="Traveloka returned an unsupported response shape.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )


def _invalid_json_error() -> TravelokaProviderError:
    return TravelokaProviderError(
        failure_type="invalid_json",
        message_en="Traveloka returned invalid JSON.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
    )
```

Replace `_parse_body` with:

```python
def _parse_body(response: TravelokaHTTPResponse) -> dict[str, Any]:
    text = response.body.decode("utf-8", errors="replace")
    content_type = response.content_type.lower()
    looks_json = "json" in content_type or text.lstrip().startswith(("{", "["))
    if not looks_json:
        raise _unsupported_response_error()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise _invalid_json_error() from None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"data": parsed}
    raise _unsupported_response_error()
```

- [ ] **Step 4: Update stale adapter test that expected HTML fallback**

Replace `test_adapter_returns_html_fallback_for_invalid_json_body` in `tests/test_traveloka_adapter.py` with an assertion that invalid JSON raises `invalid_json`, or delete it if the new test in Step 1 covers the same behavior.

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 5: Add provider-level unsupported response mapping test**

Add this test to `tests/test_traveloka_provider.py`:

```python
def test_traveloka_provider_maps_unsupported_response() -> None:
    class UnsupportedAdapter:
        def search_exact_one_way(self, request: ProviderExactOneWayRequest) -> dict[str, object]:
            raise TravelokaProviderError(
                failure_type="unsupported_response",
                message_en="Traveloka returned an unsupported response shape.",
                error_code=ErrorCode.PROVIDER_FAILED,
                retryable=False,
            )

    result = asyncio.run(
        TravelokaProvider(adapter=UnsupportedAdapter(), timeout_seconds=1).search_exact_one_way(
            _one_way_request()
        )
    )

    assert result.status == ProviderStatusCode.FAILED
    assert result.offers == []
    assert result.errors[0].code == ErrorCode.PROVIDER_FAILED
    assert result.errors[0].details == {
        "provider": "traveloka",
        "capability": "exact_one_way",
        "failure_type": "unsupported_response",
    }
```

Run:

```bash
uv run pytest tests/test_traveloka_provider.py::test_traveloka_provider_maps_unsupported_response -v
```

Expected: PASS after provider already maps `TravelokaProviderError` details.

- [ ] **Step 6: Commit fail-fast behavior**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py tests/test_traveloka_provider.py
git commit -m "fix: fail fast on unsupported traveloka responses"
```

Expected: commit includes only adapter/provider tests and adapter changes.

---

### Task 3: Enforce Traveloka HTTP Request Budget And Redirect Safety

**Files:**
- Modify: `cheapy/providers/traveloka/adapter.py`
- Modify: `tests/test_traveloka_adapter.py`

- [ ] **Step 1: Add failing tests for redirect classification**

Add these tests to `tests/test_traveloka_adapter.py`:

```python
def test_adapter_rejects_redirect_to_non_traveloka_host() -> None:
    response = TravelokaHTTPResponse(
        status_code=302,
        body=b"",
        content_type="text/plain",
        final_url="https://geo.captcha-delivery.com/interstitial/",
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        traveloka_adapter._raise_if_disallowed_final_url(response.final_url)

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED


def test_adapter_classifies_http_redirect_as_blocked() -> None:
    response = TravelokaHTTPResponse(
        status_code=302,
        body=b"",
        content_type="text/plain",
        final_url="https://www.traveloka.com/en-en/flight/fullsearch",
    )

    with pytest.raises(TravelokaProviderError) as exc_info:
        traveloka_adapter._raise_for_status(response)

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.http_status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_adapter_rejects_redirect_to_non_traveloka_host tests/test_traveloka_adapter.py::test_adapter_classifies_http_redirect_as_blocked -v
```

Expected: FAIL because redirect classification helpers are not implemented yet.

- [ ] **Step 3: Implement final URL allowlist check**

In `cheapy/providers/traveloka/adapter.py`, import `urlparse`:

```python
from urllib.parse import urlencode, urlparse
```

Add:

```python
def _raise_if_disallowed_final_url(final_url: str) -> None:
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or parsed.netloc != "www.traveloka.com":
        raise TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka redirected to an unsupported challenge host.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
        )
```

Call `_raise_if_disallowed_final_url(response.final_url)` in `_search` before `_raise_for_status(response)`.

- [ ] **Step 4: Classify HTTP 3xx as blocked**

Update `_raise_for_status` in `cheapy/providers/traveloka/adapter.py`:

```python
    if 300 <= response.status_code < 400:
        raise TravelokaProviderError(
            failure_type="blocked",
            message_en="Traveloka returned an unsupported redirect.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=response.status_code,
        )
```

Place the block before 4xx handling.

- [ ] **Step 5: Add no-auto-redirect test for stdlib HTTP**

Add this test to `tests/test_traveloka_adapter.py`:

```python
def test_stdlib_http_get_surfaces_redirect_without_following(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []

    class FakeRedirectBody:
        def read(self, size: int) -> bytes:
            return b""

        def close(self) -> None:
            return None

    class FakeOpener:
        def open(self, request, timeout: float):
            opened.append(request.full_url)
            raise HTTPError(
                request.full_url,
                302,
                "Found",
                {"content-type": "text/html", "location": "https://www.traveloka.com/next"},
                FakeRedirectBody(),
            )

    monkeypatch.setattr(traveloka_adapter, "build_opener", lambda handler: FakeOpener())

    response = traveloka_adapter._stdlib_http_get(
        "https://www.traveloka.com/search",
        {"User-Agent": "CheapyTest"},
        7.5,
        12,
    )

    assert opened == ["https://www.traveloka.com/search"]
    assert response.status_code == 302
```

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py::test_stdlib_http_get_surfaces_redirect_without_following -v
```

Expected: FAIL until `_stdlib_http_get` uses a no-redirect opener.

- [ ] **Step 6: Prevent invisible stdlib redirects**

In `cheapy/providers/traveloka/adapter.py`, replace the `urllib.request` import with:

```python
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
```

Add:

```python
class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
```

In `_stdlib_http_get`, replace `urlopen(request, timeout=timeout_seconds)` with:

```python
opener = build_opener(_NoRedirectHandler)
with opener.open(request, timeout=timeout_seconds) as response:
```

Keep the existing HTTPError path so 3xx responses return a `TravelokaHTTPResponse` for classification.

- [ ] **Step 7: Run adapter tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit request-budget safety**

Run:

```bash
git add cheapy/providers/traveloka/adapter.py tests/test_traveloka_adapter.py
git commit -m "fix: classify traveloka redirects safely"
```

Expected: commit includes only Traveloka adapter and adapter tests.

---

### Task 4: Outcome Gate For Full Replayer Implementation

**Files:**
- Read: `docs/superpowers/specs/2026-05-14-cheapy-traveloka-discovered-api-contract.md`
- Create when replayable: `docs/superpowers/plans/2026-05-14-cheapy-traveloka-client-api-replayer-phase-2.md`

- [ ] **Step 1: Read the committed discovery artifact**

Run:

```bash
sed -n '1,260p' docs/superpowers/specs/2026-05-14-cheapy-traveloka-discovered-api-contract.md
```

Expected: artifact outcome is either `Replayable with HTTP-only runtime.` or `Blocked. No replayable HTTP-only client API endpoint was discovered within the approved constraints.`

- [ ] **Step 2: If replayable, stop and write the Phase 2 plan**

If the artifact says `Replayable with HTTP-only runtime.`, do not implement the runtime replayer from this plan. Use `superpowers:writing-plans` again and create:

```text
docs/superpowers/plans/2026-05-14-cheapy-traveloka-client-api-replayer-phase-2.md
```

That Phase 2 plan must include the exact discovered endpoint method, URL path, request shape, headers, fixtures, normalizer paths, and tests. It must contain concrete values only.

Expected: Phase 2 plan is committed before any runtime replayer code changes.

- [ ] **Step 3: If blocked, verify fail-fast behavior is complete**

If the artifact says `Blocked. No replayable HTTP-only client API endpoint was discovered within the approved constraints.`, run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_provider.py -v
```

Expected: PASS. Traveloka page shells, invalid JSON, redirects, and challenge hosts are structured failures, not `SUCCESS` empty.

---

### Task 5: Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused Traveloka tests**

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_provider.py -v
```

Expected: PASS.

- [ ] **Step 2: Run provider, search, and CLI tests**

Run:

```bash
uv run pytest tests/test_providers.py tests/test_package_data.py tests/test_search.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 3: Verify default provider command remains offline**

Run:

```bash
uv run cheapy providers test
```

Expected: JSON output includes `google_fli` and `traveloka` with `live_smoke="not_run"` and exits 0.

- [ ] **Step 4: Run full offline test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS with live tests skipped unless explicitly enabled.

- [ ] **Step 5: Optional live smoke only with explicit approval**

Run only if the user explicitly asks for live smoke:

```bash
CHEAPY_RUN_LIVE_TESTS=1 uv run cheapy providers test --live
```

Expected: exits 0 and returns structured provider reports. Traveloka may be `success`, `failed`, `blocked`, `timeout`, or `unsupported_response`; it must not crash the CLI.
