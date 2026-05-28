# Cheapy Live Search Reliability Design

Date: 2026-05-28

## Summary

Improve Cheapy live flight search reliability and user-visible failure clarity
without changing Contract V1.

The search orchestrator will call planned providers concurrently under one
45-second wall-clock provider budget. Retryable provider failures get at most
one retry inside that shared budget. Markdown and MCP reports will show safe
failure reasons such as `timeout`, `missing_cookie`, or `provider_blocked`
instead of leaving users with only opaque `[redacted]` messages. Skyscanner
normalization will support safe multi-segment long-haul itineraries from fake
payloads while keeping raw provider internals private.

## Approved Decisions

1. Run provider calls concurrently from `cheapy/search.py`.
2. Use a shared 45-second wall-clock budget for the full provider batch.
3. Retry each retryable provider failure at most once, only while there is
   remaining budget.
4. Keep provider accounting logical: a provider remains `1/1` even if its
   logical call used one internal retry.
5. Do not change Contract V1 fields or provider status schemas.
6. Show safe failure reasons in Markdown/MCP report output.
7. Keep sensitive messages redacted and never render arbitrary error details.
8. Parse Skyscanner multi-segment legs only when segment fields are complete
   and route-consistent.
9. Keep `public_search_url` generation only in the public link layer.
10. Keep default tests offline with fake providers and fake Skyscanner payloads.

## Goals

- Reduce live search latency from sequential provider waits to the slowest
  in-budget provider path.
- Return partial results when one provider times out or fails.
- Keep response and provider status ordering deterministic.
- Surface safe provider failure reasons in human-readable reports.
- Preserve public search URL safety and clickability.
- Improve Skyscanner long-haul and connecting itinerary coverage.
- Keep MCP stdout protocol-clean.
- Avoid live network calls in normal tests.

## Non-Goals

- No Contract V1 schema changes.
- No Browserless, browser automation reintroduction, captcha solving, proxy
  rotation, or anti-bot workaround.
- No storage of cookies, headers, request bodies, tokens, challenge URLs, raw
  payloads, raw provider URLs, browser data, or session data.
- No exposure of Skyscanner transport deeplinks.
- No new live provider behavior in default tests.
- No new booking or checkout deep links.

## Current Context

`cheapy/search.py` currently calls selected planned providers sequentially in
`_call_planned_providers()`. Each provider returns a `ProviderResult`, and the
orchestrator converts provider results into `SearchResponseV1`.

Provider errors already often include safe fields in `ErrorV1.details`, such as
`failure_type`, `exception_type`, and `http_status_code`. The Markdown report
currently avoids rendering details, which is good for safety, but it also means
redacted sensitive messages can appear only as `[redacted]`.

Skyscanner currently avoids exposing internal deeplinks, but the adapter rejects
legs with more than one segment. That makes long-haul routes such as DUS to SGN
more likely to become `no_usable_results` even when the payload contains enough
safe segment data.

The public URL layer already builds provider-scoped safe search links and
validates them before display. That behavior remains unchanged.

## Architecture

### Search Orchestration

Update `cheapy/search.py` so `_call_planned_providers()` creates one async task
per selected `PlannedProviderCall`. Use `asyncio.gather()` in the selected-call
order so the returned provider result list remains deterministic.

Each task handles one logical provider call:

1. Build the provider-local request.
2. Call the provider method.
3. Normalize malformed or invalid provider output into a failed
   `ProviderResult`.
4. Catch provider exceptions and convert them into sanitized failed results.
5. If the normalized result is retryable and failed, retry once if the shared
   batch budget has remaining time.
6. Return the final normalized result.

The provider batch uses a shared monotonic deadline:

```text
deadline = start + 45 seconds
```

Provider attempts run only with the remaining time. The implementation must not
rely on cancelling `asyncio.to_thread()` work to enforce this budget, because
cancelling the asyncio task does not necessarily stop the underlying blocking
thread. Any provider attempt that enters blocking work must be bounded by the
remaining batch time before it starts.

For the built-in live providers, the implementation should make provider
wrappers budget-aware so their internal transport timeout is no greater than
the remaining batch time. Skyscanner's curl timeout and Traveloka's browser
workflow timeout are provider-owned deadlines and can be lowered per logical
call. Google Fli cannot depend on a cancelled `to_thread()` future as its only
deadline; if its upstream sync search has no safe per-call timeout, the plan
must either introduce a killable execution boundary for that call or avoid
starting any retry that cannot finish within the remaining budget.

If the deadline expires before a provider produces a bounded result, convert
that logical call into a sanitized retryable timeout failure with
`failure_type = "timeout"`. Completed provider results must still be returned.

Provider status accounting stays logical:

- `planned_call_count = 1`
- `executed_call_count = 1`
- retry attempts do not make output look like `2/1`

This keeps `search_plan.executed_provider_call_count` as the number of planned
provider calls selected by the planner, not the number of internal attempts.

### Retry Policy

Retry is orchestrator-level, not duplicated inside each provider.

A retry is allowed only when all conditions are true:

- the normalized provider result has `status = failed`
- the provider result has `retryable = true` or at least one provider error has
  `retryable = true`
- the call has not already retried
- the shared 45-second budget has remaining time

Non-retryable errors are returned immediately. A retry that succeeds replaces
the first failed result. A retry that fails returns the final failed result.
The final result is what appears in `response.errors` and
`provider_statuses`.

No backoff is needed for this milestone; the retry budget is the shared 45-second
deadline.

The final logical `ProviderResult.duration_ms` should measure elapsed time for
the whole logical provider call, including retry time, not only the final
attempt. This keeps duration aligned with logical `1/1` provider accounting.

### Safe Failure Reasons

Add presentation helpers in `cheapy/markdown_report.py` to derive a safe reason
from a warning or error without rendering arbitrary details.

Allowed reason sources:

1. `details["failure_type"]` if it is one of the allowlisted safe categories.
2. `details["http_status_code"]` mapped to a safe category:
   - `401` or `403` -> `provider_blocked`
   - `429` -> `rate_limited`
   - `500` and above -> `transport_error`
3. `details["exception_type"]` mapped conservatively:
   - timeout-shaped exception names -> `timeout`
   - otherwise no public reason unless explicitly allowlisted.

Allowlisted safe reasons for report output:

- `timeout`
- `provider_blocked`
- `blocked`
- `rate_limited`
- `parse_failed`
- `parse_error`
- `no_usable_results`
- `missing_cookie`
- `transport_error`
- `unsupported_passengers`
- `http_error`
- `invalid_argument`
- `entity_not_found`
- `entity_ambiguous`
- `unexpected_error`
- `no_usable_outbound_data`
- `unsupported_response`
- existing Traveloka safe partial failure types already emitted by provider
  code, such as `return_capture_timeout` and
  `final_round_trip_total_unavailable`

The report continues to redact sensitive `message_en` values. If a message is
redacted but a safe reason exists, render the reason beside the redaction, for
example:

```text
[redacted] (reason: timeout)
```

Provider notes should also include the reason in compact form:

```text
error provider_timeout: [redacted] (reason: timeout) retryable: yes
```

Do not render arbitrary `details` keys or values. Do not render raw messages,
cookies, headers, request bodies, tokens, challenge URLs, raw payload fragments,
or session data.

### Skyscanner Multi-Segment Handling

Update `cheapy/providers/skyscanner/adapter.py` so a provider leg can produce
multiple `SkyscannerLegCandidate` segments.

For each Skyscanner leg:

1. Read leg-level origin, destination, departure, arrival, duration, and
   `stopCount`.
2. Read all segment entries.
3. Require every segment to have origin, destination, departure, arrival,
   duration, carrier, and flight number.
4. Require the segment chain to start at the leg origin and end at the leg
   destination.
5. Require each segment destination to match the next segment origin.
6. Require segment dates and route values to stay internally consistent.
7. Return one `SkyscannerLegCandidate` per segment.

The itinerary extractor then validates the provider-leg groups and flattened
segment sequence against the expected route:

- one-way expected route starts at request origin and ends at request
  destination
- round-trip expected route contains an outbound chain and an inbound chain

Round-trip handling must preserve provider-leg boundaries internally until
normalization, or carry an internal inbound-start marker/date. Flattening all
segments too early is not enough: an inbound chain such as `SGN -> DOH -> DUS`
does not contain a single segment with `origin == SGN` and `destination == DUS`.
The normalizer must set `actual_return_date` from the first inbound segment's
departure date before flattening segments into Contract V1 `FlightLegV1`
entries.

Malformed, wrong-route, missing-flight-number, or broken-chain payloads remain
skipped safely. If no usable itinerary remains, the adapter raises the existing
sanitized `no_usable_results` error.

The normalizer already maps `SkyscannerLegCandidate` objects into
`FlightLegV1`; it should preserve all segment flight numbers in offer legs.
Prefer the provider leg-level duration sum for itinerary
`total_duration_minutes`, because segment duration sums exclude layovers. Fall
back to the emitted segment duration sum only when every provider leg duration
is unavailable or invalid. `stops` should reflect provider leg stop counts, with
a safe fallback to the number of intermediate segment connections.

### Public Link Safety

Skyscanner provider output must continue to set:

```python
public_search_url = None
```

User-clickable links remain attached only by:

```python
attach_public_search_urls(request, response)
```

The report continues to call `validate_public_search_url()` before turning fare
text into a Markdown link. Raw provider URLs, Skyscanner transport deeplinks,
session URLs, and challenge URLs must not enter Contract V1 output or Markdown.

## Error Handling

Provider task exceptions are sanitized by the orchestrator as today. The
orchestrator must not include exception messages in Contract V1 details.

Global budget timeout conversion should produce:

- `code = provider_timeout`
- `message_en = "<Provider label> provider timed out."`
- `details.provider`
- `details.capability`
- `details.failure_type = "timeout"`
- `retryable = true`

If a provider returns a failed result without errors, the existing synthesized
provider failure path remains. The synthesized error can include only safe
fields such as provider, capability, and provider status.

If a provider returns partial offers and errors, the response remains partial.
If all providers fail or time out, the response remains failed.

## Testing Plan

Focused tests use fake providers and fake payloads only.

Search orchestration tests:

- concurrent providers finish near the slowest provider delay, not the sum of
  delays
- returned `provider_statuses` preserve planner/provider order
- one slow or failing provider still allows offers from another provider
- shared 45-second budget converts unfinished calls to retryable timeout
  failures without cancelling completed results
- retryable failure succeeds on the second attempt
- retryable failure exhausted remains failed or partial as appropriate
- non-retryable failure is not retried
- logical provider accounting remains `1/1` after retry

Markdown report tests:

- sensitive messages still render as `[redacted]`
- safe `failure_type` appears as a reason beside redacted messages
- safe HTTP status mappings render as public categories
- arbitrary details are not rendered
- public search URL rendering remains safe and clickable

Skyscanner adapter and normalizer tests:

- fake DUS to SGN round-trip payload with connecting segments returns offers
  with segment flight numbers
- stops and durations are preserved or safely computed
- wrong-route multi-segment payloads are skipped as `no_usable_results`
- malformed segments are skipped safely
- raw deeplinks and sensitive Skyscanner tokens never appear in candidates,
  offers, provider results, or reports

Regression tests:

- `uv run pytest tests/test_search.py -v`
- `uv run pytest tests/test_markdown_report.py -v`
- `uv run pytest tests/skyscanner/test_adapter.py -v`
- `uv run pytest tests/skyscanner/test_normalizer.py -v`
- `uv run pytest tests/skyscanner/test_provider.py -v`
- `uv run pytest tests/test_mcp.py -v`
- full `uv run pytest -v`

## Acceptance Criteria

- Markdown and MCP report output shows safe provider failure reasons, not only
  opaque `[redacted]`.
- Provider calls run concurrently under one 45-second wall-clock batch budget.
- Response and provider status ordering stays stable.
- One provider timeout or failure does not block successful providers.
- Retryable timeout gets at most one in-budget retry.
- Non-retryable failures are not retried.
- Provider accounting remains logical and coherent.
- Skyscanner fake multi-segment long-haul payloads normalize into useful offers.
- Wrong-route or malformed Skyscanner segments are skipped safely.
- Public search URL behavior remains provider-scoped, safe, and clickable.
- MCP stdout remains protocol-clean.
- Browserless is not reintroduced.
- Default tests make no live network calls.
