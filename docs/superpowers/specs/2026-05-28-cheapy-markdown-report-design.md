# Cheapy Markdown Report Design

Date: 2026-05-28

## Summary

Add a professional Markdown presentation layer for Cheapy search results so
agent-visible output is easier to scan and fare text can link to a safe public
provider search when available.

The machine-readable Contract V1 response remains the source of truth. The new
report layer renders from existing `SearchRequestV1` and `SearchResponseV1`
objects, validates links before using them, and does not reach into provider
internals.

## Approved Direction

Use option 2: a dedicated Markdown formatter plus MCP human-readable content,
while keeping MCP structured output as unchanged Contract V1 JSON.

MCP output shape:

- `structuredContent`: the existing `SearchResponseV1` payload.
- `content`: one Markdown text report rendered from the same request and
  response.

CLI remains JSON-first. Add opt-in Markdown output where it is useful for local
agent/report workflows.

## Goals

- Render search results as readable Markdown for common renderers such as
  GitHub, Codex, and ChatGPT.
- Show route, dates, passenger counts, and cabin in a compact header.
- Include a summary box/table, best offers table, and provider
  status/warnings section when status data exists.
- Render fare/provider text as a Markdown link only when
  `offer.public_search_url` is present and passes the provider-scoped public
  URL validator.
- Render plain fare/provider text when the link is missing or invalid.
- Avoid showing a raw URL separately when the fare is already linked.
- Keep provider internals, raw provider URLs, raw payloads, cookies, headers,
  request bodies, tokens, challenge URLs, browser/session data, and Browserless
  out of the report.
- Keep default tests offline and deterministic.

## Non-Goals

- No Contract V1 response shape change.
- No new provider behavior.
- No live provider calls in tests.
- No booking or checkout deeplinks.
- No storage schema change.
- No broad CLI redesign.
- No Browserless reintroduction.

## Current Context

The target branch already has `FlightOfferV1.public_search_url` as an optional
field with provider-scoped validation. Search orchestration attaches safe public
search URLs where possible. SQLite storage preserves valid public search URLs
inside stored response JSON but keeps URL data out of normalized offer
observation rows.

MCP currently returns `SearchResponseV1` directly. FastMCP supports returning a
`CallToolResult`, which can carry both human-readable content and structured
content. That allows Cheapy to improve agent-visible output without wrapping or
mutating Contract V1.

CLI `history` and `watchlist` commands currently emit JSON. That behavior is
kept as the default to avoid breaking scripts.

## Architecture

Add:

```text
cheapy/markdown_report.py
```

This module owns presentation only. It imports Contract V1 models and the
shared public URL validator, but provider modules do not import it.

Public functions:

```python
def render_search_report(
    request: SearchRequestV1,
    response: SearchResponseV1,
) -> str:
    ...

def render_offer_price(offer: FlightOfferV1) -> str:
    ...
```

Internal helpers may render summary rows, best offer rows, provider status
rows, and warning/error rows. Keep helpers small and pure.

Update MCP in `cheapy/mcp.py`:

1. Build and validate `SearchRequestV1` as today.
2. Call `search_with_storage(request)` as today.
3. Render Markdown with `render_search_report(request, result.response)`.
4. Return `mcp.types.CallToolResult` with:
   - `content=[TextContent(type="text", text=markdown)]`
   - `structuredContent=result.response.model_dump(mode="json")`
   - `isError=False`

The tool schema remains based on `SearchRequestV1`. The structured response
must continue to validate as `SearchResponseV1`.

## CLI Integration

Keep all current JSON output as the default.

Add opt-in Markdown output:

- `cheapy history show RUN_ID --markdown`
- `cheapy watchlist check WATCHLIST_ID --markdown`

For `history show --markdown`, reconstruct `SearchRequestV1` explicitly from
the stored `search_run` fields. Parse `search_run["passengers_json"]` with
`json.loads`, then validate only these request fields:

- `schema_version`
- `origin`
- `destination`
- `departure_date`
- `return_date`
- `search_mode`
- `passengers`
- `max_results`

Ignore storage-only fields such as `id`, `created_at_utc`, `request_id`,
`status`, `trip_type`, and `mixed_currency`. Render the stored `response` as
`SearchResponseV1`. If request reconstruction or response validation fails,
emit the existing safe JSON storage error on stderr and exit non-zero.

For `watchlist check --markdown`, render the fresh search response after the
watchlist decision is recorded. The Markdown report focuses on the search
result; the JSON decision payload remains available by omitting `--markdown`.

## Markdown Format

Use plain GitHub-flavored Markdown that renders cleanly without HTML.

Recommended structure:

```markdown
## CXR -> SGN | 2026-07-10 | 1 adult | Economy

| Summary | Value |
| --- | --- |
| Status | success |
| Offers | 3 |
| Search mode | exact |
| Providers | traveloka: success, google_fli: skipped |

### Best Offers

| Rank | Fare | Route | Dates | Stops | Duration |
| ---: | --- | --- | --- | ---: | --- |
| 1 | [4,920,000 VND on Traveloka](https://...) | CXR -> SGN | 2026-07-10 | 0 | 1h 10m |

### Provider Status

| Provider | Status | Calls | Notes |
| --- | --- | ---: | --- |
| traveloka | partial | 1/1 | Traveloka search timed out after returning partial fares. |
```

In Provider Status, render Calls as `executed/planned`. Include succeeded,
failed, and retryable values in Notes when they clarify status, for example:
`succeeded: 1, failed: 0, retryable: false`.

When there are no offers, the Best Offers section should say `No offers
returned.` instead of rendering an empty table.

Warnings and errors should render only safe code and message text:

- `warning.code`
- `warning.message_en`
- `error.code`
- `error.message_en`
- provider status, call counts, and retryable flag

Do not render arbitrary `details` dictionaries, because they are not needed for
scan-friendly output and can contain operational context that should stay in
machine-readable JSON.

## Link Safety

`render_offer_price(offer)` builds the visible fare text from normalized
Contract V1 fields:

```text
4,920,000 VND on Traveloka
```

If `offer.public_search_url` is a string and
`validate_public_search_url(offer.provider, offer.public_search_url)` returns a
safe URL, render:

```markdown
[4,920,000 VND on Traveloka](https://...)
```

Otherwise render the plain text fare. The formatter never reads raw provider
URLs and never invents links.

Because `FlightOfferV1` validation rejects unsafe URLs, invalid-link formatter
tests should bypass validation intentionally with
`valid_offer.model_copy(update={"public_search_url": unsafe_url})`, then assert
`render_offer_price()` revalidates and renders plain text.

Markdown special characters in visible text must be escaped. URLs are used only
after validation. Since the validator already rejects control characters,
fragments, unexpected hosts, unexpected paths, and sensitive query material, the
formatter can use the validated URL directly.

## Formatting Rules

- Format currency amounts without decimal places when the numeric amount is an
  integer, with thousands separators.
- Preserve the original three-letter currency code.
- Display provider names in a readable form:
  - `google_fli` -> `Google Fli`
  - `traveloka` -> `Traveloka`
  - unknown providers get title-cased after replacing underscores with spaces.
- Display duration as `1h 10m` style.
- Display cabin as `Economy` because Contract V1 has no cabin field yet.
- Display passengers from `SearchRequestV1.passengers`.
- Sort/display offers in the response order, which is already ranked by search
  orchestration.

## Error Handling

Report rendering should fail closed:

- Invalid or missing public links become plain fare text.
- Missing optional return dates render as one-way.
- Empty warnings/errors/provider statuses omit those subsections.

MCP should not fail the search solely because Markdown rendering fails. If a
renderer exception occurs, MCP returns a minimal safe Markdown fallback such as
`## Cheapy flight search results` plus the structured Contract V1 response.
This fallback must not include exception messages, raw payloads, or paths.

CLI Markdown rendering may use the same fallback, but JSON defaults remain
unchanged.

## Tests

Add focused tests for:

- An offer with a valid `public_search_url` renders the fare/provider as a
  Markdown hyperlink.
- An offer without `public_search_url` renders plain fare/provider text.
- An invalid link is not rendered as a hyperlink.
- The report does not show a raw URL separately when the price is linked.
- Provider warnings and statuses render cleanly with code/message/status.
- Top-level `warnings`/`errors` and nested
  `provider_statuses[*].warnings`/`errors` render only code, message, status,
  call counts, and retryable fields.
- Warning/error `details` keys and values are not rendered, including strings
  containing `url`, `token`, `payload`, or `headers`.
- Empty offers render a clear no-offers line.
- MCP structured content still validates as `SearchResponseV1`.
- MCP text content includes the Markdown report.
- CLI default output remains JSON.
- CLI `--markdown` output uses the report helper.

Run relevant tests with `uv`:

```text
uv run pytest tests/test_mcp.py tests/test_cli.py tests/test_contracts.py -v
```

If a dedicated formatter test file is added, include it in the targeted run.

## Acceptance Criteria Mapping

- Markdown formatter/report exists as a presentation layer.
- Valid `public_search_url` renders clickable fare/provider text.
- Missing or invalid links render clean plain text.
- Raw/internal URLs are never exposed by the formatter.
- MCP stdout remains protocol-clean because Markdown is returned through MCP
  content, not printed.
- Structured MCP output remains Contract V1 JSON.
- Tests cover linked, unlinked, invalid-link, provider status/warning, and
  existing Contract behavior.
