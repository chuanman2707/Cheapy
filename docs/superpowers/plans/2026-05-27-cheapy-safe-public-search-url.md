# Cheapy Safe Public Search URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Add safe provider-scoped public search URLs to Contract V1 flight offers so agents can render fares as clickable Markdown links without exposing provider internals.

**Architecture:** Add a small provider-scoped URL safety module, add an additive `FlightOfferV1.public_search_url` field, enrich offers in the search orchestration layer, and sanitize links again before SQLite storage. Provider internals never pass raw browser/API/deeplink/session URLs into Contract V1.

**Tech Stack:** Python 3, Pydantic v2, stdlib `urllib.parse`, existing `uv`/pytest workflow, existing Contract V1 models, and existing Traveloka URL builder.

---

## Context

Safe means:

- URL is `https`.
- Host is exactly allowlisted for the offer provider.
- Path is a public search path, not an API, challenge, session, auth, or internal endpoint.
- Query does not contain encoded or plain sensitive keys/values such as token, cookie, header, body, payload, session, challenge, request ID, JWT, or auth material.
- Skyscanner `/transport_deeplink/...` remains internal actionability data and must not be exposed or persisted.
- Traveloka browser/API endpoints such as `/api/v2/flight/search/...` remain internal and must not be exposed or persisted.
- Google Fli internal filter/API URLs remain internal; Cheapy should generate a Google public search URL from Contract V1 data.

Per-provider public URL strategy:

- `google_fli`: generate `https://www.google.com/travel/flights?q=...` from actual route/date/passengers/cabin when available.
- `traveloka`: reuse `cheapy.providers.traveloka.urls.build_full_search_url(...)`, then validate as a Traveloka public search URL.
- `skyscanner`: generate `https://www.skyscanner.com.sg/transport/flights/{origin}/{destination}/{yymmdd}/...` from actual route/date/passengers/cabin when available. Do not reuse `/transport_deeplink/...`.

---

## Task 1: Add Provider-Scoped URL Safety Validator

**Files:**

- Create `cheapy/public_url_safety.py`
- Create `tests/test_public_links.py`

**Tests first:**

- [ ] Add validator tests accepting known public search URLs:

```python
validate_public_search_url(
    "traveloka",
    "https://www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK&dt=10-7-2026&ps=1.0.0&sc=ECONOMY&funnelSource=SEO-Homepage-SearchForm",
)
validate_public_search_url(
    "google_fli",
    "https://www.google.com/travel/flights?q=Flights+from+SGN+to+BKK+on+2026-07-10",
)
validate_public_search_url(
    "skyscanner",
    "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/260710/?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0",
)
```

- [ ] Add tests rejecting cross-provider URLs, for example Traveloka offer with Google URL.
- [ ] Add tests rejecting unsafe URLs:
  - `http://...`
  - hostile host or host suffix such as `www.traveloka.com.evil.test`
  - protocol-relative URL
  - userinfo in netloc
  - non-default port
  - fragment
  - `/api/...`
  - `/g/radar/...`
  - `/transport_deeplink/...`
  - encoded internal paths such as `%2Ftransport_deeplink%2F`
  - case variants such as `/API/...`
  - path traversal into internal paths
  - sensitive query key/value including encoded variants for token, cookie, header, body, payload, session, challenge, request ID, JWT, or auth.

**Expected before implementation:** `uv run pytest tests/test_public_links.py -v` fails because the module does not exist.

**Implementation:**

- [ ] Implement `validate_public_search_url(provider: str, url: str) -> str | None`.
- [ ] Keep the module independent from Pydantic models to avoid import cycles.
- [ ] Use exact provider host allowlists:
  - `google_fli`: `www.google.com`
  - `traveloka`: `www.traveloka.com`
  - `skyscanner`: `www.skyscanner.com.sg`
- [ ] Allow only provider public search paths:
  - Google: `/travel/flights`
  - Traveloka: `/en-en/flight/fulltwosearch`
  - Skyscanner: `/transport/flights/...`
- [ ] Normalize enough to reject encoded/case-varied unsafe material:
  - repeated URL decoding for path/query inspection
  - lowercase comparisons
  - normalized path segment checks
- [ ] Return the original URL string when valid; return `None` when unsafe or malformed.

**Verify:**

- [ ] `uv run pytest tests/test_public_links.py -v`

**Commit:**

- [ ] `git add cheapy/public_url_safety.py tests/test_public_links.py`
- [ ] `git commit -m "feat: add public search url validator"`

---

## Task 2: Add Contract V1 Field and Schema Coverage

**Files:**

- Modify `cheapy/models/contracts.py`
- Modify `tests/test_contracts.py`
- Modify `tests/test_schema_export.py`

**Tests first:**

- [ ] Add Contract V1 tests showing `FlightOfferV1` accepts `public_search_url=None`.
- [ ] Add a Contract V1 test showing a valid provider-matching public URL is accepted.
- [ ] Add a Contract V1 test showing an unsafe or cross-provider URL is rejected.
- [ ] Add schema export coverage showing `public_search_url` appears on `FlightOfferV1`.

**Expected before implementation:** targeted contract/schema tests fail because the field is missing.

**Implementation:**

- [ ] Add additive field to `FlightOfferV1`:

```python
public_search_url: str | None = None
```

- [ ] Add an `after` model validator that imports `validate_public_search_url` lazily, validates against `self.provider`, normalizes only by accepting the returned URL, and raises `ValueError` if unsafe.
- [ ] Keep existing fields and semantics unchanged.

**Verify:**

- [ ] `uv run pytest tests/test_contracts.py tests/test_schema_export.py -v`

**Commit:**

- [ ] `git add cheapy/models/contracts.py tests/test_contracts.py tests/test_schema_export.py`
- [ ] `git commit -m "feat: add public_search_url to flight offers"`

---

## Task 3: Add Public Search URL Builders and Attachment

**Files:**

- Create `cheapy/public_links.py`
- Extend `tests/test_public_links.py`

**Tests first:**

- [ ] Add builder tests for Traveloka one-way and round-trip URLs using the existing Traveloka URL builder.
- [ ] Add builder tests for Google one-way and round-trip public search URLs.
- [ ] Add builder tests for Skyscanner one-way and round-trip public search URLs.
- [ ] Add tests that `attach_public_search_urls(...)` uses `offer.actual_origin`, `offer.actual_destination`, `offer.actual_departure_date`, and `offer.actual_return_date`.
- [ ] Add test that if `offer.actual_return_date is None`, the generated URL is one-way even when the original request has `return_date`.
- [ ] Add test that unknown providers keep `public_search_url=None`.

**Expected before implementation:** `uv run pytest tests/test_public_links.py -v` fails because builders are missing.

**Implementation:**

- [ ] Implement `build_public_search_url(provider: str, request: SearchRequestV1, offer: FlightOfferV1) -> str | None`.
- [ ] Implement `attach_public_search_urls(request: SearchRequestV1, response: SearchResponseV1) -> SearchResponseV1`.
- [ ] For all providers, use actual offer route/date fields:
  - `offer.actual_origin`
  - `offer.actual_destination`
  - `offer.actual_departure_date`
  - `offer.actual_return_date`
- [ ] Use `request.passengers` for adults/children/infants.
- [ ] Use `request.cabin` only if present/supported by the local contract shape; otherwise omit or default to provider-safe economy behavior matching existing builders.
- [ ] Traveloka:
  - Build a `ProviderExactOneWayRequest` or `ProviderExactRoundTripRequest` from offer actuals and request passenger/cabin values.
  - Call `cheapy.providers.traveloka.urls.build_full_search_url(...)`.
  - Validate with `validate_public_search_url("traveloka", url)`.
- [ ] Google:
  - Build `https://www.google.com/travel/flights?q=...`.
  - Keep it as a public search query, not provider internal `fli` or API state.
  - Include origin, destination, departure date, optional return date, passengers, and cabin if cleanly available.
  - Validate with `validate_public_search_url("google_fli", url)`.
- [ ] Skyscanner:
  - Build `https://www.skyscanner.com.sg/transport/flights/{origin}/{destination}/{yymmdd}/...`.
  - Add public query params such as `adultsv2`, `childrenv2`, `cabinclass`, `ref=home`, and `rtn`.
  - For round trip, include the return date segment in the public path if supported by the route shape used in current provider code.
  - Never use `/transport_deeplink/...`.
  - Validate with `validate_public_search_url("skyscanner", url)`.
- [ ] If any builder cannot produce a validated URL, return `None`.
- [ ] Rebuild offer objects with `.model_copy(update={"public_search_url": url})`.

**Verify:**

- [ ] `uv run pytest tests/test_public_links.py -v`

**Commit:**

- [ ] `git add cheapy/public_links.py tests/test_public_links.py`
- [ ] `git commit -m "feat: build public search urls for offers"`

---

## Task 4: Wire Search Orchestration

**Files:**

- Modify `cheapy/search.py`
- Modify `tests/test_search.py`

**Tests first:**

- [ ] Add a `search_exact(...)` test where a fake `traveloka` provider returns an offer and the response offer includes a validated `public_search_url`.
- [ ] Add a test where an unknown provider returns an offer and `public_search_url` remains `None`.
- [ ] Add a test proving flexible/actual date output uses actual offer date in the URL, not the original requested date.
- [ ] Confirm existing ranking/dedup expectations still pass.

**Expected before implementation:** targeted search tests fail because search responses are not enriched.

**Implementation:**

- [ ] Import `attach_public_search_urls` in `cheapy/search.py`.
- [ ] In `_response_from_provider_results(...)`, build the existing `SearchResponseV1` first, then return `attach_public_search_urls(request, response)`.
- [ ] Do not mutate provider result objects in place.
- [ ] Do not let link generation failure fail search results.

**Verify:**

- [ ] `uv run pytest tests/test_search.py -v`

**Commit:**

- [ ] `git add cheapy/search.py tests/test_search.py`
- [ ] `git commit -m "feat: attach public search urls to search results"`

---

## Task 5: Sanitize SQLite Storage

**Files:**

- Modify `cheapy/storage/sqlite.py`
- Modify `tests/storage/test_sqlite.py`

**Tests first:**

- [ ] Add storage test showing a valid `public_search_url` remains in stored `search_runs.response_json`.
- [ ] Add storage test injecting an invalid URL via `model_copy(update=...)`, then assert `sanitize_response_for_storage(...)` nulls it and does not fail.
- [ ] Add storage test proving `offer_observations` has no URL/public link column and no public URL value.
- [ ] Add regression coverage that unsafe strings such as `transport_deeplink`, token, cookie, session, challenge, and API endpoints are not persisted in sanitized response JSON.

**Expected before implementation:** sanitizer tests fail because offer-level URL sanitization is absent.

**Implementation:**

- [ ] Import `validate_public_search_url` in `cheapy/storage/sqlite.py`.
- [ ] In `sanitize_response_for_storage(...)`, after `response.model_dump(mode="json")` and before `SearchResponseV1.model_validate(...)`, sanitize `payload["offers"][*]["public_search_url"]`.
- [ ] Add helper `_sanitize_offer_public_search_urls(payload: dict[str, Any]) -> None`:
  - if URL is missing or `None`, leave as `None`;
  - if URL is not a string, set `None`;
  - validate with the offer provider;
  - set invalid URLs to `None`;
  - never raise with the unsafe URL in an error message.
- [ ] Keep `offer_observations` schema unchanged; no URL columns.
- [ ] Keep storage local-only and sanitized.

**Verify:**

- [ ] `uv run pytest tests/storage/test_sqlite.py -v`

**Commit:**

- [ ] `git add cheapy/storage/sqlite.py tests/storage/test_sqlite.py`
- [ ] `git commit -m "feat: sanitize public search urls before storage"`

---

## Task 6: Update Agent Output Guidance

**Files:**

- Modify `cheapy/agent_hooks.py`
- Modify `.codex/skills/cheapy/SKILL.md`
- Modify `.cheapy/claude-instructions.md`
- Modify `tests/test_agent_hooks.py`

**Tests first:**

- [ ] Add agent instruction test asserting instructions tell agents:
  - when an offer has `public_search_url`, render the fare/provider as a Markdown link;
  - when absent, render plain text with no fabricated link.

**Expected before implementation:** instruction test fails because no link guidance exists.

**Implementation:**

- [ ] Update `INSTRUCTION_BODY` in `cheapy/agent_hooks.py` with concise wording:

```text
When an offer includes public_search_url, render the fare/provider as a Markdown link, for example [4,920,000 VND on Traveloka](...). If public_search_url is absent, show plain text and do not invent a link.
```

- [ ] Mirror the same guidance into the project-local skill and Claude instructions.
- [ ] Do not add Browserless references.
- [ ] Do not claim provider support beyond what this branch actually exposes.

**Verify:**

- [ ] `uv run pytest tests/test_agent_hooks.py -v`

**Commit:**

- [ ] `git add cheapy/agent_hooks.py .codex/skills/cheapy/SKILL.md .cheapy/claude-instructions.md tests/test_agent_hooks.py`
- [ ] `git commit -m "docs: teach agents to render public search links"`

---

## Task 7: MCP/CLI Serialization Guardrails

**Files:**

- Inspect `tests/test_mcp.py`
- Inspect `tests/test_cli.py`
- Add targeted tests only if existing coverage does not already prove serialization behavior.

**Tests first:**

- [ ] If MCP tests can exercise `SearchResponseV1` serialization without live providers, add an assertion that `public_search_url` is present when the fake provider/search fixture includes it.
- [ ] If CLI tests cover JSON output, add an assertion that the new field is additive and does not break existing output.
- [ ] Keep stdout protocol-clean assertions intact.

**Implementation:**

- [ ] Update only the minimal MCP/CLI serialization tests required by the local test shape.
- [ ] Avoid live provider calls.
- [ ] Do not add logging/printing to stdout in MCP paths.

**Verify:**

- [ ] `uv run pytest tests/test_mcp.py tests/test_cli.py -v`

**Commit:**

- [ ] If files changed: `git add tests/test_mcp.py tests/test_cli.py`
- [ ] If files changed: `git commit -m "test: cover public search url serialization"`

---

## Task 8: Full Verification

**Run targeted suites:**

- [ ] `uv run pytest tests/test_public_links.py -v`
- [ ] `uv run pytest tests/test_contracts.py tests/test_schema_export.py -v`
- [ ] `uv run pytest tests/test_search.py -v`
- [ ] `uv run pytest tests/storage/test_sqlite.py -v`
- [ ] `uv run pytest tests/test_agent_hooks.py tests/test_mcp.py tests/test_cli.py -v`

**Run full suite:**

- [ ] `uv run pytest -v`

**Safety checks:**

- [ ] `rg -n "Browserless|browserless" cheapy tests .codex .cheapy`
  - Existing historical references may exist outside touched files; do not introduce new ones.
- [ ] `rg -n "transport_deeplink|/api/v2/flight/search|challenge|cookie|authorization|jwt|token" tests cheapy`
  - Confirm any matches are validators, sanitizer tests, or internal provider code, not output/report guidance.
- [ ] `git diff --check`
- [ ] `git status --short`

**Final review checklist:**

- [ ] Contract change is additive.
- [ ] Unknown or unsupported provider links fall back to `None`.
- [ ] Provider-scoped validation prevents cross-provider URL reuse.
- [ ] Storage does not add URL columns to `offer_observations`.
- [ ] SQLite `response_json` stores only sanitized validated public URLs.
- [ ] MCP stdout remains protocol-clean.
- [ ] Default tests make no live provider calls.
- [ ] Browserless remains removed.

**Final commit:**

- [ ] If verification fixes changed files, commit them.
- [ ] Summarize changed files, tests run, and any residual risk.

---

## Expected Result

After implementation, each returned offer may include:

```json
{
  "provider": "traveloka",
  "total_amount": 4920000,
  "currency": "VND",
  "public_search_url": "https://www.traveloka.com/en-en/flight/fulltwosearch?..."
}
```

Agent-facing output should render:

```markdown
[4,920,000 VND on Traveloka](https://www.traveloka.com/en-en/flight/fulltwosearch?...)
```

If `public_search_url` is `null`, render plain text:

```markdown
4,920,000 VND on Traveloka
```
