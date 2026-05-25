# Cheapy Local Browser Bootstrap Design

Date: 2026-05-25

## Summary

Replace Cheapy's hosted Browserless dependency with a project-owned local
browser bootstrap module powered by CloakBrowser.

The new module becomes the shared bootstrap surface for live providers that need
browser-created cookies, user agent state, or selected first-party network
requests. Skyscanner and Traveloka will use this module instead of calling
Browserless APIs or checking `BROWSERLESS_TOKEN`. Future providers can use the
same module without importing CloakBrowser directly.

The design removes hosted Browserless as a runtime dependency and keeps browser
state local, ephemeral, and provider-scoped.

## Approved Decisions

1. Browserless is removed from all runtime provider paths.
2. `BROWSERLESS_TOKEN` is no longer required for Skyscanner or Traveloka.
3. CloakBrowser is the local browser runtime and remains a normal package
   dependency.
4. Provider code does not import `cloakbrowser` directly; it calls the shared
   bootstrap module for both one-shot bootstrap calls and any full browser
   workflow that still needs a Playwright-compatible browser object.
5. The bootstrap module is provider-neutral. It does not know about flights,
   Contract V1, Skyscanner, Traveloka, or provider-specific endpoints.
6. Browser cookies are held in memory only and are not persisted to disk.
7. Provider-level session caches may remain in memory and provider-scoped.
8. No login, account-backed session, captcha solving service, proxy rotation,
   identity rotation, or persistent user browser profile is added.
9. Default tests remain offline and do not launch a real browser.
10. Live tests remain opt-in through `CHEAPY_RUN_LIVE_TESTS=1` and do not require
    `BROWSERLESS_TOKEN`.

## Goals

- Remove dependency on hosted Browserless free tier, tokens, and API endpoints.
- Provide one reusable local browser bootstrap module for current and future
  live providers.
- Keep provider-specific parsing, endpoint selection, retry policy, and Contract
  V1 mapping inside each provider.
- Preserve current Skyscanner and Traveloka search contracts.
- Preserve safe secret handling: no cookies, tokens, headers, raw request
  bodies, or raw provider payloads in public errors, logs, reprs, or test
  snapshots.
- Keep the implementation small enough to test with fake browser objects.

## Non-Goals

- No persistent browser profile or disk-backed cookie cache.
- No new MCP tools or Contract V1 schema changes.
- No provider framework rewrite beyond the bootstrap boundary needed for this
  migration.
- No direct HTTP-only replacement for provider flows that require browser state.
- No live provider calls in default tests.
- No automatic proxy, captcha, login, or challenge-bypass system.

## Architecture

Add a new package:

```text
cheapy/browser_bootstrap/
  __init__.py
  cloak.py
  cookies.py
  errors.py
  types.py
```

`types.py` contains provider-neutral dataclasses:

```python
@dataclass(frozen=True)
class BrowserBootstrapSession:
    cookie_header: str = field(repr=False)
    user_agent: str


@dataclass(frozen=True)
class CapturedRequest:
    url: str = field(repr=False)
    method: str
    post_data: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class BrowserNetworkCapture:
    cookie_header: str = field(repr=False)
    user_agent: str
    requests: tuple[CapturedRequest, ...] = field(repr=False)


@dataclass(frozen=True)
class BrowserBootstrapErrorContext:
    failure_type: str
    phase: str
    http_status_code: int | None = None
    exception_type: str | None = None
```

`cookies.py` converts Playwright-compatible browser cookies into a `Cookie`
header. It may support optional domain filtering, but it must not log or expose
cookie values.

`errors.py` defines neutral bootstrap errors:

- `BrowserBootstrapUnavailable`
- `BrowserBootstrapTimeout`
- `BrowserBootstrapBlocked`
- `BrowserBootstrapCookieUnavailable`
- `BrowserNetworkCaptureUnavailable`

Each error carries a safe `BrowserBootstrapErrorContext`. The context may expose
only:

- `failure_type`
- `phase`, such as `launch`, `context_page_setup`, `navigation`,
  `capture_wait`, `cookie_read`, or `cleanup`
- `http_status_code`, when a navigation or captured response status is known
- `exception_type`, using the exception class name only

The context must not include full URLs, query strings, headers, cookies,
captured request bodies, provider payloads, raw exception messages, or
environment values.

`cloak.py` is the only module that imports `cloakbrowser`. It exposes sync APIs:

```python
def launch_browser(**kwargs: object) -> object: ...


def bootstrap_cookies(
    *,
    page_url: str,
    timeout_seconds: float,
    wait_until: str = "domcontentloaded",
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserBootstrapSession: ...


def capture_network_requests(
    *,
    page_url: str,
    timeout_seconds: float,
    request_predicate: Callable[[str, str], bool],
    wait_until: str = "domcontentloaded",
    capture_timeout_seconds: float,
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserNetworkCapture: ...
```

The top-level `launch_browser` wrapper is the only default path to
`cloakbrowser.launch`. Existing browser workflows may accept this wrapper as
their default launcher while still using provider-specific page interactions.

The optional `launch_browser` injection on bootstrap helpers keeps tests offline
and lets providers or tests supply fake Playwright-compatible browser objects.
If `user_agent` is supplied, the helper creates a context with that user agent.
If it is not supplied, the helper uses CloakBrowser's default user agent and
returns the value observed from `navigator.userAgent`.

The module launches a fresh headless CloakBrowser browser per bootstrap call,
creates a fresh context/page, registers listeners before navigation when network
capture is requested, navigates to the supplied page URL, collects cookies and
the evaluated `navigator.userAgent`, then closes page, context, and browser in
`finally` blocks.

Default `wait_until` is `domcontentloaded` to match the existing local
Traveloka browser workflow. Providers may request a stricter wait state when a
specific site needs it, but the capture timeout remains the deciding condition
for network request capture.

## Provider Integration

### Skyscanner

Skyscanner replaces `cheapy.providers.skyscanner.browserless` runtime usage with
a local session manager backed by `browser_bootstrap.bootstrap_cookies`.

The Skyscanner adapter flow remains:

1. Get or refresh an in-memory provider-scoped browser session.
2. Convert `BrowserBootstrapSession` into `SkyscannerConfig`.
3. Resolve origin and destination entities.
4. Fetch itineraries through the existing HTTP/search code.
5. On repeated `NoUsableResults`, force one session refresh and retry according
   to the existing attempt budget.

Missing `BROWSERLESS_TOKEN` no longer causes `SKIPPED`. If local browser launch
fails, the provider returns a structured failed result with a safe
browser-bootstrap failure type.

### Traveloka

Traveloka replaces Browserless function and unblock calls with
`browser_bootstrap.capture_network_requests`.

The existing Traveloka browser adapter and session workflow also move their
default launcher behind `browser_bootstrap.launch_browser`. After migration, no
Traveloka module imports `cloakbrowser` directly. The Traveloka browser workflow
may still own page-level actions such as visible option discovery, selection,
and final total reads; the shared module owns only launching and generic
bootstrap/capture primitives.

The Traveloka bootstrapper supplies a Traveloka search URL and a predicate that
captures first-party poll requests only:

```text
POST /api/v2/flight/search/poll
```

Traveloka provider logic remains responsible for:

- selecting the latest supported poll request
- parsing `post_data` into `poll_body`
- using the captured `cookie_header` and `user_agent`
- deciding whether HTTP replay or the existing browser workflow should handle a
  specific search path
- mapping partial capture, timeout, block, rate-limit, and parse outcomes to
  existing provider errors

The shared bootstrap module does not parse Traveloka payloads and does not know
which Traveloka request is business-critical beyond the predicate supplied by
the provider.

The Browserless `/unblock` fallback is not carried forward as a hosted-service
feature. Traveloka local bootstrap uses this fallback policy instead:

1. Run one local `capture_network_requests` call with a fresh ephemeral browser
   context.
2. If no matching poll request is captured but cookies were available, run one
   additional local capture with a fresh ephemeral browser context and the same
   request URL.
3. Do not persist or seed cookies between those two contexts in V1.
4. If the second capture also lacks a matching poll request, raise the existing
   Traveloka fresh-body-unavailable provider failure.

This keeps the old "one extra chance after missing fresh body" shape without
reintroducing hosted unblock APIs, residential proxy parameters, persistent
state, or provider-internal identity rotation.

## Data Flow

Skyscanner cookie bootstrap:

1. Provider requests a session from its in-memory session manager.
2. Session manager calls `bootstrap_cookies(page_url=...)` on cache miss,
   expiration, or forced refresh.
3. Bootstrap opens local CloakBrowser, navigates, collects cookies and user
   agent, and closes resources.
4. Provider uses the returned cookie header and user agent for existing
   Skyscanner HTTP requests.

Traveloka network capture:

1. Provider builds the Traveloka full search URL.
2. Provider calls `capture_network_requests(...)` with a Traveloka poll-request
   predicate.
3. Bootstrap opens local CloakBrowser, registers request listeners, navigates,
   records matching request URLs and post bodies, collects cookies and user
   agent, and closes resources.
4. Provider converts the latest captured poll request into the existing
   `poll_url` and `poll_body` contract.
5. Existing Traveloka normalization and Contract V1 response shaping continue.

Future providers follow the same pattern: call a small bootstrap API, then keep
provider-specific interpretation in the provider package.

## Error Handling

The core module raises neutral errors only. It never returns public Contract V1
errors and never names a specific provider.

Provider mapping rules:

- `BrowserBootstrapUnavailable` maps to a provider failure with
  `failure_type="browser_bootstrap_failed"` and `retryable=True`. Launch and
  context/page setup failures use this mapping.
- `BrowserBootstrapTimeout` maps to `failure_type="timeout"` and
  `ErrorCode.PROVIDER_TIMEOUT`. Navigation and capture-wait timeouts use this
  mapping.
- `BrowserBootstrapBlocked` maps to provider `blocked` or `rate_limited`
  depending on the observed `http_status_code`; status `429` maps to
  `rate_limited`, and statuses `401` or `403` map to `blocked`.
- `BrowserBootstrapCookieUnavailable` maps to
  `failure_type="browser_cookie_unavailable"`.
- `BrowserNetworkCaptureUnavailable` maps to the provider-specific equivalent
  of missing fresh browser state, such as Traveloka's fresh poll body
  unavailable failure.

Error details may include safe metadata such as provider name, capability,
failure type, HTTP status code, and exception class name. They must not include
cookies, raw request bodies, raw headers, full URLs with query strings,
provider payloads, browser tokens, or environment variable values.

Cleanup failures are swallowed after best-effort resource closing unless no
earlier error exists; if cleanup is the only failure, it maps to
`BrowserBootstrapUnavailable` with `phase="cleanup"` and the cleanup exception
class name.

## Security And Privacy

- Cookies and captured request bodies use `repr=False`.
- No cookies or captured bodies are written to disk.
- No browser state is persisted through a user data directory.
- The bootstrap module performs cleanup in `finally` blocks.
- Diagnostic output remains stderr-only where CLI or MCP code already has that
  requirement.
- `cheapy mcp` stdout remains protocol-clean.

## Testing

Default tests are offline.

Core bootstrap tests use fake Playwright-compatible objects to cover:

- browser/context/page cleanup on success and failure
- cookie serialization and empty-cookie handling
- user agent extraction
- request listener registration before navigation
- request predicate filtering
- capture timeout when no matching request is observed
- neutral error classification without leaking cookie or body values
- safe error context fields for launch, navigation, capture, cookie, and cleanup
  phases

Skyscanner provider tests cover:

- no `BROWSERLESS_TOKEN` requirement
- session manager calls the shared bootstrap function
- cached sessions are reused before TTL
- forced refresh after repeated `NoUsableResults`
- provider error mapping for local browser bootstrap failures
- existing unsupported-passenger, parse, block, rate-limit, and no-usable-results
  behavior remains unchanged

Traveloka provider tests cover:

- no `BROWSERLESS_TOKEN` requirement
- captured poll request is converted to the existing poll bootstrap contract
- missing captured poll request maps to the expected provider failure
- missing captured poll request triggers exactly one fresh local recapture before
  failing
- existing Traveloka browser workflow default launcher comes from
  `cheapy.browser_bootstrap`, not a direct provider-level `cloakbrowser` import
- cookies, user agent, poll URL, and poll body are passed only through internal
  objects and are not leaked in errors
- existing HTTP adapter, browser workflow, normalizer, and provider tests remain
  behaviorally compatible

Packaging tests continue to assert `cloakbrowser>=0.3.26` is a runtime
dependency. Browserless-specific tests are renamed, removed, or rewritten so
they assert local browser bootstrap behavior instead.

## Migration Steps

1. Add the neutral `cheapy.browser_bootstrap` package with fake-browser unit
   coverage.
2. Refactor Skyscanner's Browserless session manager into a local browser
   session manager that calls `bootstrap_cookies`.
3. Remove Skyscanner's `BROWSERLESS_TOKEN` skip gate and update error names and
   tests.
4. Refactor Traveloka Browserless capture/unblock code to use
   `capture_network_requests` and the one-time fresh local recapture policy.
5. Remove Traveloka's `BROWSERLESS_TOKEN` skip gate and update error names and
   tests.
6. Move the existing Traveloka browser adapter default launcher to
   `browser_bootstrap.launch_browser`.
7. Delete or fully retire Browserless client classes, constants, env-file token
   loaders, and Browserless endpoint references from runtime modules.
8. Update scripts and live-test skip messages so Browserless is not required.
9. Run focused provider tests, then `uv run pytest -v`.

## Acceptance Criteria

- Searching with default-enabled Skyscanner and Traveloka does not require
  `BROWSERLESS_TOKEN`.
- No runtime provider path calls `production-sfo.browserless.io` or any other
  Browserless API endpoint.
- Provider code uses the shared bootstrap module instead of importing
  CloakBrowser directly.
- `rg -n "from cloakbrowser|import cloakbrowser" cheapy` returns only the shared
  bootstrap module.
- Browserless references may remain only in historical docs, old committed
  design notes, or compatibility comments that are not imported by runtime
  provider code.
- Default tests do not launch a real browser or make live network calls.
- Contract V1 schemas, MCP tool names, and public search request shapes are
  unchanged.
- Secret redaction tests cover cookies and captured request bodies.
