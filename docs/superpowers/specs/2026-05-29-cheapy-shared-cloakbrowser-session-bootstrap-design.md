# Cheapy Shared Cloakbrowser Session Bootstrap Design

Date: 2026-05-29

## Summary

Add a shared local Cloakbrowser bootstrap layer for live providers that need
browser-created session material.

Skyscanner will use the shared layer to collect an in-memory cookie header and
user agent, then continue using the existing curl-backed HTTP adapter. The
normal live path must no longer require `CHEAPY_SKYSCANNER_COOKIE`, although the
environment cookie remains an explicit operator override.

Traveloka will use the shared layer in a replay-from-harvest flow. A fresh
Cloakbrowser session opens the Traveloka full-search page, captures first-party
`/api/v2/flight/search/initial` and `/api/v2/flight/search/poll` request and
response material in memory, then Cheapy attempts to replay the harvested
request through an injectable HTTP client. If replay returns a supported JSON
payload, the provider normalizes that replay payload. If replay cannot be used
safely, the provider falls back to the browser-captured JSON payload from the
same provider call.

No Contract V1 shape changes are required. No Browserless path is reintroduced.
No cookies, headers, request bodies, raw provider payloads, challenge URLs, raw
session URLs, or browser session data are persisted or exposed.

## Approved Decisions

1. Implement a shared `cheapy.browser_bootstrap` package as the only runtime
   package path that imports `cloakbrowser`.
2. Keep default tests offline with fake browser objects and fake HTTP clients.
3. Keep all browser-derived session material in RAM only.
4. Do not store browser state in env vars, files, SQLite, reports, stdout,
   stderr, Contract V1 details, or test snapshots.
5. Keep `CHEAPY_SKYSCANNER_COOKIE` and `CHEAPY_SKYSCANNER_USER_AGENT` as
   explicit Skyscanner overrides, but make Cloakbrowser bootstrap the normal
   missing-cookie path.
6. Use a provider-instance in-memory TTL cache for Skyscanner browser sessions.
7. Allow Skyscanner to force-refresh the cached session after safe failure
   classes such as `blocked`, `rate_limited`, or `no_usable_results`, only while
   the caller budget still has time.
8. Use Traveloka option 3: browser harvest followed by HTTP replay in the same
   provider call.
9. Traveloka HTTP replay must be covered by fake HTTP tests. If replay fails or
   the harvested material is incomplete, the provider may use the valid
   browser-captured payload from that same call.
10. Keep Traveloka's browser selection/final-total workflow available for
    round-trip cases where search API replay does not provide enough final
    total semantics.
11. Do not reintroduce Browserless, hosted unblock APIs, captcha solving, proxy
    rotation, login, identity rotation, or persistent browser profiles.
12. Keep `public_search_url` generation only in the public link layer.

## Goals

- Provide one shared local browser bootstrap boundary for current live
  providers.
- Make Skyscanner normal live searches bootstrap cookie and user-agent state
  without a required env cookie.
- Preserve Skyscanner's existing curl adapter, parser hardening, public-link
  safety, and Contract V1 output.
- Let Traveloka attempt a real HTTP replay using request material harvested from
  the browser in the same provider call.
- Preserve Traveloka's existing browser-network-capture fallback when replay is
  not safe or complete.
- Keep provider execution inside the existing orchestrator 45 second shared
  provider budget and one-retry behavior.
- Keep MCP stdout protocol-clean.

## Non-Goals

- No Contract V1 schema changes.
- No storage schema changes.
- No live network calls in default tests.
- No Browserless runtime code, dependency path, endpoint, token, or fallback.
- No raw Traveloka or Skyscanner internal URLs in Contract V1 output.
- No provider-specific browser logic inside the shared bootstrap package.
- No attempt to solve captcha or bypass anti-abuse systems.
- No durable cookie, token, header, request body, or browser profile cache.

## Architecture

Add a provider-neutral package:

```text
cheapy/browser_bootstrap/
  __init__.py
  cloak.py
  cookies.py
  errors.py
  types.py
```

`cloak.py` is the only module that imports `cloakbrowser`. It exposes
injectable synchronous primitives:

```python
def launch_browser(**kwargs: object) -> object: ...

def bootstrap_cookies(
    *,
    page_url: str,
    deadline_monotonic: float,
    wait_until: str = "domcontentloaded",
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserBootstrapSession: ...

def capture_first_party_requests(
    *,
    page_url: str,
    deadline_monotonic: float,
    request_predicate: RequestPredicate,
    response_predicate: ResponsePredicate | None = None,
    wait_until: str = "domcontentloaded",
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserNetworkCapture: ...
```

The shared package knows how to launch a browser, create a fresh context/page,
register listeners before navigation, evaluate `navigator.userAgent`, serialize
browser cookies into a `Cookie` header, capture selected first-party request and
response metadata, and close resources in `finally` blocks.

The shared package does not know about flight searches, Contract V1, provider
capabilities, Skyscanner endpoints, Traveloka request bodies, public links, or
normalization.

## Shared Types

Shared types are dataclasses with sensitive fields marked `repr=False`:

```python
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

Provider code may add narrower provider-local wrappers around these types when
that improves testability, but those wrappers must keep the same sensitive data
policy.

Request and response correlation is sequence-based. A provider must choose a
single `CapturedExchange` for replay and fallback. It must not replay one
captured request while falling back to an unrelated response from a different
sequence.

## Error Handling

The shared package raises neutral bootstrap errors only:

- `BrowserBootstrapUnavailable`
- `BrowserBootstrapTimeout`
- `BrowserBootstrapBlocked`
- `BrowserBootstrapCookieUnavailable`
- `BrowserNetworkCaptureUnavailable`

Each error carries a safe context with only:

- `failure_type`
- `phase`
- `http_status_code`
- `exception_type`

Allowed phases include `launch`, `context_page_setup`, `navigation`,
`capture_wait`, `cookie_read`, `user_agent_read`, and `cleanup`.

The shared error context must not include full URLs, query strings, cookies,
headers, request bodies, tokens, response bodies, provider payloads, challenge
URLs, browser session IDs, raw exception messages, or environment values.

Provider mapping rules:

- bootstrap unavailable maps to provider `failure_type="browser_bootstrap_failed"`
  and retryable true.
- bootstrap timeout maps to `failure_type="timeout"` and retryable true.
- cookie unavailable maps to `failure_type="browser_cookie_unavailable"` and
  retryable true.
- HTTP 401 or 403 challenge evidence maps to provider `blocked`.
- HTTP 429 maps to provider `rate_limited`.
- missing Traveloka captured request material maps to provider-specific
  `network_capture_unavailable` unless a valid browser-captured response is
  available as fallback.

## Skyscanner Integration

Skyscanner adds a small provider-local session manager. It owns:

- env override detection
- bootstrap URL construction
- in-memory TTL cache
- forced refresh policy
- conversion from `BrowserBootstrapSession` to `SkyscannerConfig`
- mapping shared bootstrap errors to `SkyscannerProviderError`

Normal call flow:

1. Reject unsupported children or infants before upstream work.
2. If `CHEAPY_SKYSCANNER_COOKIE` is set, build `SkyscannerConfig` from env and
   do not bootstrap. In this mode `CHEAPY_SKYSCANNER_USER_AGENT` is used as the
   env-cookie user-agent override when present.
3. Otherwise ask the provider-local session manager for a non-expired session.
4. On cache miss, call `bootstrap_cookies()` with a Skyscanner public bootstrap
   URL. If `CHEAPY_SKYSCANNER_USER_AGENT` is set without an env cookie, pass it
   as the requested bootstrap context user agent.
5. Build `SkyscannerConfig` with the returned cookie header and user agent.
6. Run the existing `SkyscannerAdapter` with the existing curl client.
7. If the adapter fails with `blocked`, `rate_limited`, or `no_usable_results`
   and the session came from cache, force one refresh and retry inside the
   current provider attempt budget.
8. Return clean candidates and normalize as today.

`public_search_url` remains `None` in provider output and is attached only by
`attach_public_search_urls()`.

## Traveloka Integration

Traveloka adds a replay-from-harvest adapter path. It owns:

- full-search URL construction
- first-party request/response predicates
- choosing the best harvested `/initial` or `/poll` request
- replaying that harvested request through an injectable HTTP client
- deciding when to use replay payload versus browser-captured payload
- preserving the existing browser workflow for round-trip selection details

Normal call flow:

1. Build the existing Traveloka `fulltwosearch` URL.
2. Call `capture_first_party_requests()` with predicates for:
   - `POST /api/v2/flight/search/initial`
   - `POST /api/v2/flight/search/poll`
3. Capture browser cookie header, user agent, matching request material, and
   supported JSON responses in memory.
4. Select the latest useful `CapturedExchange`, preferring a poll exchange when
   the exchange itself has a supported response payload or when it is needed to
   complete the search.
5. Convert the selected exchange into a provider-local `TravelokaReplayRequest`.
   The replay request contains only same-host HTTPS URL path plus query, method,
   an allowlisted header subset, the original JSON body, cookie header, and user
   agent. All sensitive fields use `repr=False`.
6. Replay the selected request with an injectable HTTP client using only that
   provider-local replay object.
7. If replay returns a supported Traveloka JSON payload, normalize replay
   payload.
8. If replay fails safely but the same selected exchange has a browser-captured
   supported JSON payload, normalize that browser-captured payload.
9. If neither replay nor capture has a supported payload, return a sanitized
   provider failure.

Replay failure must not leak request bodies, headers, cookies, URLs, tokens, or
payload fragments. Replay failures may expose only safe categories such as
`timeout`, `blocked`, `rate_limited`, `invalid_json`, `transport_error`, and
`unsupported_response`.

Traveloka replay has a strict send allowlist. The provider may send:

- `accept`
- `accept-language`
- `content-type`
- `origin`
- `referer`, only if it validates as `https://www.traveloka.com/...`
- user agent from the selected browser capture
- cookie header from the selected browser capture

The provider must drop hop-by-hop headers, browser-only fetch metadata that
tests show is unnecessary, authorization headers, proxy headers, analytics
headers, trace IDs, client hints that encode a browser fingerprint, and any
header with an unsafe name or value. "Safe to send" does not mean "safe to
expose": all replay headers and bodies remain sensitive and must be hidden from
reprs, errors, logs, reports, and Contract details.

For round trips, if search API replay does not prove enough final-total
semantics, the existing browser selection/final-total workflow remains the
fallback. The provider must not fake a final selected round-trip total from
incomplete replay data.

## Timeout And Budget Behavior

All provider work is bounded by the timeout clone supplied by the search
orchestrator. Bootstrap, capture, replay, adapter HTTP calls, polling, sleeps,
and browser interactions must use the current provider deadline.

The shared bootstrap helpers accept `deadline_monotonic`, not independent nested
timeouts. Each navigation, capture wait, cookie read, user-agent read, and
cleanup operation derives its local timeout from the remaining deadline. Tests
must prove no helper call can exceed the remaining provider attempt budget.

Cleanup gets a small best-effort slice when possible. Cleanup failures are
swallowed unless cleanup is the only failure, in which case they become a safe
`browser_bootstrap_failed` error with `phase="cleanup"`.

Retries remain orchestrator-owned. Provider-local refresh is allowed only as a
replacement for stale same-attempt session material, not as an unbounded retry
loop.

## Cache Behavior

Skyscanner may cache `BrowserBootstrapSession` in memory on the provider
instance. The cache key is provider-local and should include stable public
configuration such as base URL, market, locale, and currency. It must not be
global durable state.

The initial TTL is 300 seconds. A later implementation may tune it through tests
or live diagnostics, but it must remain in memory only.

`SkyscannerProvider.with_timeout_seconds()` must preserve the same provider-local
session manager object when cloning the provider for a bounded attempt. This
keeps the cache provider-instance scoped across orchestrator timeout clones
without making it global process state. Env cookie override paths bypass the
session manager entirely.

Traveloka does not persist or reuse harvested request material across provider
calls. Each call harvests and replays within one fresh browser session.

## Sensitive Data Policy

Sensitive data includes:

- cookies
- headers
- user agents
- browser fingerprints
- request bodies
- post data
- raw payloads
- challenge URLs
- raw session URLs
- internal provider URLs
- Skyscanner transport deeplinks
- Traveloka Datadome or WAF artifacts
- `datadome`
- `aws-waf-token`
- `tvl`
- `tvo`
- `tvs`
- provider tokens
- browser session data

Sensitive data must never appear in:

- stdout
- stderr
- Contract V1 details
- provider errors
- `SearchResponseV1`
- Markdown reports
- CLI JSON or human output
- MCP structured output or Markdown content
- SQLite history
- repr strings
- command argv
- test failure snapshots

Tests should use explicit denylist assertions across provider result, final
search response, CLI/MCP/report surfaces, and bootstrap dataclass reprs.

## Public Link Safety

Provider adapters must not return booking deeplinks, transport deeplinks,
captured request URLs, replay URLs, or challenge URLs.

Skyscanner and Traveloka offers continue to set `public_search_url = None` at
provider output time. User-clickable public links are generated only by the
existing public link layer after validation.

## Testing Plan

Default tests remain offline.

Shared bootstrap tests use fake Playwright-compatible objects to cover:

- cookie serialization
- empty-cookie failure
- user-agent extraction
- listener registration before navigation
- request predicate filtering
- first-party response capture
- timeout when no matching request arrives
- cleanup on success and failure
- neutral error context fields
- redacted reprs for cookie, headers, request bodies, URLs, and payloads
- deadline enforcement for navigation, capture, cookie read, and cleanup

Skyscanner tests cover:

- env cookie override path does not call bootstrap
- missing env cookie calls shared bootstrap and builds `SkyscannerConfig`
- cache reuse before TTL expiry
- cache expiry refresh
- timeout clones preserve the same provider-local session manager
- forced refresh after stale-session failure classes
- bootstrap error mapping to safe provider results
- no cookie/user-agent in stdout, stderr, reports, Contract details, or argv
- existing adapter, normalizer, public URL, timeout, retry, and no-leak tests
  continue to pass

Traveloka tests cover:

- runtime `cloakbrowser` import exists only in `cheapy.browser_bootstrap`
- default launcher flows through shared bootstrap
- fake browser harvests `/initial` and `/poll` request and response material
- captured request and response selection uses one deterministic exchange
- HTTP replay uses fake client only
- replay header allowlist and URL validation are enforced
- replay success takes precedence over captured payload
- replay safe failure falls back to the same exchange's browser-captured payload
- replay and capture failure maps to sanitized provider errors
- no request material appears in errors, reports, stdout, stderr, Contract
  output, or test snapshots
- round-trip final-total workflow remains available when replay is incomplete
- existing Traveloka normalization and browser workflow tests remain compatible

Focused commands:

```sh
uv run pytest tests/browser_bootstrap -v
uv run pytest tests/skyscanner -v
uv run pytest tests/traveloka -v
uv run pytest tests/test_search.py tests/test_markdown_report.py tests/test_cli.py tests/test_mcp.py -v
uv run pytest -v
```

## Migration Steps

1. Add `cheapy.browser_bootstrap` with fake-browser tests.
2. Move Traveloka's default Cloakbrowser launcher behind
   `cheapy.browser_bootstrap.launch_browser`.
3. Add Skyscanner session manager and bootstrap-driven config path while
   preserving env override.
4. Add Skyscanner cache and safe forced refresh behavior.
5. Add Traveloka harvest types and predicates for first-party search API
   request/response capture.
6. Add Traveloka fake HTTP replay client surface.
7. Prefer replay payload on replay success.
8. Fall back to browser-captured payload on safe replay failure.
9. Preserve browser selection/final-total fallback for round-trip gaps.
10. Add no-leak tests across reports, CLI, MCP, provider results, and reprs.
11. Verify no runtime Browserless references and no direct provider-level
    `cloakbrowser` imports.
12. Run focused tests and full `uv run pytest -v`.

## Acceptance Criteria

- Skyscanner normal live path can bootstrap cookie and user-agent state with
  Cloakbrowser and use the curl adapter without `CHEAPY_SKYSCANNER_COOKIE`.
- Skyscanner env cookie override still works and remains redacted.
- Missing Cloakbrowser or bootstrap runtime errors map to safe failure reasons.
- Skyscanner cache is in-memory only and provider-scoped.
- Skyscanner forced refresh is bounded by the caller budget.
- Traveloka harvests first-party search request and response material in memory.
- Traveloka can replay harvested request material through an injectable fakeable
  HTTP client.
- Traveloka uses replay payload when replay returns supported JSON.
- Traveloka falls back to same-call browser-captured payload when replay is not
  safe or complete.
- Traveloka keeps browser workflow fallback for round-trip final-total gaps.
- Cookies, headers, request bodies, tokens, challenge URLs, raw payloads,
  browser session data, and internal provider URLs never appear in public or
  persisted surfaces.
- Provider concurrency, shared 45 second budget, and one-retry behavior remain
  intact.
- `public_search_url` behavior remains safe and clickable through the public
  link layer only.
- MCP stdout remains protocol-clean.
- No Browserless runtime path exists.
- Default tests make no live provider calls.
- `uv run pytest -v` passes.
