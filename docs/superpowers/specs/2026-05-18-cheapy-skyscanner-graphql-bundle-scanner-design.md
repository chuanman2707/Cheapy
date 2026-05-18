# Cheapy Skyscanner GraphQL Bundle Scanner Design

Date: 2026-05-18

## Summary

Add a V1 experimental Skyscanner discovery utility that scans public JavaScript
bundles for internal GraphQL signals.

This is discovery-only work. It does not add a Skyscanner provider, does not
touch the provider registry, does not normalize fares, and does not connect to
the MCP or Cheapy CLI search flow.

The utility lives under the Skyscanner provider ownership path but remains
experimental and unregistered. It fetches an HTTPS entry page, extracts
same-origin JavaScript bundle URLs from `<script src>` tags, fetches bounded
bundle content with a plain HTTP client over HTTPS, scans for GraphQL-focused
candidate identifiers, and writes one JSON object to stdout.

## Goals

- Create a narrow research tool for Skyscanner JavaScript bundle discovery.
- Use a plain HTTP client over HTTPS; no browser automation.
- Require an explicit `--url` entry URL.
- Keep successful scan output as machine-readable JSON on stdout; fatal errors
  are machine-readable JSON on stderr.
- Scan only same-origin JavaScript bundles referenced by the entry HTML.
- Extract GraphQL-focused candidate signals:
  - operation-like names near `query`, `mutation`, or `subscription`
  - persisted query hashes or ids
  - endpoint-like paths containing `graphql`
- Keep default tests offline by using fake HTTP responses.
- Avoid adding runtime dependencies for V1; stdlib HTTP and HTML parsing are
  sufficient unless implementation proves otherwise.

## Non-Goals

- No Skyscanner provider implementation beyond the experimental scanner module.
- No provider manifest.
- No provider registry integration.
- No fare search runtime.
- No result normalization into Contract V1 offers.
- No MCP tool or Cheapy CLI command.
- No browser, Playwright, or Cloakbrowser use.
- No login, captcha solving, proxying, cookie persistence, or session storage.
- No file artifact writing in V1.
- No extraction of raw response snippets or full bundle contents into JSON.

## Architecture

Add one standalone experimental scanner module:

```text
cheapy/providers/skyscanner/
  __init__.py
  scan_graphql_bundles.py
```

Usage:

```sh
uv run python -m cheapy.providers.skyscanner.scan_graphql_bundles --url https://www.skyscanner.net/transport/flights/sgn/bkk/
```

The scanner module owns argument parsing, HTTP fetching, HTML script extraction,
same-origin filtering, bundle scanning, JSON serialization, and process exit
codes. It sits under `cheapy/providers/skyscanner/` to keep future Skyscanner
work in one ownership path, but V1 intentionally does not add
`manifest.toml`, `provider.py`, `adapter.py`, registry loading, or search
integration.

The module should remain small enough to review directly. If the implementation
starts to grow beyond a focused discovery utility, that is a signal to split the
scanner into smaller importable units in a later design, not in V1.

## Inputs

Required:

- `--url`: HTTPS Skyscanner entry page URL.

Optional conservative limits:

- `--max-bundles`, default `20`
- `--max-bytes-per-bundle`, default `5000000`
- `--timeout-seconds`, default `15`

V1 does not include route/date preset arguments. The caller supplies the exact
entry URL being researched.

## Data Flow

1. Validate `--url` as an HTTPS URL with a host.
2. Fetch the entry HTML using a plain HTTP client.
3. Reject non-HTML entry responses before scanning.
4. Extract only external script sources from tags such as
   `<script src="/assets/app.js">`.
5. Resolve relative script URLs with the final same-origin entry URL.
6. Keep only script URLs whose origin matches the final entry origin.
7. Fetch at most `max_bundles` same-origin scripts.
8. Read at most `max_bytes_per_bundle` bytes per bundle.
9. Decode bundle bytes conservatively as text for regex scanning.
10. Extract GraphQL candidate signals.
11. Print a single JSON object to stdout.

Inline scripts are ignored in V1. Third-party script URLs are counted as skipped
but are not fetched.

## JSON Output

Successful scans print one JSON object to stdout:

```json
{
  "schema_version": "1",
  "target_url": "https://www.skyscanner.example/path",
  "fetched_at": "2026-05-18T00:00:00Z",
  "entry": {
    "status_code": 200,
    "final_url": "https://www.skyscanner.example/path",
    "content_type": "text/html",
    "script_count": 42,
    "same_origin_script_count": 18,
    "skipped_cross_origin_script_count": 24
  },
  "limits": {
    "max_bundles": 20,
    "max_bytes_per_bundle": 5000000,
    "timeout_seconds": 15
  },
  "bundles": [
    {
      "url": "https://www.skyscanner.example/_next/static/app.js",
      "final_url": "https://www.skyscanner.example/_next/static/app.js",
      "status_code": 200,
      "content_type": "application/javascript",
      "bytes_scanned": 123456,
      "truncated": false,
      "matches": {
        "operation_names": ["FlightSearchQuery"],
        "persisted_query_ids": ["0123456789abcdef"],
        "graphql_paths": ["/graphql"]
      }
    }
  ],
  "errors": []
}
```

All matches are candidates. V1 does not claim that a matched value is currently
replayable or sufficient to call an internal API.

Fatal validation or entry-fetch failures exit nonzero and write a JSON error
object to stderr. Bundle-level failures are included in the top-level `errors`
array and scanning continues when possible.

Fatal stderr error shape:

```json
{
  "schema_version": "1",
  "error": true,
  "error_type": "invalid_url",
  "message": "Entry URL must be an HTTPS URL with a host.",
  "details": {
    "target_url": "http://example.test"
  }
}
```

Bundle-level error shape inside successful stdout scans:

```json
{
  "scope": "bundle",
  "error_type": "bundle_fetch_failed",
  "message": "Bundle fetch failed.",
  "url": "https://www.skyscanner.example/_next/static/app.js",
  "status_code": null,
  "details": {
    "exception_type": "TimeoutError"
  }
}
```

Error details must stay safe: no cookies, request headers, response bodies, raw
bundle contents, or match context snippets.

## GraphQL Signal Extraction

The scanner focuses on three signal families:

- `operation_names`: operation-like names found near GraphQL words such as
  `query`, `mutation`, or `subscription`
- `persisted_query_ids`: hash-like or id-like values near persisted-query terms
  such as `sha256Hash`, `persistedQuery`, `operationId`, or `queryId`
- `graphql_paths`: URL paths or endpoint strings containing `graphql`

Regex patterns must favor low-risk candidate discovery over broad dumping. The
scanner should deduplicate matches per bundle and sort values for stable JSON.
It must not include raw source snippets in output.

## Error Handling

The utility fails closed and reports structured JSON errors.

For origin checks, origin means `(scheme, host, effective_port)`, where HTTPS
without an explicit port uses `443`. Entry redirects are followed only when they
remain on the same origin as the original `--url`; a cross-origin entry redirect
is fatal. Script URLs are resolved relative to the final same-origin entry URL.
Bundle redirects are followed only when they remain on that same origin; a
cross-origin bundle redirect becomes a bundle-level error and scanning continues.
`target_url` records the original requested URL. `entry.final_url` and
`bundle.final_url` record the final same-origin URLs after redirects.

| Condition | Error type | Exit behavior |
| --- | --- | --- |
| entry URL is not HTTPS | `invalid_url` | nonzero |
| entry fetch timeout or network failure | `entry_fetch_failed` | nonzero |
| entry response is 401 or 403 | `blocked` | nonzero |
| entry response is 429 | `rate_limited` | nonzero |
| entry response is not HTML | `unsupported_entry_content_type` | nonzero |
| entry redirects to another origin | `cross_origin_redirect` | nonzero |
| bundle redirects to another origin | `cross_origin_redirect` | continue |
| bundle fetch timeout or network failure | `bundle_fetch_failed` | continue |
| bundle response is 401 or 403 | `bundle_blocked` | continue |
| bundle response is 429 | `bundle_rate_limited` | continue |
| bundle exceeds byte cap | no error; `truncated=true` | continue |

The script must not emit cookies, request headers, response bodies, raw bundle
contents, or match context snippets.

## Safety Constraints

- Fetch only the entry URL and same-origin script URLs directly referenced by
  that entry HTML.
- Do not follow redirects to a different origin.
- Do not persist cookies or reuse session state between runs.
- Do not send custom authentication headers.
- Do not retry failed requests automatically.
- Do not fetch analytics, logging, image, CSS, or third-party URLs.
- Do not perform provider-internal fanout beyond the explicit bundle cap.

## Testing

Default tests must remain offline.

Required coverage:

- HTTPS URL validation.
- HTML `<script src>` extraction.
- Relative and absolute script URL resolution.
- Same-origin filtering.
- Cross-origin redirect blocking.
- Bundle count cap.
- Bundle byte cap and `truncated=true`.
- Operation-name extraction.
- Persisted query id extraction.
- GraphQL path extraction.
- Stable JSON shape for a no-match scan.
- Structured stderr JSON for fatal validation or entry-fetch errors.
- Bundle-level fetch errors are reported without aborting other bundles.
- Regression coverage proves the experimental Skyscanner directory is not
  loaded as a provider: no `skyscanner` manifest appears in
  `discover_provider_manifests()`, `load_search_providers()`,
  `cheapy providers list`, `cheapy providers test`, search runtime provider
  calls, or MCP tool listing.

Tests should use fake HTTP responses or monkeypatched opener functions. They
must not call Skyscanner or any live network endpoint by default.

## Acceptance Criteria

- The scanner is implemented as an experimental module under
  `cheapy/providers/skyscanner/`.
- Running the scanner requires an explicit HTTPS `--url`.
- Successful scans write JSON to stdout only.
- Fatal errors write JSON to stderr and exit nonzero.
- No Skyscanner manifest, provider runtime, registry, MCP, or CLI search
  behavior changes.
- Offline tests cover parser, filtering, limits, regex extraction, and JSON
  error behavior.
- Offline regression tests prove that placing the scanner under
  `cheapy/providers/skyscanner/` does not register or execute Skyscanner as a
  Cheapy provider.
