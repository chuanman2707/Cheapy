# Cheapy Live Search Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Cheapy live search feel reliable by running providers concurrently under a 45-second batch budget, retrying bounded retryable failures once, showing safe failure reasons, and parsing safe Skyscanner multi-segment itineraries.

**Architecture:** Keep Contract V1 unchanged. Add presentation-only safe reason helpers in `cheapy/markdown_report.py`, orchestrator-level concurrency/retry/budget logic in `cheapy/search.py`, budget-aware timeout hooks in live provider wrappers, and Skyscanner multi-segment extraction with internal leg-boundary metadata.

**Tech Stack:** Python 3.12+, asyncio, Pydantic Contract V1 models, pytest, uv, Cheapy provider protocols.

---

## Commit Policy

The repository may have unrelated dirty worktree changes. Before each commit, run:

```bash
git status --short
git diff --cached --name-status
```

Only stage files from the current task. Do not revert unrelated changes.

Recommended commit body for AI commits:

```text
AI-Generated-By: GPT-5 Codex
```

## Reference Material

- Project instructions: `AGENTS.md`
- Cheapy skill: `.codex/skills/cheapy/SKILL.md`
- Design spec: `docs/superpowers/specs/2026-05-28-cheapy-live-search-reliability-design.md`
- Contract source of truth: `cheapy/models/contracts.py`
- Provider result model: `cheapy/providers/base.py`
- Search orchestration: `cheapy/search.py`
- Markdown report: `cheapy/markdown_report.py`
- Skyscanner adapter: `cheapy/providers/skyscanner/adapter.py`
- Skyscanner normalizer: `cheapy/providers/skyscanner/normalizer.py`
- Skyscanner provider: `cheapy/providers/skyscanner/provider.py`
- Google Fli provider: `cheapy/providers/google_fli/provider.py`
- Traveloka provider: `cheapy/providers/traveloka/provider.py`

## Scope Check

This is one reliability/UX feature across three existing surfaces. Do not add
Contract V1 fields, storage schema changes, Browserless, live test calls, or raw
provider URL passthrough. Public links remain attached only through
`attach_public_search_urls()`.

## File Structure

Modify:

- `cheapy/markdown_report.py`: derive and render allowlisted safe failure reasons.
- `tests/test_markdown_report.py`: prove safe reasons render while sensitive messages remain redacted.
- `cheapy/search.py`: concurrent provider dispatch, bounded retry, 45-second batch budget, deterministic result ordering.
- `tests/test_search.py`: fake-provider tests for concurrency, retry, timeout, deterministic ordering, and logical accounting.
- `cheapy/providers/google_fli/provider.py`: expose an internal timeout clone hook for orchestrator budget use.
- `cheapy/providers/skyscanner/provider.py`: expose an internal timeout clone hook.
- `cheapy/providers/traveloka/provider.py`: expose an internal timeout clone hook.
- `cheapy/providers/skyscanner/adapter.py`: parse multi-segment provider legs and preserve internal outbound/inbound grouping.
- `cheapy/providers/skyscanner/normalizer.py`: derive round-trip return date from preserved inbound grouping.
- `tests/skyscanner/test_adapter.py`: fake payload tests for multi-segment extraction and malformed skips.
- `tests/skyscanner/test_normalizer.py`: Contract V1 tests for multi-segment round trips.
- `tests/skyscanner/test_provider.py`: provider wrapper regression tests for sanitized output.

## Task 0: Preflight

**Files:**
- Read: `AGENTS.md`
- Read: `.codex/skills/cheapy/SKILL.md`
- Read: `docs/superpowers/specs/2026-05-28-cheapy-live-search-reliability-design.md`

- [ ] **Step 1: Confirm branch and working tree**

Run:

```bash
git branch --show-current
git status --short
```

Expected: branch is `codex/local-sqlite-history-watchlist`. Record any dirty files. Do not revert unrelated work.

- [ ] **Step 2: Run focused baseline tests**

Run:

```bash
uv run pytest tests/test_search.py tests/test_markdown_report.py tests/skyscanner/test_adapter.py tests/skyscanner/test_normalizer.py tests/skyscanner/test_provider.py -v
```

Expected: PASS before edits. If a baseline test fails, record the failing test and failure message before changing code.

## Task 1: Safe Failure Reasons In Markdown

**Files:**
- Modify: `tests/test_markdown_report.py`
- Modify: `cheapy/markdown_report.py`

- [ ] **Step 1: Add failing tests for safe reasons**

Append tests to `tests/test_markdown_report.py`:

```python
def test_sensitive_provider_message_keeps_safe_failure_reason() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_TIMEOUT,
        severity=Severity.ERROR,
        message_en="Skyscanner request included cookie and session data.",
        details={
            "provider": "skyscanner",
            "capability": "exact_round_trip",
            "failure_type": "timeout",
            "cookie": "secret-cookie",
        },
        retryable=True,
    )
    response = _response(
        errors=[error],
        provider_statuses=[
            _provider_status(
                provider_name="skyscanner",
                status=ProviderStatusCode.FAILED,
                succeeded_call_count=0,
                failed_call_count=1,
                errors=[error],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "[redacted] (reason: timeout)" in report
    assert "cookie" not in report.lower()
    assert "secret-cookie" not in report
    assert "| Skyscanner | failed | 1/1 | failed: 1; retryable: yes; error provider_timeout: [redacted] (reason: timeout) retryable: yes |" in report


def test_provider_reason_can_come_from_safe_http_status_code() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_BLOCKED,
        severity=Severity.ERROR,
        message_en="Provider blocked the request at a challenge URL.",
        details={
            "provider": "skyscanner",
            "capability": "exact_one_way",
            "http_status_code": 403,
        },
        retryable=False,
    )

    report = render_search_report(
        _request(),
        _response(errors=[error], provider_statuses=[_provider_status(errors=[error])]),
    )

    assert "[redacted] (reason: provider_blocked)" in report
    assert "challenge URL" not in report
    assert "http_status_code" not in report
    assert "403" not in report


def test_unsafe_failure_type_is_not_rendered_as_reason() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Provider failed at https://example.test/challenge?token=secret",
        details={
            "provider": "skyscanner",
            "capability": "exact_one_way",
            "failure_type": "token_session_header_dump",
        },
        retryable=False,
    )

    report = render_search_report(
        _request(),
        _response(errors=[error], provider_statuses=[_provider_status(errors=[error])]),
    )

    assert "[redacted]" in report
    assert "token_session_header_dump" not in report
    assert "https://example.test" not in report
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_markdown_report.py::test_sensitive_provider_message_keeps_safe_failure_reason tests/test_markdown_report.py::test_provider_reason_can_come_from_safe_http_status_code tests/test_markdown_report.py::test_unsafe_failure_type_is_not_rendered_as_reason -v
```

Expected: FAIL because the report currently renders only sanitized `message_en`.

- [ ] **Step 3: Implement safe reason helpers**

In `cheapy/markdown_report.py`, add module constants near the sensitive message constants:

```python
_SAFE_FAILURE_REASONS = frozenset(
    {
        "timeout",
        "provider_blocked",
        "blocked",
        "rate_limited",
        "parse_failed",
        "parse_error",
        "no_usable_results",
        "missing_cookie",
        "transport_error",
        "unsupported_passengers",
        "http_error",
        "invalid_argument",
        "entity_not_found",
        "entity_ambiguous",
        "unexpected_error",
        "no_usable_outbound_data",
        "unsupported_response",
        "return_capture_timeout",
        "final_round_trip_total_unavailable",
        "outbound_selection_unavailable",
        "outbound_selection_transition_unavailable",
        "return_selection_unavailable",
        "selected_outbound_binding_unavailable",
        "selected_return_binding_unavailable",
        "partial_failure",
    }
)
_TIMEOUT_EXCEPTION_TYPES = frozenset(
    {
        "TimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "TimeoutException",
    }
)
```

Replace `_safe_message(message.message_en)` calls in `_message_row()` and `_provider_message_note()` with a new helper:

```python
def _safe_message_with_reason(message: WarningV1 | ErrorV1) -> str:
    rendered = _safe_message(message.message_en)
    reason = _safe_reason(message)
    if reason is None:
        return rendered
    return f"{rendered} (reason: {reason})"
```

Add:

```python
def _safe_reason(message: WarningV1 | ErrorV1) -> str | None:
    details = message.details
    failure_type = details.get("failure_type")
    if isinstance(failure_type, str):
        normalized = failure_type.strip().lower()
        if normalized in _SAFE_FAILURE_REASONS:
            if normalized == "blocked":
                return "provider_blocked"
            if normalized == "parse_error":
                return "parse_failed"
            return normalized

    status_code = details.get("http_status_code")
    if isinstance(status_code, int):
        if status_code in {401, 403}:
            return "provider_blocked"
        if status_code == 429:
            return "rate_limited"
        if status_code >= 500:
            return "transport_error"

    exception_type = details.get("exception_type")
    if isinstance(exception_type, str) and exception_type in _TIMEOUT_EXCEPTION_TYPES:
        return "timeout"
    return None
```

Update `_message_row()`:

```python
        _safe_message_with_reason(message),
```

Update `_provider_message_note()`:

```python
        f"{kind} {message.code.value}: {_safe_message_with_reason(message)} "
```

- [ ] **Step 4: Run focused Markdown tests**

Run:

```bash
uv run pytest tests/test_markdown_report.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add tests/test_markdown_report.py cheapy/markdown_report.py
git diff --cached --name-status
git commit -m "feat: show safe provider failure reasons" -m "AI-Generated-By: GPT-5 Codex"
```

## Task 2: Concurrent Provider Calls, Budget, And Retry

**Files:**
- Modify: `tests/test_search.py`
- Modify: `cheapy/search.py`
- Modify: `cheapy/providers/google_fli/provider.py`
- Modify: `cheapy/providers/skyscanner/provider.py`
- Modify: `cheapy/providers/traveloka/provider.py`

- [ ] **Step 1: Add fake provider helpers and failing orchestration tests**

In `tests/test_search.py`, add imports:

```python
import asyncio
from time import perf_counter
```

Add helper provider classes near the existing provider test doubles:

```python
class _AsyncSequencedProvider:
    capabilities = ("exact_one_way",)

    def __init__(
        self,
        name: str,
        results: list[ProviderResult],
        *,
        delay_seconds: float = 0.0,
        events: list[str] | None = None,
    ) -> None:
        self.name = name
        self._results = list(results)
        self._delay_seconds = delay_seconds
        self.calls = 0
        self.events = events if events is not None else []

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        self.calls += 1
        self.events.append(f"{self.name}:start:{self.calls}")
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        self.events.append(f"{self.name}:finish:{self.calls}")
        if not self._results:
            raise AssertionError(f"{self.name} was called too many times")
        return self._results.pop(0)

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        raise AssertionError("round-trip should not be called")


class _AsyncNeverFinishesProvider:
    name = "never_finishes"
    capabilities = ("exact_one_way",)

    async def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> ProviderResult:
        await asyncio.sleep(60)
        raise AssertionError("budget should cancel this provider")

    async def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> ProviderResult:
        raise AssertionError("round-trip should not be called")
```

Add helpers:

```python
def _provider_success_result(provider_name: str, offer_id: str) -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        capability="exact_one_way",
        status=ProviderStatusCode.SUCCESS,
        offers=[
            _offer(
                offer_id=offer_id,
                provider=provider_name,
                currency="USD",
                price_amount=100.0,
            )
        ],
        warnings=[],
        errors=[],
        duration_ms=1,
        retryable=False,
    )


def _provider_failed_result_for_test(
    provider_name: str,
    *,
    retryable: bool,
    code: ErrorCode = ErrorCode.PROVIDER_TIMEOUT,
    failure_type: str = "timeout",
) -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        capability="exact_one_way",
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[
            ErrorV1(
                code=code,
                severity=Severity.ERROR,
                message_en=f"{provider_name} failed safely.",
                details={
                    "provider": provider_name,
                    "capability": "exact_one_way",
                    "failure_type": failure_type,
                },
                retryable=retryable,
            )
        ],
        duration_ms=1,
        retryable=retryable,
    )
```

Append tests:

```python
def test_search_exact_calls_providers_concurrently_and_keeps_status_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    providers = [
        _AsyncSequencedProvider(
            "slow_a",
            [_provider_success_result("slow_a", "slow-a:1")],
            delay_seconds=0.05,
            events=events,
        ),
        _AsyncSequencedProvider(
            "slow_b",
            [_provider_success_result("slow_b", "slow-b:1")],
            delay_seconds=0.05,
            events=events,
        ),
    ]
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: providers)

    started = perf_counter()
    response = search_exact(_request())
    elapsed = perf_counter() - started

    assert elapsed < 0.09
    assert events[:2] == ["slow_a:start:1", "slow_b:start:1"]
    assert [status.provider_name for status in response.provider_statuses] == [
        "slow_a",
        "slow_b",
    ]


def test_search_exact_retries_retryable_failure_once_and_keeps_logical_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _AsyncSequencedProvider(
        "retry_provider",
        [
            _provider_failed_result_for_test("retry_provider", retryable=True),
            _provider_success_result("retry_provider", "retry-provider:1"),
        ],
    )
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(_request())

    assert response.status == SearchStatus.SUCCESS
    assert [offer.offer_id for offer in response.offers] == ["retry-provider:1"]
    assert response.errors == []
    status = response.provider_statuses[0]
    assert status.provider_name == "retry_provider"
    assert status.planned_call_count == 1
    assert status.executed_call_count == 1
    assert status.succeeded_call_count == 1
    assert status.failed_call_count == 0


def test_search_exact_retry_exhausted_returns_final_failed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _AsyncSequencedProvider(
        "retry_provider",
        [
            _provider_failed_result_for_test("retry_provider", retryable=True),
            _provider_failed_result_for_test("retry_provider", retryable=True),
        ],
    )
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert response.provider_statuses[0].failed_call_count == 1
    assert response.provider_statuses[0].planned_call_count == 1
    assert response.provider_statuses[0].executed_call_count == 1
    assert response.errors[0].details["failure_type"] == "timeout"


def test_search_exact_does_not_retry_non_retryable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _AsyncSequencedProvider(
        "blocked_provider",
        [
            _provider_failed_result_for_test(
                "blocked_provider",
                retryable=False,
                code=ErrorCode.PROVIDER_BLOCKED,
                failure_type="blocked",
            ),
            _provider_success_result("blocked_provider", "blocked-provider:1"),
        ],
    )
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [provider])

    response = search_exact(_request())

    assert response.status == SearchStatus.FAILED
    assert provider.calls == 1
    assert response.errors[0].code == ErrorCode.PROVIDER_BLOCKED


def test_search_exact_global_budget_returns_timeout_without_losing_completed_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cheapy.search._PROVIDER_BATCH_TIMEOUT_SECONDS", 0.02)
    success = _AsyncSequencedProvider(
        "success_provider",
        [_provider_success_result("success_provider", "success-provider:1")],
    )
    slow = _AsyncNeverFinishesProvider()
    monkeypatch.setattr("cheapy.search.load_search_providers", lambda: [success, slow])

    response = search_exact(_request())

    assert response.status == SearchStatus.PARTIAL
    assert [offer.offer_id for offer in response.offers] == ["success-provider:1"]
    statuses = {status.provider_name: status for status in response.provider_statuses}
    assert statuses["never_finishes"].status == ProviderStatusCode.FAILED
    assert statuses["never_finishes"].errors[0].code == ErrorCode.PROVIDER_TIMEOUT
    assert statuses["never_finishes"].errors[0].details["failure_type"] == "timeout"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_search.py::test_search_exact_calls_providers_concurrently_and_keeps_status_order tests/test_search.py::test_search_exact_retries_retryable_failure_once_and_keeps_logical_accounting tests/test_search.py::test_search_exact_retry_exhausted_returns_final_failed_result tests/test_search.py::test_search_exact_does_not_retry_non_retryable_failure tests/test_search.py::test_search_exact_global_budget_returns_timeout_without_losing_completed_results -v
```

Expected: FAIL because orchestration is sequential and has no retry/batch budget.

- [ ] **Step 3: Implement orchestrator budget helpers**

In `cheapy/search.py`, add imports:

```python
from time import perf_counter
```

Add constants after `_MIXED_CURRENCY_NOTE`:

```python
_PROVIDER_BATCH_TIMEOUT_SECONDS = 45.0
_MIN_RETRY_REMAINING_SECONDS = 0.001
```

Replace `_call_planned_providers()` with a gather-based implementation:

```python
async def _call_planned_providers(
    planned_calls: tuple[PlannedProviderCall, ...],
    passengers: PassengersV1,
) -> list[ProviderResult]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _PROVIDER_BATCH_TIMEOUT_SECONDS
    tasks = [
        asyncio.create_task(_call_planned_provider_with_retry(planned_call, passengers, deadline))
        for planned_call in planned_calls
    ]
    return list(await asyncio.gather(*tasks))
```

Add:

```python
async def _call_planned_provider_with_retry(
    planned_call: PlannedProviderCall,
    passengers: PassengersV1,
    deadline: float,
) -> ProviderResult:
    started = perf_counter()
    first = await _call_planned_provider_attempt(planned_call, passengers, deadline)
    if not _should_retry_provider_result(first):
        return _with_logical_duration(first, started)
    if _remaining_seconds(deadline) <= _MIN_RETRY_REMAINING_SECONDS:
        return _with_logical_duration(first, started)
    second = await _call_planned_provider_attempt(planned_call, passengers, deadline)
    return _with_logical_duration(second, started)


async def _call_planned_provider_attempt(
    planned_call: PlannedProviderCall,
    passengers: PassengersV1,
    deadline: float,
) -> ProviderResult:
    provider = planned_call.provider
    candidate = planned_call.candidate
    remaining = _remaining_seconds(deadline)
    if remaining <= 0:
        return _provider_timeout_result(provider, candidate.capability)
    budgeted_provider = _provider_with_timeout(provider, remaining)
    try:
        raw_result = await asyncio.wait_for(
            _invoke_provider(budgeted_provider, candidate, passengers),
            timeout=remaining,
        )
        return _normalize_provider_result(budgeted_provider, candidate.capability, raw_result)
    except TimeoutError:
        return _provider_timeout_result(provider, candidate.capability)
    except Exception as exc:
        return _provider_exception_result(provider, candidate.capability, exc)


async def _invoke_provider(
    provider: FlightProvider,
    candidate: SearchCandidate,
    passengers: PassengersV1,
) -> object:
    if candidate.capability == EXACT_ONE_WAY_CAPABILITY:
        return await provider.search_exact_one_way(
            _one_way_provider_request(candidate, passengers)
        )
    return await provider.search_exact_round_trip(
        _round_trip_provider_request(candidate, passengers)
    )


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


def _provider_with_timeout(provider: FlightProvider, timeout_seconds: float) -> FlightProvider:
    with_timeout = getattr(provider, "with_timeout_seconds", None)
    if callable(with_timeout):
        try:
            return with_timeout(max(_MIN_RETRY_REMAINING_SECONDS, timeout_seconds))
        except Exception:
            return provider
    return provider


def _should_retry_provider_result(result: ProviderResult) -> bool:
    if result.status != ProviderStatusCode.FAILED:
        return False
    return result.retryable or any(error.retryable for error in result.errors)


def _with_logical_duration(result: ProviderResult, started: float) -> ProviderResult:
    return result.model_copy(update={"duration_ms": max(0, round((perf_counter() - started) * 1000))})
```

Remove the old sequential loop body from `_call_planned_providers()`.

- [ ] **Step 4: Add timeout failed result helper**

In `cheapy/search.py`, update `_provider_failed_result()` signature to accept optional `code`, `retryable`, and `duration_ms` defaults:

```python
def _provider_failed_result(
    *,
    provider_name: str,
    capability: str,
    message_en: str,
    details: dict[str, object],
    code: ErrorCode = ErrorCode.PROVIDER_FAILED,
    retryable: bool = False,
    duration_ms: int = 0,
) -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        capability=capability,
        status=ProviderStatusCode.FAILED,
        offers=[],
        warnings=[],
        errors=[
            _error(
                code=code,
                message_en=message_en,
                details=details,
                retryable=retryable,
            )
        ],
        duration_ms=duration_ms,
        retryable=retryable,
    )
```

Add:

```python
def _provider_timeout_result(
    provider: FlightProvider,
    capability: str,
) -> ProviderResult:
    return _provider_failed_result(
        provider_name=provider.name,
        capability=capability,
        message_en=f"{_provider_display_name(provider.name)} provider timed out.",
        details={
            "provider": provider.name,
            "capability": capability,
            "failure_type": "timeout",
        },
        code=ErrorCode.PROVIDER_TIMEOUT,
        retryable=True,
    )


def _provider_display_name(provider_name: str) -> str:
    return provider_name.replace("_", " ").title()
```

- [ ] **Step 5: Add timeout clone hooks to built-in providers**

In each provider class, add a method:

`cheapy/providers/google_fli/provider.py`:

```python
    def with_timeout_seconds(self, timeout_seconds: float) -> "GoogleFliProvider":
        return GoogleFliProvider(
            adapter=self._adapter,
            timeout_seconds=max(0.001, timeout_seconds),
        )
```

`cheapy/providers/skyscanner/provider.py`:

```python
    def with_timeout_seconds(self, timeout_seconds: float) -> "SkyscannerProvider":
        return SkyscannerProvider(
            adapter=self._adapter,
            timeout_seconds=max(0.001, timeout_seconds),
            env=self._env,
        )
```

`cheapy/providers/traveloka/provider.py`:

```python
    def with_timeout_seconds(self, timeout_seconds: float) -> "TravelokaProvider":
        return TravelokaProvider(
            adapter=self._adapter,
            timeout_seconds=max(0.001, timeout_seconds),
        )
```

If review shows Traveloka or Skyscanner injected adapters cannot honor the lowered timeout, keep the hook for production default providers and cover retry/budget behavior at the orchestrator level with fake async providers.

- [ ] **Step 6: Run focused search tests**

Run:

```bash
uv run pytest tests/test_search.py -v
```

Expected: PASS.

- [ ] **Step 7: Run provider wrapper tests**

Run:

```bash
uv run pytest tests/test_google_fli_provider.py tests/skyscanner/test_provider.py tests/traveloka/test_provider.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add tests/test_search.py cheapy/search.py cheapy/providers/google_fli/provider.py cheapy/providers/skyscanner/provider.py cheapy/providers/traveloka/provider.py
git diff --cached --name-status
git commit -m "feat: run providers concurrently with bounded retry" -m "AI-Generated-By: GPT-5 Codex"
```

## Task 3: Skyscanner Multi-Segment Itineraries

**Files:**
- Modify: `tests/skyscanner/test_adapter.py`
- Modify: `tests/skyscanner/test_normalizer.py`
- Modify: `tests/skyscanner/test_provider.py`
- Modify: `cheapy/providers/skyscanner/adapter.py`
- Modify: `cheapy/providers/skyscanner/normalizer.py`

- [ ] **Step 1: Replace old multi-segment skip expectation with success coverage**

In `tests/skyscanner/test_adapter.py`, replace `test_multisegment_leg_is_skipped_instead_of_misrepresented` with:

```python
def test_multisegment_leg_returns_segment_candidates() -> None:
    payload = search_payload()
    itinerary = payload["itineraries"]["results"][0]
    leg = itinerary["legs"][0]
    leg["origin"]["displayCode"] = "DUS"
    leg["destination"]["displayCode"] = "SGN"
    leg["durationInMinutes"] = 990
    leg["stopCount"] = 1
    leg["segments"] = [
        {
            "origin": {"displayCode": "DUS"},
            "destination": {"displayCode": "DOH"},
            "departure": "2026-07-11T15:25:00",
            "arrival": "2026-07-11T23:35:00",
            "durationInMinutes": 370,
            "marketingCarrier": {"displayCode": "QR", "name": "Qatar Airways"},
            "flightNumber": "86",
        },
        {
            "origin": {"displayCode": "DOH"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-07-12T02:00:00",
            "arrival": "2026-07-12T13:55:00",
            "durationInMinutes": 475,
            "marketingCarrier": {"displayCode": "QR", "name": "Qatar Airways"},
            "flightNumber": "970",
        },
    ]
    client = FakeClient(
        [
            FakeResponse(payload=entity("DUS", "95565012")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=payload),
        ]
    )

    candidates = adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
        ProviderExactOneWayRequest(
            origin="DUS",
            destination="SGN",
            departure_date="2026-07-11",
        )
    )

    assert len(candidates) == 1
    assert [(leg.origin, leg.destination, leg.flight_number) for leg in candidates[0].legs] == [
        ("DUS", "DOH", "QR86"),
        ("DOH", "SGN", "QR970"),
    ]
    assert candidates[0].total_duration_minutes == 990
    assert candidates[0].stops == 1
    assert_no_sensitive_tokens(candidates)
```

Add a malformed-chain test:

```python
def test_multisegment_broken_chain_is_skipped_as_no_usable_results() -> None:
    payload = search_payload()
    leg = payload["itineraries"]["results"][0]["legs"][0]
    leg["origin"]["displayCode"] = "DUS"
    leg["destination"]["displayCode"] = "SGN"
    leg["stopCount"] = 1
    leg["segments"] = [
        {
            "origin": {"displayCode": "DUS"},
            "destination": {"displayCode": "DOH"},
            "departure": "2026-07-11T15:25:00",
            "arrival": "2026-07-11T23:35:00",
            "durationInMinutes": 370,
            "marketingCarrier": {"displayCode": "QR"},
            "flightNumber": "86",
        },
        {
            "origin": {"displayCode": "DXB"},
            "destination": {"displayCode": "SGN"},
            "departure": "2026-07-12T02:00:00",
            "arrival": "2026-07-12T13:55:00",
            "durationInMinutes": 475,
            "marketingCarrier": {"displayCode": "QR"},
            "flightNumber": "970",
        },
    ]
    client = FakeClient(
        [
            FakeResponse(payload=entity("DUS", "95565012")),
            FakeResponse(payload=entity("SGN", "95673379")),
            FakeResponse(payload=payload),
        ]
    )

    with pytest.raises(adapter.SkyscannerProviderError) as exc_info:
        adapter.SkyscannerAdapter(config=config(), client=client).search_exact_one_way(
            ProviderExactOneWayRequest(
                origin="DUS",
                destination="SGN",
                departure_date="2026-07-11",
            )
        )

    assert exc_info.value.failure_type == "no_usable_results"
    assert_error_is_sanitized(exc_info.value)
```

- [ ] **Step 2: Add round-trip normalizer coverage**

In `tests/skyscanner/test_normalizer.py`, add:

```python
def test_normalize_round_trip_multisegment_sets_return_date_from_inbound_group() -> None:
    outbound_1 = _leg(
        origin="DUS",
        destination="DOH",
        departure_time="2026-07-11T15:25:00",
        arrival_time="2026-07-11T23:35:00",
        airline_code="QR",
        flight_number="QR86",
        duration_minutes=370,
    )
    outbound_2 = _leg(
        origin="DOH",
        destination="SGN",
        departure_time="2026-07-12T02:00:00",
        arrival_time="2026-07-12T13:55:00",
        airline_code="QR",
        flight_number="QR970",
        duration_minutes=475,
    )
    inbound_1 = _leg(
        origin="SGN",
        destination="DOH",
        departure_time="2026-08-14T20:00:00",
        arrival_time="2026-08-15T00:15:00",
        airline_code="QR",
        flight_number="QR971",
        duration_minutes=435,
    )
    inbound_2 = _leg(
        origin="DOH",
        destination="DUS",
        departure_time="2026-08-15T02:30:00",
        arrival_time="2026-08-15T07:50:00",
        airline_code="QR",
        flight_number="QR85",
        duration_minutes=380,
    )
    request = ProviderExactRoundTripRequest(
        origin="DUS",
        destination="SGN",
        departure_date="2026-07-11",
        return_date="2026-08-14",
    )
    candidate = SkyscannerItineraryCandidate(
        item_id="longhaul-1",
        price_amount=920.0,
        currency="SGD",
        legs=(outbound_1, outbound_2, inbound_1, inbound_2),
        total_duration_minutes=1980,
        stops=2,
        outbound_leg_count=2,
    )

    offers, errors = normalize_candidates([candidate], request)

    assert errors == []
    offer = offers[0]
    assert offer.actual_origin == "DUS"
    assert offer.actual_destination == "SGN"
    assert offer.actual_departure_date == "2026-07-11"
    assert offer.actual_return_date == "2026-08-14"
    assert offer.return_offset_days == 0
    assert [leg.flight_number for leg in offer.legs] == [
        "QR86",
        "QR970",
        "QR971",
        "QR85",
    ]
    assert offer.total_duration_minutes == 1980
    assert offer.stops == 2
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
uv run pytest tests/skyscanner/test_adapter.py::test_multisegment_leg_returns_segment_candidates tests/skyscanner/test_adapter.py::test_multisegment_broken_chain_is_skipped_as_no_usable_results tests/skyscanner/test_normalizer.py::test_normalize_round_trip_multisegment_sets_return_date_from_inbound_group -v
```

Expected: FAIL because adapter still rejects multi-segment legs and the candidate dataclass lacks `outbound_leg_count`.

- [ ] **Step 4: Update Skyscanner candidate dataclass**

In `cheapy/providers/skyscanner/adapter.py`, extend `SkyscannerItineraryCandidate`:

```python
@dataclass(frozen=True)
class SkyscannerItineraryCandidate:
    item_id: str
    price_amount: float
    currency: str
    legs: tuple[SkyscannerLegCandidate, ...]
    total_duration_minutes: int
    stops: int
    outbound_leg_count: int | None = None
```

In `tests/skyscanner/test_normalizer.py` and `tests/skyscanner/test_provider.py` helper candidates, no change is needed because the new field has a default.

- [ ] **Step 5: Implement multi-segment extraction**

In `cheapy/providers/skyscanner/adapter.py`, replace `_candidate_leg()` with helpers that return a tuple:

```python
def _candidate_leg_segments(leg: object) -> tuple[SkyscannerLegCandidate, ...] | None:
    if not isinstance(leg, dict):
        return None
    origin = _as_str(_field(leg, ("origin.displayCode",)))
    destination = _as_str(_field(leg, ("destination.displayCode",)))
    segments = leg.get("segments")
    if origin is None or destination is None or not isinstance(segments, list) or not segments:
        return None

    candidates = tuple(
        segment_candidate
        for segment in segments
        if (segment_candidate := _candidate_segment(segment)) is not None
    )
    if len(candidates) != len(segments):
        return None
    if candidates[0].origin != origin or candidates[-1].destination != destination:
        return None
    for current, next_segment in zip(candidates, candidates[1:], strict=False):
        if current.destination != next_segment.origin:
            return None
    return candidates


def _candidate_segment(segment: object) -> SkyscannerLegCandidate | None:
    if not isinstance(segment, dict):
        return None
    origin = _as_str(_field(segment, ("origin.displayCode",)))
    destination = _as_str(_field(segment, ("destination.displayCode",)))
    departure = _as_str(_field(segment, ("departure",)))
    arrival = _as_str(_field(segment, ("arrival",)))
    duration = _int_value(_field(segment, ("durationInMinutes",)))
    if origin is None or destination is None or departure is None or arrival is None or duration is None:
        return None
    flight = _segment_flight_number(segment)
    if flight is None:
        return None
    airline_code, flight_number = flight
    if not airline_code or not flight_number:
        return None
    return SkyscannerLegCandidate(
        origin=origin,
        destination=destination,
        departure_time=departure,
        arrival_time=arrival,
        airline_code=airline_code,
        flight_number=flight_number,
        duration_minutes=duration,
    )
```

Update `_extract_candidate()` to collect provider leg groups:

```python
    leg_groups = tuple(
        group
        for leg in legs_payload
        if (group := _candidate_leg_segments(leg)) is not None
    )
    if not leg_groups or len(leg_groups) != len(legs_payload) or len(leg_groups) != len(expected_routes):
        return None
    for group, (expected_origin, expected_destination) in zip(
        leg_groups,
        expected_routes,
        strict=True,
    ):
        if group[0].origin != expected_origin or group[-1].destination != expected_destination:
            return None
    legs = tuple(segment for group in leg_groups for segment in group)
```

Compute duration and stops:

```python
    leg_durations = [
        duration
        for leg in legs_payload
        if (duration := _int_value(_field(leg, ("durationInMinutes",)))) is not None and duration >= 0
    ]
    total_duration = (
        sum(leg_durations)
        if len(leg_durations) == len(legs_payload)
        else sum(leg.duration_minutes for leg in legs)
    )
    stop_count = 0
    for leg in legs_payload:
        stops = _int_value(_field(leg, ("stopCount",)))
        if stops is None or stops < 0:
            stop_count += max(0, len(_candidate_leg_segments(leg) or ()) - 1)
        else:
            stop_count += stops
```

Set `outbound_leg_count` when returning the candidate:

```python
        outbound_leg_count=len(leg_groups[0]) if len(expected_routes) == 2 else None,
```

- [ ] **Step 6: Update normalizer return-date logic**

In `cheapy/providers/skyscanner/normalizer.py`, replace `_actual_return_date()` with:

```python
def _actual_return_date(
    candidate: SkyscannerItineraryCandidate,
    legs: list[FlightLegV1],
    request: ProviderRequest,
) -> str | None:
    if not isinstance(request, ProviderExactRoundTripRequest):
        return None
    if candidate.outbound_leg_count is not None:
        if candidate.outbound_leg_count < len(legs):
            return legs[candidate.outbound_leg_count].departure_time[:10]
        return None
    for leg in legs:
        if leg.origin == request.destination and leg.destination == request.origin:
            return leg.departure_time[:10]
    return None
```

Update the call site:

```python
    actual_return_date = _actual_return_date(candidate, legs, request)
```

- [ ] **Step 7: Run focused Skyscanner tests**

Run:

```bash
uv run pytest tests/skyscanner/test_adapter.py tests/skyscanner/test_normalizer.py tests/skyscanner/test_provider.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add tests/skyscanner/test_adapter.py tests/skyscanner/test_normalizer.py tests/skyscanner/test_provider.py cheapy/providers/skyscanner/adapter.py cheapy/providers/skyscanner/normalizer.py
git diff --cached --name-status
git commit -m "feat: parse skyscanner multi-segment itineraries" -m "AI-Generated-By: GPT-5 Codex"
```

## Task 4: Final Verification And Review

**Files:**
- Read: all changed files from Tasks 1-3

- [ ] **Step 1: Run focused test matrix**

Run:

```bash
uv run pytest tests/test_search.py tests/test_markdown_report.py tests/skyscanner/test_adapter.py tests/skyscanner/test_normalizer.py tests/skyscanner/test_provider.py tests/test_mcp.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS. If a test fails, fix the root cause before proceeding.

- [ ] **Step 3: Safety scans**

Run:

```bash
rg -n "Browserless|browserless|transport_deeplink|__Secure-anon_token|secret-cookie|request_body|raw_payload|sessionId" cheapy tests
```

Expected: only denylist constants, test fixtures, and safety assertions. No production output path should expose these tokens.

- [ ] **Step 4: Run package smoke**

Run:

```bash
uv run cheapy --version
```

Expected: prints the package version and exits 0.

- [ ] **Step 5: Final diff review**

Run:

```bash
git status --short
git log --oneline -6
git diff HEAD~3..HEAD --stat
```

Expected: only intended files changed. No unrelated dirty files are staged.

- [ ] **Step 6: Commit verification fixes if needed**

If Step 1-5 required fixes, commit them:

```bash
git add tests/test_search.py tests/test_markdown_report.py tests/skyscanner/test_adapter.py tests/skyscanner/test_normalizer.py tests/skyscanner/test_provider.py tests/test_mcp.py tests/test_cli.py cheapy/search.py cheapy/markdown_report.py cheapy/providers/google_fli/provider.py cheapy/providers/skyscanner/provider.py cheapy/providers/traveloka/provider.py cheapy/providers/skyscanner/adapter.py cheapy/providers/skyscanner/normalizer.py
git diff --cached --name-status
git commit -m "test: verify live search reliability" -m "AI-Generated-By: GPT-5 Codex"
```

If no fixes were needed, do not create an empty commit.
