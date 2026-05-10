# Cheapy Gate 4 Exact Search Design

## Summary

Gate 4 adds the first internal search orchestration layer for Cheapy.

The approved scope is narrow: exact one-way search only, backed by the bundled `manual_fixture` provider through the existing provider registry. Gate 4 does not add CLI search, MCP behavior, live providers, storage, expanded search, flexible dates, nearby airports, split-ticket search, or provider concurrency.

The main deliverable is an internal Python API that accepts `SearchRequestV1` and returns `SearchResponseV1` for runtime search outcomes.

## Goals

- Add an internal exact-search API.
- Convert `SearchRequestV1` into provider-local exact one-way requests.
- Load enabled bundled providers through the existing registry.
- Call providers that support `exact_one_way`.
- Convert `ProviderResult` values into Contract V1 `SearchResponseV1`.
- Preserve provider warnings and errors.
- Return deterministic fixture-backed responses for tests.
- Keep model validation errors as Pydantic validation errors.
- Return runtime search failures as `SearchResponseV1(status="failed")`.

## Non-Goals

- No `cheapy search` CLI command.
- No MCP server behavior or `search_cheapest_flights` tool behavior.
- No live provider calls.
- No provider retry, timeout, or concurrency framework.
- No expanded search.
- No round-trip search.
- No flexible-date, nearby-airport, or split-ticket planner.
- No new Contract V1 error codes.
- No storage or price history.

## Approved Approach

Create a small search orchestrator module, expected as `cheapy/search.py`, with a primary internal API:

```python
def search_exact(request: SearchRequestV1) -> SearchResponseV1:
    ...
```

The orchestrator sits between:

- Contract V1 models in `cheapy.models.contracts`
- the airport resolver in `cheapy.airports`
- provider discovery/loading in `cheapy.providers.registry`
- provider-local models in `cheapy.providers.base`

`SearchRequestV1` remains the input contract. `ProviderExactOneWayRequest` remains provider-local. `ProviderResult` remains provider-local. `SearchResponseV1` is the only output shape for completed runtime searches, including provider failures and unsupported Gate 4 capability cases.

## Components

### Search Orchestrator

The search orchestrator owns:

- scope checks for exact one-way Gate 4 requests
- airport resolution
- provider loading
- provider capability filtering
- provider invocation
- response assembly

It must be simple and synchronous at the public function boundary. Provider calls run through a private async helper that the sync API drives.

### Provider Result Mapping

Provider results are mapped into `ProviderStatusV1`:

- `provider_name` comes from the provider result
- `capability` is `exact_one_way`
- call counts are `1` for each attempted exact provider call
- success/failure counts derive from provider status
- provider warnings and errors are preserved
- `duration_ms` and `retryable` are copied from the provider result

Provider offers, warnings, and errors are also collected into the top-level response.

### Response Builder Helpers

Use private helpers to keep the module readable:

- build a deterministic request ID
- build an empty or failed search plan
- build `CurrencyGroupV1` values
- map `ProviderResult` to `ProviderStatusV1`
- create Contract V1 errors for orchestrator-level failures

These helpers remain private in Gate 4.

## Data Flow

`search_exact(request)` runs this flow:

1. Resolve `request.origin` and `request.destination` with the existing airport catalog. The resolver accepts known IATA codes only and normalizes accepted values with `strip().upper()`.
2. Check Gate 4 scope. `request.search_mode` must be `exact`, and `request.return_date` must be `None`.
3. Load enabled providers with `load_enabled_providers()`.
4. Keep providers that expose the `exact_one_way` capability.
5. Build a `ProviderExactOneWayRequest` from resolved origin IATA, resolved destination IATA, departure date, and passengers. Do not pass raw request airport strings to providers.
6. Call each exact one-way provider.
7. Convert each provider result into `ProviderStatusV1`.
8. Collect offers, warnings, and errors.
9. Sort and truncate offers by `request.max_results`.
10. Build currency groups and currency notes.
11. Return a complete `SearchResponseV1`.

For an in-scope exact one-way request, the search plan is deterministic:

- `search_mode`: the request search mode
- `planned_candidate_count`: `1`
- `executed_candidate_count`: `1`
- `planned_provider_call_count`: number of exact-capable providers
- `executed_provider_call_count`: number of exact-capable providers called
- `candidate_count_by_family`: `{exact: 1}`
- `provider_call_count_by_family`: `{exact: executed_provider_call_count}`
- `truncated`: `False`
- `truncated_families`: `[]`
- `candidate_families`: `[exact]`

For failures before a provider call, the search plan uses the request search mode, zero counts, empty family maps, `truncated=False`, and empty candidate family lists.

## Response Status Rules

- `success`: at least one provider returns offers and no provider errors are present.
- `partial`: offers are present and at least one provider error is present.
- `failed`: no offers are present, or no provider can be called.

Provider warnings do not make a response partial by themselves.

## Offer Ordering And Currency

Gate 4 does not perform currency conversion.

If all returned offers use the same currency, sort by:

1. `price_amount`
2. `offer_id`

If more than one currency appears, keep deterministic ordering without implying cross-currency comparability:

1. `currency`
2. `price_amount`
3. `offer_id`

`mixed_currency` is `True` when more than one currency appears. `currency_groups` always includes one group per returned currency and is empty only when there are no offers. `currency_notes` includes a note that no currency conversion was applied when currencies are mixed.

For the approved `manual_fixture` acceptance path, the response contains VND offers only, `mixed_currency=False`, one VND currency group containing both offer IDs, and no currency notes.

## Error Handling

Contract/model shape errors are not swallowed. Invalid data used to construct or parse `SearchRequestV1` continues to raise Pydantic validation errors before or during caller validation.

Runtime search failures return `SearchResponseV1(status="failed")`.

### Airport Not Found

If origin or destination cannot be resolved, return an `AIRPORT_NOT_FOUND` error with:

- `severity="error"`
- `retryable=False`
- `details.field`: `origin` or `destination`
- `details.value`: the rejected value

No provider call is attempted.

### Unsupported Gate 4 Scope

If the request is outside Gate 4 capability, return `NO_PROVIDER_AVAILABLE`.

Examples:

- `search_mode="expanded"`
- `return_date` is not `None`

The error details include `unsupported_reason`. This avoids adding a new Contract V1 code before the contract needs one.

### No Provider Available

If no enabled provider is loaded, or no enabled provider supports `exact_one_way`, return `NO_PROVIDER_AVAILABLE`.

If `load_enabled_providers()` raises `ProviderManifestError` or `ProviderLoadError`, return `NO_PROVIDER_AVAILABLE`. The error details include `registry_error_type` and do not include a traceback. The orchestrator treats registry load failures as runtime search failures because no usable provider set can be constructed.

### Provider-Level Failure

If a provider returns a failed `ProviderResult`, preserve its errors in both the provider status and the top-level response.

### Unexpected Provider Exception

If a provider raises unexpectedly, convert that exception into:

- a failed `ProviderStatusV1`
- a top-level `PROVIDER_FAILED` error

The response must not include a traceback. Error details include the provider name and exception type only; they do not include exception messages, secrets, or raw provider payloads.

## Request ID

Gate 4 request IDs are deterministic to keep tests stable. The format is:

```text
exact:{origin}:{destination}:{departure_date}:{search_mode}:{adults}:{children}:{infants_on_lap}:{infants_in_seat}:{max_results}
```

`origin` and `destination` in the request ID use the resolved IATA values, not the raw request strings.

No randomness is needed in Gate 4.

## Tests

Add focused tests for the internal search API.

Expected test coverage:

- successful `manual_fixture` exact one-way search returns `SearchResponseV1(status="success")`
- successful response contains two VND offers for `CXR` to `SGN` on `2026-07-10`
- offers are sorted and respect `max_results`
- VND-only success includes one `CurrencyGroupV1(currency="VND")` with returned offer IDs
- `search_plan` contains exact-family counts only
- `provider_statuses` contains one successful `manual_fixture` status
- unsupported route/date returns `status="failed"` and preserves the provider error
- unknown airport returns `AIRPORT_NOT_FOUND` without provider calls
- expanded search returns failed response with `NO_PROVIDER_AVAILABLE`
- round-trip request returns failed response with `NO_PROVIDER_AVAILABLE`
- no enabled providers returns failed response with `NO_PROVIDER_AVAILABLE`
- registry manifest/load errors return failed response with `NO_PROVIDER_AVAILABLE`
- provider exception returns failed response with `PROVIDER_FAILED`
- mixed-currency grouping uses a fake provider result

Default verification remains:

```bash
uv run pytest -v
```

Focused verification includes the new search tests:

```bash
uv run pytest tests/test_search.py -v
```

## Acceptance Criteria

Gate 4 is complete when:

- `search_exact(SearchRequestV1(...))` returns a valid `SearchResponseV1`
- the fixture request `CXR` to `SGN` on `2026-07-10` returns the two deterministic manual fixture offers
- runtime failures return `SearchResponseV1(status="failed")`
- provider failures and warnings are preserved
- exact search plan accounting is deterministic
- currency groups are deterministic
- provider requests and request IDs use resolved IATA values
- no CLI search command is added
- MCP remains outside scope
- no network calls are introduced
- `uv run pytest -v` passes

## Deferred Work

Deferred to later gates:

- CLI `cheapy search`
- MCP `search_cheapest_flights`
- expanded search orchestration
- flexible-date planner
- nearby-airport planner
- split-ticket planner
- provider timeout and retry policy
- provider concurrency limits
- official API provider integration
