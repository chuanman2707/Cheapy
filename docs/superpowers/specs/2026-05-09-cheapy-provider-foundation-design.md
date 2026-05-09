# Cheapy Provider Foundation Design

Date: 2026-05-09

## Purpose

Gate 3 adds the first provider foundation for Cheapy.

In this project, a provider means a source Cheapy can ask for flight offers. Gate 3 does not connect to real websites or APIs. It only creates the local shape that later real providers must follow.

The goal is to prove three things:

- Cheapy can find packaged providers.
- A packaged provider can describe itself through a manifest.
- A packaged provider can return fixed sample offers that match the existing Contract V1 offer models.

## Scope

Gate 3 includes:

- a new `cheapy.providers` package
- a small provider base contract
- provider manifest loading
- provider registry discovery through package resources
- one default provider named `manual_fixture`
- fixed sample offers from `manual_fixture`
- `cheapy providers list`
- `cheapy providers test`
- provider foundation tests
- CLI tests for the new provider commands

Gate 3 excludes:

- real MCP search behavior
- any change that makes `cheapy mcp` start a real MCP server
- search orchestration from `SearchRequestV1` to `SearchResponseV1`
- live provider calls
- `google_fli`
- Traveloka research
- storage
- network access
- secrets or credentials

## Design Decisions

The approved Gate 3 approach is provider foundation plus a deterministic fixture provider.

`manual_fixture` is enabled by default because it is bundled, deterministic, and never calls the network. It exists so tests and CLI checks can prove provider behavior without needing a live integration.

Gate 3 intentionally does not return full `SearchResponseV1` objects from provider calls. That translation belongs to a later orchestrator gate. Provider calls return provider-level data, mainly valid `FlightOfferV1` objects plus controlled warning or error information.

Gate 3 intentionally establishes `exact_one_way` as the stable first capability string. The master spec used `exact_search` as an early example, but Gate 3 uses the narrower name because the fixture provider only supports one-way exact-date searches.

## Code Layout

Gate 3 adds:

```text
cheapy/providers/
  __init__.py
  base.py
  registry.py
  manual_fixture/
    __init__.py
    manifest.toml
    provider.py
```

`base.py` defines the minimum shape provider implementations must follow.

`registry.py` discovers packaged providers and reads their manifests. It must use package resources instead of local filesystem assumptions.

`manual_fixture/manifest.toml` describes the fixed local provider.

`manual_fixture/provider.py` returns the sample offers.

## Minimum Provider Shape

Gate 3 provider implementations use an async interface. The implementation plan may choose exact class names, but the behavior is fixed:

- expose the provider name
- expose the advertised capabilities
- accept an exact one-way fixture request
- return a provider-level result

The exact one-way fixture request must include:

- `origin`
- `destination`
- `departure_date`
- passenger counts

The provider-level result must include:

- provider name
- capability name
- status
- `offers`
- warnings
- errors

For a supported fixture request, `manual_fixture` returns valid `FlightOfferV1` objects. For unsupported input, it returns a controlled provider-level failure.

Gate 3 adds these provider-local models in `cheapy.providers.base`:

```text
ProviderExactOneWayRequest
ProviderResult
```

`ProviderExactOneWayRequest` has:

- `origin`
- `destination`
- `departure_date`
- `passengers`

`passengers` reuses the existing `PassengersV1` model.

`ProviderResult` has:

- `provider_name: str`
- `capability: str`
- `status: ProviderStatusCode`
- `offers: list[FlightOfferV1]`
- `warnings: list[WarningV1]`
- `errors: list[ErrorV1]`
- `duration_ms: int`
- `retryable: bool`

`ProviderResult` reuses the existing Contract V1 models:

- `ProviderStatusCode`
- `FlightOfferV1`
- `WarningV1`
- `ErrorV1`

`ProviderStatusV1` is not returned directly by provider calls in Gate 3. A later orchestrator gate will convert one or more `ProviderResult` values into `ProviderStatusV1` and `SearchResponseV1`.

For unsupported fixture input, `manual_fixture` returns:

- `status = ProviderStatusCode.FAILED`
- `offers = []`
- `warnings = []`
- one `ErrorV1`

The unsupported-input error uses:

- `code = ErrorCode.PROVIDER_FAILED`
- `severity = Severity.ERROR`
- `message_en = "No manual fixture exists for the requested route/date."`
- `details.provider = "manual_fixture"`
- `details.capability = "exact_one_way"`
- `details.origin`
- `details.destination`
- `details.departure_date`
- `retryable = false`

## Provider Manifest

Each provider has a `manifest.toml` file.

The Gate 3 manifest fields are:

- `manifest_schema_version`
- `name`
- `display_name`
- `default_enabled`
- `module`
- `capabilities`

For `manual_fixture`, the values are:

- `manifest_schema_version = "1"`
- `name = "manual_fixture"`
- `display_name = "Manual fixture provider"`
- `default_enabled = true`
- `module = "cheapy.providers.manual_fixture.provider"`
- `capabilities = ["exact_one_way"]`

Registry discovery must only use bundled provider package names. It must not accept user-controlled provider paths.

## Manual Fixture Provider

`manual_fixture` returns fixed sample offers for:

```text
origin: CXR
destination: SGN
departure_date: 2026-07-10
return_date: null
passengers: 1 adult, no children, no infants
```

The provider returns two valid `FlightOfferV1` objects:

- a cheaper direct flight
- a second direct flight at a different time or price

Both offers use:

- `provider = "manual_fixture"`
- `fare_details_status = "not_collected"`
- `flags.baggage_unknown = true`
- one `FlightLegV1`
- same-currency pricing

If the provider receives a route or date outside its fixed fixture, it returns a controlled provider-level failure. It must not invent offers and must not attempt a network fallback.

## CLI Behavior

Gate 3 adds a provider command group:

```text
cheapy providers list
cheapy providers test
```

`cheapy providers list` prints JSON by default. It lists bundled providers with:

- provider name
- display name
- default enabled state
- capabilities

`cheapy providers test` prints JSON by default. It loads enabled bundled providers and checks that `manual_fixture` can return valid sample offers.

The provider commands may support `--human` if the implementation plan chooses to mirror `cheapy doctor`, but JSON output is the default and required behavior.

`cheapy providers list` success output is:

```json
{
  "status": "ok",
  "providers": [
    {
      "name": "manual_fixture",
      "display_name": "Manual fixture provider",
      "default_enabled": true,
      "enabled": true,
      "capabilities": ["exact_one_way"]
    }
  ]
}
```

On success, exit code is `0`, stdout contains the JSON object, and stderr is empty.

`cheapy providers test` success output is:

```json
{
  "status": "ok",
  "providers_tested": 1,
  "providers": [
    {
      "name": "manual_fixture",
      "capability": "exact_one_way",
      "status": "success",
      "offer_count": 2,
      "error_count": 0
    }
  ]
}
```

On success, exit code is `0`, stdout contains the JSON object, and stderr is empty.

## Error Behavior

If no provider manifests are found, provider commands return a clear JSON error.

If a manifest is invalid, provider commands return a clear JSON error for that provider. They must not print an unhandled traceback for normal validation failures.

If `manual_fixture` receives unsupported input, it returns a controlled provider failure.

If a provider raises an unexpected exception during `cheapy providers test`, the command catches it and reports a failed provider check in JSON.

Successful CLI commands keep stdout as JSON and avoid stderr. Failed CLI commands write structured JSON errors to stderr, matching the existing CLI style.

Failed provider CLI commands use the existing CLI error shape:

```json
{
  "error": true,
  "code": "ERROR_CODE",
  "message": "Human-readable English message.",
  "suggestion": "Specific next step."
}
```

For failed provider CLI commands, stdout is empty and stderr contains the JSON error.

Required failure cases:

- no manifests found: exit `1`, code `NO_PROVIDER_AVAILABLE`
- invalid manifest: exit `1`, code `PROVIDER_MANIFEST_INVALID`
- provider test returns a provider-level failure: exit `1`, code `PROVIDER_TEST_FAILED`
- provider test raises an unexpected exception: exit `1`, code `PROVIDER_TEST_ERROR`

## Tests

Gate 3 tests cover:

- `manual_fixture` manifest loading
- required manifest fields
- default enabled state
- `exact_one_way` capability
- registry discovery through package resources
- no user-controlled provider path discovery
- `manual_fixture` returns valid `FlightOfferV1` objects
- unsupported fixture input fails in a controlled way
- provider code does not make network calls
- `cheapy providers list` returns valid JSON
- `cheapy providers test` returns valid JSON
- `cheapy mcp` remains blocked with the existing contract-gate error
- `uv build --wheel` includes `cheapy/providers/manual_fixture/manifest.toml`
- an installed wheel can discover the provider manifest through package resources
- an installed wheel can run `cheapy providers list`
- an installed wheel can run `cheapy providers test`

Default verification remains:

```bash
uv run pytest -v
```

Manual checks after implementation:

```bash
uv run cheapy providers list
uv run cheapy providers test
```

## Acceptance Criteria

Gate 3 is complete when:

- `uv run pytest -v` passes
- `uv run cheapy providers list` succeeds
- `uv run cheapy providers test` succeeds
- `uv build --wheel` succeeds
- the built wheel contains `cheapy/providers/manual_fixture/manifest.toml`
- an installed wheel can discover and test `manual_fixture`
- `manual_fixture` is discovered from packaged resources
- `manual_fixture` is enabled by default
- `manual_fixture` returns fixed valid offers for the approved fixture route
- unsupported fixture input fails in a controlled way
- no live network calls are made
- no real MCP server behavior is added
- no orchestrator is added
- no `google_fli` code is added

## Deferred Work

Deferred to later gates:

- full search orchestration
- converting provider-level results into `SearchResponseV1`
- exact search planner
- expanded search planner
- provider timeout and concurrency handling
- MCP `search_cheapest_flights`
- `google_fli`
- opt-in live provider tests
- MCP install hooks for Codex and Claude
