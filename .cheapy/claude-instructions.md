# Cheapy MCP Flight Search

<!-- BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS -->
Use Cheapy for one-way and round-trip MVP flight searches.

- Call only `search_cheapest_flights`.
- Pass `schema_version="1"`.
- Before calls, require origin, destination, and departure date; ask a follow-up if any are missing.
- Normalize clear origin and destination airports to 3-letter IATA codes.
- If airport meaning is unclear, clarify ambiguous airports instead of guessing.
- Normalize dates to ISO `YYYY-MM-DD`.
- Use `search_mode="exact"` for fixed exact one-way or exact round-trip searches.
- Use `return_date` for round-trip searches when the user asks for a return.
- Use `search_mode="expanded"` for expanded flexible-date searches around the requested date.
- Use Contract V1 passenger defaults when unspecified: `adults=1`, `children=0`, `infants_on_lap=0`, `infants_in_seat=0`.
- Ask a follow-up for ambiguous non-default passenger counts.
- nearby-airport and split-ticket search is deferred.
- Cheapy may call multiple enabled live providers, including google_fli and traveloka.
- Do not ask the user to choose providers.
- Use each offer's `provider` field when explaining where a fare came from.
- When an offer includes `public_search_url`, render the fare/provider as a Markdown link and use the exact `public_search_url` value as the Markdown URL target. If `public_search_url` is absent, show plain text and do not invent a link.
- Traveloka is a default-enabled research provider for this codebase under the project permission assumption and may return structured timeout, block, or parse failures.
- Choose the cheapest result from the returned `offers` list when currencies are comparable.
- Explain mixed currency cautiously; preserve provider currency and do not overstate comparisons.
<!-- END CHEAPY MANAGED CLAUDE INSTRUCTIONS -->
