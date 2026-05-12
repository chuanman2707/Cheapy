# Cheapy MCP Flight Search

<!-- BEGIN CHEAPY MANAGED CLAUDE INSTRUCTIONS -->
Use Cheapy only for exact one-way MVP flight searches.

- Call only `search_cheapest_flights`.
- Pass `schema_version="1"`.
- Before calls, require origin, destination, and departure date; ask a follow-up if any are missing.
- Normalize clear origin and destination airports to 3-letter IATA codes.
- If airport meaning is unclear, clarify ambiguous airports instead of guessing.
- Normalize dates to ISO `YYYY-MM-DD`.
- Use Contract V1 passenger defaults when unspecified: `adults=1`, `children=0`, `infants_on_lap=0`, `infants_in_seat=0`.
- Ask a follow-up for ambiguous non-default passenger counts.
- expanded, flexible, nearby-airport, split-ticket, and round-trip search is deferred; do not pass return_date.
- Do not ask the user to choose providers.
- Explain mixed currency cautiously; preserve provider currency and do not overstate comparisons.
<!-- END CHEAPY MANAGED CLAUDE INSTRUCTIONS -->
