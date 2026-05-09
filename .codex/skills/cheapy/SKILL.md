---
name: cheapy-flight-search
description: Use when a user asks an agent to search flights with Cheapy, normalize airport aliases to IATA codes, or call Cheapy MCP tools.
---

# Cheapy Flight Search

Use this skill before calling Cheapy MCP tools.

Cheapy tools only accept IATA airport codes. Always pass origin and destination as 3-letter IATA codes.

The agent is responsible for understanding the user's sentence and converting clear airport aliases to IATA before calling Cheapy. Do not pass city names, airport names, or Vietnamese aliases into Cheapy tools.

## Vietnamese Airport Aliases

Use these aliases only when the user's meaning is clear.

| User text | IATA |
| --- | --- |
| nha trang | CXR |
| cam ranh | CXR |
| sân bay cam ranh | CXR |
| sài gòn | SGN |
| sai gon | SGN |
| tp hcm | SGN |
| ho chi minh | SGN |
| hồ chí minh | SGN |
| hà nội | HAN |
| ha noi | HAN |
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
3. Decide one-way or round-trip from the user's sentence.
4. Call the Cheapy MCP search tool with IATA values only.
5. If an airport is ambiguous or outside the supported list, ask the user to clarify instead of guessing.
