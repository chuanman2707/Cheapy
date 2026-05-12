# Cheapy Master Spec Design

Date: 2026-05-08

## Purpose

Cheapy is a Python package and stdio MCP server for agent-driven cheap flight search.

The main user experience is not a standalone UI. A user chats with an agent such as Codex or Claude. The agent reads Cheapy's local skill/instructions, turns the user's natural-language request into a structured MCP tool call, then Cheapy searches providers and returns normalized results.

Example flow:

```text
User:
Find the cheapest one-way flight from Nha Trang to Saigon on July 10.

Agent:
- Uses Cheapy's local skill.
- Normalizes the route and date when confident.
- Calls Cheapy's MCP tool.

Cheapy:
- Searches all loaded providers.
- Runs the requested search mode.
- Deduplicates and sorts results.
- Returns the cheapest candidates.

Agent:
- Explains the best results to the user in the user's language.
```

The project now optimizes for the fastest useful MCP prototype release. The priority is not an end-user CLI search workflow. A prototype is successful when a user can install Cheapy, add it as an MCP server to Codex or Claude, and ask the agent to call Cheapy's MCP tool for a flight search.

The prototype release path is:

1. install the `cheapy-flights` package
2. register the local stdio MCP server with an agent client
3. provide project-local agent instructions for Codex and Claude
4. expose `search_cheapest_flights` over MCP
5. route the MCP tool to Cheapy's internal exact-search orchestrator
6. return `SearchResponseV1` results from the packaged provider stack

The CLI exists only to operate the MCP product: start the server, install/configure MCP client entries, run doctor checks, and inspect providers. `cheapy search` is deferred.

## Phase 1 Scope

Phase 1 includes:

- Python distribution name: `cheapy-flights`
- Python import package: `cheapy`
- CLI command: `cheapy` for operational commands only
- Python 3.12+
- `uv`, Pydantic, Typer, pytest
- stdio MCP server only
- one high-level MCP search tool: `search_cheapest_flights`
- MCP tool wiring to the internal exact-search orchestrator
- project-local Codex skill file
- minimal CLI for MCP install and provider checks
- explicit `cheapy doctor` command
- bundled airport snapshot
- provider registry that discovers provider manifests with packaged resources
- first provider: `manual_fixture`
- exact one-way search for the prototype route/date fixtures
- installable wheel/package behavior
- no storage layer

Phase 1 does not include:

- SQLite
- price history
- watchlists
- recurring scheduler
- Telegram alerts
- HTTP MCP transport
- end-user CLI search UX
- `cheapy search`
- official/live provider integration
- `google_fli`
- expanded search mode with flexible-date expansion
- expanded search mode with nearby airport expansion
- split-ticket search on the original route/date
- baggage allowance modeling
- refund/change rule modeling
- fare brand modeling
- non-economy cabin classes
- stop filters
- raw provider payloads in MCP responses

After the MCP prototype release, the next product decision is whether to add an official/live provider first or broaden search planning first. Traveloka remains research-only and disabled by default until the safe MCP path and an official/live provider path are stable.

## User And Agent Flow

Cheapy expects the agent to handle natural language. Cheapy handles flight-search planning and result processing.

The agent is responsible for:

- deciding when the user is asking for flight search
- asking follow-up questions when required inputs are missing
- normalizing dates to `YYYY-MM-DD`
- normalizing airports to IATA codes when confident
- calling `search_cheapest_flights`
- explaining structured Cheapy results to the user

Cheapy is responsible for:

- validating structured input
- validating IATA codes against the packaged airport catalog
- planning search candidates supported by the current release
- loading all provider modules available in the package
- calling providers with bounded concurrency and timeouts
- normalizing provider results
- deduplicating offers
- sorting candidates by price when comparable
- returning structured offers, flags, warnings, and failure states

## Project-Local Codex Skill

`cheapy mcp install --client codex` must not copy files into a global Codex skill folder.

For phase 1, the installer creates or verifies a project-local OpenAI-compatible skill file:

```text
.codex/skills/cheapy/SKILL.md
```

This keeps the Cheapy skill tied to the project. If the user deletes the project folder, the skill goes away with it.

The skill must teach Codex:

- when to call Cheapy
- what required information is needed
- how to ask for missing origin, destination, departure date, and return date
- to use IATA codes when confident
- to ask for clarification instead of passing non-IATA airport text
- to normalize dates before tool calls
- to call only the high-level `search_cheapest_flights` tool
- to use exact mode when the user asks for a specific fixed trip
- to treat expanded/flexible requests as deferred until Cheapy exposes expanded search
- not to ask the user to choose providers
- to explain nearby airports, date offsets, split tickets, self-transfer, and long connections clearly
- to treat mixed-currency comparisons as estimates if it uses Wise Currency Converter

The installer must not assume Codex automatically discovers project-local skill folders. It must also create or update a project-level `AGENTS.md` hook that points Codex to `.codex/skills/cheapy/SKILL.md`, while preserving existing `AGENTS.md` content. If the installer cannot safely update `AGENTS.md`, it must print the exact manual instruction and report that skill activation is incomplete.

For Claude, phase 1 installs the MCP server entry. It writes a project-local instruction file at:

```text
.cheapy/claude-instructions.md
```

The installer should create or update a project `CLAUDE.md` import/reference to `.cheapy/claude-instructions.md` when it can do so safely. If not, it prints the path and the exact manual instruction the user should add.

## MCP Design

Phase 1 supports stdio only:

```bash
cheapy mcp
```

No HTTP MCP server is included in phase 1.

The MCP stdio process must keep stdout protocol-clean. Only JSON-RPC MCP messages may go to stdout. Logs, warnings, progress, provider diagnostics, Typer output, and accidental provider prints must go to stderr or be suppressed. The MCP entrypoint must be separate from user-facing CLI output.

The primary MCP tool is:

```text
search_cheapest_flights
```

There is no `get_price_history` tool in phase 1 because storage is out of scope.

MCP responses, warnings, and error messages use English. The agent translates or explains them to the user.

Cheapy should also expose MCP prompts/resources for agents that support them:

- `cheapy://guides/usage`
- `cheapy://guides/airport-normalization`
- `cheapy://guides/missing-information`
- `cheapy://guides/result-interpretation`
- `cheapy://guides/mixed-currency`
- `cheapy://status/providers`

MCP prompts should use stable names:

- `cheapy.missing_information`
- `cheapy.result_interpretation`
- `cheapy.mixed_currency_guidance`

These resources are supporting material. The project-local Codex skill is the primary instruction mechanism for Codex.

## CLI Design

The phase 1 CLI is minimal:

```bash
cheapy mcp
cheapy mcp install --client codex
cheapy mcp install --client claude
cheapy doctor
cheapy providers list
cheapy providers test
```

There is intentionally no `cheapy search` command in the MCP prototype. Search is exposed through MCP first so the product validates the real user workflow: an agent calls `search_cheapest_flights` and explains the structured result. A future CLI search command may be added as a developer/debug harness, but it is not required for prototype release.

`cheapy mcp install --client codex|claude` must:

- prefer the official client CLI when available, such as `codex mcp add`
- fall back to direct config editing only when the client CLI is unavailable
- create a timestamped backup before direct config edits
- preserve unrelated config during fallback edits
- avoid reserializing unrelated config when possible
- use file locks or atomic writes for direct config edits
- set backup/config permissions to `0600` when the platform supports it
- add or update the Cheapy MCP server entry idempotently
- print the changed config path
- print the backup path

The installer must resolve the executable path at install time. The preferred MCP server entry uses the absolute installed `cheapy` command:

```json
{
  "command": "/absolute/path/to/cheapy",
  "args": ["mcp"]
}
```

If the installed command cannot be resolved, the installer must fail with a clear message telling the user to install `cheapy-flights` first. A later release may support `uvx` or repo-local development commands, but phase 1 targets installed-command use.

`cheapy doctor` is a first-class phase 1 command. It checks:

- the resolved Cheapy executable path
- whether the MCP server can start
- stdout protocol cleanliness
- provider manifest discovery from the installed package
- project-local Codex skill hook status
- Claude instruction hook status
- MCP client config entry status when the client is installed
- backup/config file permissions when applicable

`cheapy providers test` is separate. It checks packaged provider health. After a live provider exists, it may also run opt-in live provider smoke tests.

Fallback direct config edits must use managed blocks or equivalent idempotent markers where the target file format allows it. The fallback editor must parse before writing, write a temp file on the same filesystem, then use an atomic replace. If writing fails, it must leave the original config untouched and print the backup/rollback path.

## Contract V1

Phase 1 must freeze an explicit MCP contract before implementation work starts. The public tool contract is versioned as `SearchRequestV1` and `SearchResponseV1`.

All request and response models should use strict Pydantic validation for scalar fields, enums, dates, passenger counts, and booleans. The implementation should reject ambiguous coercions instead of silently converting strings such as `"1"` or `"false"`.

### SearchRequestV1

`search_cheapest_flights` accepts one request object with these fields:

```text
schema_version: "1"
origin: string
destination: string
departure_date: YYYY-MM-DD
return_date: YYYY-MM-DD | null
search_mode: exact | expanded, default exact
passengers:
  adults: integer >= 1, default 1
  children: integer >= 0, default 0
  infants_on_lap: integer >= 0, default 0
  infants_in_seat: integer >= 0, default 0
max_results: integer >= 1 and <= 20, default 5
```

Phase 1 does not expose provider selection in the request. Cheapy loads all available providers that support the request.

Phase 1 does not expose cabin class. Economy is implied.

Phase 1 does not expose stop filters. Any-stop search is implied.

Dates are local travel calendar dates. The agent must resolve natural-language dates to `YYYY-MM-DD` before calling the tool. If the year is missing and cannot be inferred safely from the conversation, the agent must ask the user.

Example one-way request:

```json
{
  "schema_version": "1",
  "origin": "CXR",
  "destination": "SGN",
  "departure_date": "2026-07-10",
  "return_date": null,
  "search_mode": "exact",
  "passengers": {
    "adults": 1,
    "children": 0,
    "infants_on_lap": 0,
    "infants_in_seat": 0
  },
  "max_results": 5
}
```

Example round-trip request:

```json
{
  "schema_version": "1",
  "origin": "SGN",
  "destination": "BKK",
  "departure_date": "2026-07-10",
  "return_date": "2026-08-08",
  "search_mode": "exact",
  "passengers": {
    "adults": 1,
    "children": 0,
    "infants_on_lap": 0,
    "infants_in_seat": 0
  },
  "max_results": 5
}
```

### SearchResponseV1

The response has one source of truth for offers:

```text
offers
```

`currency_groups` is a derived grouping view over `offers`. It must not contain independent offers that are absent from `offers`.

Top-level response fields:

```text
schema_version: "1"
status: success | partial | failed | needs_clarification
request_id: string
offers: list[FlightOfferV1]
warnings: list[WarningV1]
errors: list[ErrorV1]
provider_statuses: list[ProviderStatusV1]
search_plan: SearchPlanV1
mixed_currency: boolean
currency_groups: list[CurrencyGroupV1]
currency_notes: list[string]
candidates: list[AirportCandidateV1] | null
```

`candidates` is only populated for `needs_clarification`. It must be present in the schema even when null.

`search_mode` and `truncated` live inside `search_plan`, not as separate top-level source-of-truth fields.

When mixed currencies appear, each offer must include:

```text
comparable: false
rank_within_currency: integer | null
global_rank: null
```

When all returned offers share one currency, Cheapy may set:

```text
comparable: true
rank_within_currency: integer
global_rank: integer
```

### Warning And Error Codes

Warnings and errors are machine-readable first and human-readable second.

Each warning/error includes:

```text
code
severity: info | warning | error
message_en
details
retryable: boolean
```

Phase 1 warning/error codes must include at least:

- `mixed_currency`
- `search_truncated`
- `candidate_family_truncated`
- `provider_failed`
- `provider_timeout`
- `provider_rate_limited`
- `provider_blocked`
- `no_provider_available`
- `airport_ambiguous`
- `airport_not_found`
- `fare_details_not_collected`
- `split_ticket`
- `self_transfer`
- `nearby_airport_used`
- `flexible_date_used`

### ProviderStatusV1

Each provider status includes:

```text
provider_name
capability
status: success | partial | failed | skipped
planned_call_count
executed_call_count
succeeded_call_count
failed_call_count
duration_ms
warnings
errors
retryable
```

Provider status codes must be stable enough for tests to assert codes rather than free-form text.

## Search Modes

`search_cheapest_flights` uses the V1 `search_mode` field:

```text
exact
expanded
```

The MCP prototype supports `exact` only. Exact mode searches the requested airports and requested dates only. This is the safest mode for fixed-trip requests and is the release-critical path.

`expanded` remains in the V1 contract for forward compatibility, but expanded search is deferred. Until the expanded planner is implemented, an expanded request returns a structured failed response instead of attempting flexible dates, nearby airports, or split-ticket search.

If `search_mode` is omitted, Cheapy uses `exact`. The agent instructions for the prototype should call exact mode for fixed-trip requests and explain that flexible/expanded search is not available yet.

In the long-term expanded search design, Cheapy tries:

1. exact date and requested airports
2. flexible dates around the requested date, only in expanded mode
3. nearby airports, only in expanded mode
4. split-ticket candidates when the route is long enough, only in expanded mode

Default limits:

- top results: 5
- flexible date window: plus/minus 3 days
- nearby airport radius in expanded mode: 300 km
- maximum nearby airports per side: 3
- maximum split hubs: 3
- minimum split connection: 300 minutes
- long connection threshold: 480 minutes
- skip hub split under direct distance: 1200 km
- provider call timeout: 30 seconds
- full request timeout: 120 seconds
- maximum provider calls per request: 20

Cheapy must always return the executed search plan:

```text
search_mode
planned_candidate_count
executed_candidate_count
planned_provider_call_count
executed_provider_call_count
candidate_count_by_family
provider_call_count_by_family
truncated
truncated_families
candidate_families
```

Budget accounting is based on provider invocations, not only search candidates. A search candidate may produce zero or more provider calls depending on provider capability. The request budget of 20 applies to `executed_provider_call_count`.

Candidate family order is stable:

```text
exact
flexible_dates
nearby_origin
nearby_destination
split_ticket
```

The planner computes all candidates first, estimates provider calls for each candidate, then selects candidates until the provider-call budget is exhausted. The response must show both the planned and executed counts so the agent can explain what was skipped.

When planned candidates exceed the provider-call cap, Cheapy keeps candidates in this order:

1. exact date with requested airports
2. flexible dates with requested airports
3. nearby airport candidates
4. split-ticket candidates

For round trips with flexible dates, Cheapy searches the exact departure and return pair first, then date offsets closest to the original dates.

Nearby airport expansion after the prototype includes:

- requested origin to requested destination
- nearby origin to requested destination
- requested origin to nearby destination

It does not include nearby origin to nearby destination in the first expanded-search release.

Split-ticket search is deferred. In its first release, it should use only the original airports and original dates. It does not combine split-ticket search with nearby airports or flexible dates.

If expansion is truncated, Cheapy must return `truncated = true` and list which candidate families were dropped or partially executed.

Concurrency defaults:

- maximum concurrent provider calls per request: 5
- maximum concurrent calls per provider: 2

Timeout defaults:

- provider call timeout: 30 seconds
- request timeout: 120 seconds

Retry defaults:

- no retry for validation errors, airport errors, provider blocked responses, or rate-limit responses
- at most one retry for transient network errors
- retry uses bounded exponential backoff with jitter
- retries count toward the provider-call budget

## Ranking And Currency

The default ranking goal is absolute cheapest first.

When all offers use the same currency, Cheapy sorts by price ascending.

When a response contains mixed currencies:

- Cheapy does not convert currencies.
- Cheapy sets `mixed_currency = true`.
- Cheapy still returns structured candidate offers.
- Cheapy sorts offers inside each currency group by raw amount.
- Cheapy keeps currency groups separate in the response so the agent does not mistake raw amounts in different currencies for a final ranking.
- The agent skill instructs the agent to use Wise Currency Converter if it needs an estimated comparison.
- The agent must tell the user that converted values are estimates.

Cheapy must not claim a final cross-currency cheapest winner unless currencies are comparable.

## Flight Model

Phase 1 supports economy only.

Passenger schema:

```text
adults: integer >= 1
children: integer >= 0
infants_on_lap: integer >= 0
infants_in_seat: integer >= 0
```

If the user does not specify passengers, the agent should call Cheapy with:

```text
adults = 1
children = 0
infants_on_lap = 0
infants_in_seat = 0
```

Cheapy does not support stop filters in phase 1. It searches with any number of stops and returns stop/leg details so the agent can explain them.

Cheapy does not model baggage, refund rules, change rules, or fare brands in phase 1. Because those details are not collected, offers should expose a simple `fare_details_status = "not_collected"` field so the agent does not imply that baggage or refund details were checked.

## Offer Flags

Phase 1 does not include risk scoring or risk levels.

Each offer should include simple flags:

- `is_split_ticket`
- `is_self_transfer`
- `uses_nearby_origin`
- `uses_nearby_destination`
- `uses_flexible_departure_date`
- `uses_flexible_return_date`
- `has_long_connection`
- `has_overnight_connection`
- `has_many_stops`
- `baggage_unknown`

Nearby airport offers must include the requested airport, actual airport, and distance from the requested airport.

Flexible-date offers must include the requested date, actual date, and offset in days.

## Provider System

Providers live inside the `cheapy` package:

```text
cheapy/providers/
  registry.py
  manual_fixture/
    manifest.toml
    provider.py
```

Each provider has a manifest file. Example:

```toml
manifest_schema_version = "1"
name = "manual_fixture"
display_name = "Manual fixture provider"
default_enabled = true
capabilities = ["exact_one_way"]
```

At runtime, Cheapy:

1. discovers manifests with `importlib.resources`, not raw filesystem assumptions
2. validates each manifest
3. lazy-imports the provider implementation only when needed
4. checks provider capabilities
5. calls all providers that support the required search capability

Manifest discovery must not use user-controlled paths or names. Provider resource names come only from package metadata and bundled resources.

The user and agent do not choose a provider during normal search. Cheapy uses all loaded providers.

The provider interface is async-only. Provider internals may use sync transport if needed, but they must expose async methods to core.

Core does not require a specific HTTP client. The prototype `manual_fixture` provider does not use HTTP and must never make live network calls.

Provider capabilities must be typed enough for the planner to make safe decisions. A provider manifest should distinguish at least:

- exact one-way support
- exact round-trip support
- flexible date support
- supported passenger fields
- supported cabin classes
- whether returned currency is raw/provider-controlled

Each provider must pass shared provider contract tests for every capability it advertises.

## Live Provider Deferred

The prototype release does not include a live provider. The first live provider is deferred until after the MCP install, server, tool, and agent-instruction path works end to end.

The preferred live-provider direction is an official or documented API integration. A `google_fli` style provider can be researched later, but it must remain disabled or research-only until its access, safety, and maintenance assumptions are clear.

When a live provider is added, provider-specific request payload building, response parsing, and normalization must stay inside that provider package. Provider-specific models must not become core models. For a future `google_fli` research provider, that package would be:

```text
cheapy/providers/google_fli/
```

The provider should be split into:

- request builder
- transport/client wrapper
- response parser
- normalizer
- provider class

The goal is to preserve the separation between MCP, core search, and provider logic while keeping the prototype release small.

## Airport Data

Phase 1 uses a bundled generated airport snapshot committed into the repo/package. Runtime does not depend on fetching airport data.

The snapshot must include:

- IATA code
- airport name
- city or municipality when available
- country
- latitude
- longitude

Cheapy follows the useful idea from `fli` of generating airport data, but it cannot rely only on `fli`'s generated `airports.csv` because that file lacks latitude and longitude.

Airport resolution rules:

1. Exact IATA code wins.
2. Exact airport name match wins.
3. Exact city match with one airport uses that airport.
4. Exact city match with multiple airports returns `needs_clarification` with candidates.
5. Phase 1 does not auto-pick a primary airport for multi-airport cities.

The agent should still normalize common city names to IATA codes when confident.

## Data Governance

Airport and hub data are product dependencies, not incidental test fixtures.

The airport snapshot must include:

- source name
- source URL or package name
- source license
- generation date
- generator script version
- snapshot schema version

The repo should include the offline generator script or clear reproduction notes. Runtime uses the bundled snapshot and does not fetch airport data.

The hub candidate source must be bounded and versioned. It may be curated manually or generated offline from airport metadata, but it must include provenance:

- source
- generation method
- version
- last updated date

If the hub source is missing, stale, or invalid, split-ticket hub search is skipped with a structured warning.

Provider data governance:

- `manual_fixture` is the prototype provider.
- live providers are deferred until after the MCP prototype path works end to end.
- Provider raw responses are not product data.
- Parser fixtures used in tests must be redacted and must include fixture provenance notes.
- Provider access assumptions, required environment variables, and unsupported provider states must be documented in the provider package.

Secrets policy:

- Phase 1 does not require stored secrets in Cheapy core.
- If any provider later needs credentials, secrets must come from environment variables or OS-level secret storage.
- Secrets must not be written to source, config backups, logs, fixtures, debug artifacts, or MCP responses.

Sensitive operational data:

- route
- dates
- passenger composition
- provider statuses

Default logs must avoid raw request payloads. Logs should use request IDs, provider names, status codes, durations, counts, and warning/error codes.

## Search Orchestration

Cheapy's search orchestrator is inside Cheapy, not the agent.

The MCP prototype uses the current internal exact-search orchestrator. It supports exact one-way requests and maps runtime search outcomes into `SearchResponseV1`.

The long-term orchestrator:

- receives the structured request
- resolves airports
- creates search candidates
- applies the request-wide call cap
- calls provider capabilities
- merges provider results
- deduplicates offers
- sorts same-currency results by price and keeps mixed-currency groups separate
- returns the final structured response

Planner responsibilities should remain separate as they are added:

- exact-date planner
- flexible-date planner
- nearby-airport planner
- split-ticket planner

This keeps the split-ticket logic from getting mixed with nearby and flexible-date logic after the prototype release.

The split-ticket planner is deferred. When it is added, it must not choose hubs from the entire airport snapshot by distance alone. It should use a small tiered hub candidate source bundled with the airport data. The hub source may be curated or generated offline from airport metadata, but runtime hub selection must only rank candidates from that bounded hub set. If no suitable hub source is available, Cheapy should skip hub split and return a warning instead of running distance-only hub search.

## Deduplication

Cheapy deduplicates top results.

Within the same provider, Cheapy can deduplicate by itinerary signature:

- provider
- actual origin
- actual destination
- actual dates
- airline codes
- flight numbers
- departure and arrival times
- ticketing strategy

When duplicates are found, Cheapy keeps the cheapest duplicate.

Across providers, Cheapy only merges offers when fare details are sufficiently similar:

- same flights and times
- same cabin
- same passenger count
- same known fare details
- same ticketing strategy

Unknown fare details must block cross-provider merge. If `fare_details_status = "not_collected"` for either offer, Cheapy may deduplicate within the same provider by exact itinerary signature, but it must not merge offers across providers unless every comparable fare field is known and equal.

Otherwise Cheapy shows them separately because provider terms and fare details may differ.

## Response Shape

`search_cheapest_flights` returns `SearchResponseV1` as defined in Contract V1.

The canonical top-level response fields are:

- `status`: `success`, `partial`, `failed`, or `needs_clarification`
- `schema_version`
- `request_id`
- `offers`
- `warnings`
- `errors`
- `provider_statuses`
- `search_plan`
- `mixed_currency`
- `currency_groups`
- `currency_notes`
- `candidates`

Each offer includes:

- offer ID
- price amount
- currency
- comparable
- rank within currency
- global rank when comparable
- provider
- requested origin and destination
- actual origin and destination
- nearby airport distance when applicable
- requested departure date
- actual departure date
- departure offset days
- requested return date when applicable
- actual return date when applicable
- return offset days when applicable
- flight legs
- total duration
- stops
- offer flags
- fare details status

`offers` is the source of truth. `currency_groups` is derived from `offers`.

Cheapy never returns raw provider payloads through MCP.

Warnings and errors must be structured. They should include:

- `code`
- `severity`
- `message_en`
- `details`
- `retryable`

## Failure Behavior

The MCP prototype uses the packaged fixture provider intentionally. After a live provider is added, there is no fixture fallback during live search unless the fixture provider is explicitly selected for testing.

If every provider call fails, Cheapy returns:

```text
status = failed
offers = []
warnings = provider error summaries
```

If some provider or sub-search calls succeed and others fail, Cheapy returns:

```text
status = partial
offers = successful normalized offers
warnings = failed provider/sub-search summaries
```

If route or airport input is ambiguous, Cheapy returns:

```text
status = needs_clarification
offers = []
candidates = possible airport matches
```

## Logging

Phase 1 uses JSON structured logs to stderr.

Logs should include:

- request ID
- provider
- capability
- status
- duration in milliseconds
- warning codes
- planned/executed provider-call counts
- truncated families

Logs must not include:

- raw provider responses
- full raw request payloads
- secrets
- config file contents
- debug artifact contents

Log values must be sanitized to avoid control characters, line breaks, and delimiter injection.

Raw provider payloads are never returned through MCP and never logged by default. For parser debugging, Cheapy may support an opt-in local debug artifact mode that writes redacted provider fixtures outside the MCP response. This mode must be off by default and clearly marked as local debugging, not product storage.

Debug artifact policy:

- opt-in only
- local filesystem only
- redacted before writing
- no secrets
- no config backups
- no passenger names or personal documents
- retention defaults to manual cleanup unless a TTL is explicitly configured
- artifact path is printed to stderr, not returned in MCP results

## Testing

Phase 1 tests are organized into lanes:

```text
contract
integration
packaging
protocol
security
live
```

The default test suite runs:

```text
contract + integration + packaging + protocol
```

The live lane is deferred until a live provider exists. Once added, it is opt-in only.

`pytest` custom markers must be registered, and CI should run with strict marker checking.

Phase 1 tests include:

- model validation tests
- strict schema validation tests for `SearchRequestV1` and `SearchResponseV1`
- schema compatibility tests for response evolution
- airport resolver tests
- airport distance tests
- planner tests
- deterministic provider-call budget and truncation tests
- orchestrator tests
- deduplication tests
- cross-provider unknown fare detail merge-blocking tests
- provider parser fixture tests
- parser fixture provenance tests
- exact MCP tool tests
- MCP tool shape tests
- CLI install behavior tests with temporary config files
- MCP stdio protocol-cleanliness tests
- wheel/package-data test for provider manifest discovery
- redaction tests for logs and debug artifacts
- installer rollback tests for failed config edits

The default test suite does not run live provider calls. After a live provider exists, live provider smoke tests run through an explicit marker or environment flag, and `cheapy providers test` may run them manually.

Future opt-in live provider smoke tests use:

```text
route: SGN -> BKK
trip type: one-way
departure date: current date + 30 days
```

The live smoke test must assert structure, not exact prices:

- provider call returns a structured status
- parser does not crash
- normalized offers are valid if results exist
- provider failure is structured if live search fails

## Pre-Implementation Gates

Implementation should not begin until these V1 decisions are frozen in the spec:

1. `SearchRequestV1` and `SearchResponseV1` schema details
2. warning, error, and provider-status code enums
3. provider-call budget semantics
4. concurrency and timeout layering
5. airport snapshot provenance
6. hub candidate source provenance
7. installer managed-block and rollback behavior
8. debug artifact redaction policy
9. test lane marker names and default test command

These gates prevent rework across the agent skill, MCP tool contract, orchestrator, provider tests, and CLI installer.

## Open Implementation Notes

The implementation plan should decide exact class names, file names, and Pydantic model names.

It should preserve the product decisions in this spec:

- one high-level MCP search tool
- agent-first natural language handling
- project-local Codex skill
- provider manifest discovery with packaged resources
- `manual_fixture` as the prototype provider
- live providers deferred until after the MCP prototype path works
- no storage in phase 1
- exact mode by default
- expanded mode deferred until after the MCP prototype release
- strict call/time caps
- simple flags instead of risk scoring
- `cheapy doctor` as a first-class command
- no `cheapy search` command in the MCP prototype
- V1 request/response contracts as the implementation source of truth
