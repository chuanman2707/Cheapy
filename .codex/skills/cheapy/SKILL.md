---
name: cheapy-flight-search
description: Use when a user asks an agent to search flights with Cheapy, normalize airport aliases to IATA codes, or call Cheapy MCP tools.
---

# Cheapy Flight Search

Use this skill before calling Cheapy MCP tools.

Cheapy tools only accept IATA airport codes. Always pass origin and destination as 3-letter IATA codes.

The agent is responsible for understanding the user's sentence and converting clear airport aliases to IATA before calling Cheapy. Do not pass city names, airport names, or Vietnamese aliases into Cheapy tools.

Cheapy runtime does not resolve Vietnamese aliases.

## Vietnamese Airport Aliases

Use these aliases only when the user's meaning is clear.

| User text | IATA |
| --- | --- |
| nha trang | CXR |
| cam ranh | CXR |
| sân bay cam ranh | CXR |
| sài gòn | SGN |
| sai gon | SGN |
| saigon | SGN |
| tphcm | SGN |
| tp hcm | SGN |
| ho chi minh | SGN |
| ho chi minh city | SGN |
| hồ chí minh | SGN |
| hà nội | HAN |
| ha noi | HAN |
| hanoi | HAN |
| nội bài | HAN |
| noi bai | HAN |
| đà nẵng | DAD |
| da nang | DAD |
| phú quốc | PQC |
| phu quoc | PQC |

## Supported MVP Airports

Vietnam: CXR, SGN, HAN, DAD, PQC.

Regional and Asia: SIN, BKK, KUL, TPE, HKG, ICN, NRT, DOH, DXB.

Long haul: LAX, SFO, JFK, LHR, CDG, FRA, SYD, MEL.

## Calling Pattern

1. Convert clear human airport names to IATA.
2. Convert dates into ISO `YYYY-MM-DD`.
4. Call the Cheapy MCP search tool with IATA values only.
5. If an airport is ambiguous or outside the supported list, ask the user to clarify instead of guessing.

<!-- BEGIN CHEAPY MANAGED CODEX INSTRUCTIONS -->
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
- When an offer includes `public_search_url`, render the fare/provider as a Markdown link, for example [4,920,000 VND on Traveloka](...). If `public_search_url` is absent, show plain text and do not invent a link.
- Traveloka is a default-enabled research provider for this codebase under the project permission assumption and may return structured timeout, block, or parse failures.
- Choose the cheapest result from the returned `offers` list when currencies are comparable.
- Explain mixed currency cautiously; preserve provider currency and do not overstate comparisons.
<!-- END CHEAPY MANAGED CODEX INSTRUCTIONS -->
