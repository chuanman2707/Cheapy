# Cheapy Traveloka Client API Discovery Contract

Date: 2026-05-14

## Scope

Discovery route:

```text
CXR -> HAN
departure: 2026-05-20
return: 2026-05-25
passengers: 1 adult
cabin: ECONOMY
currency: USD
locale: en-en
```

Runtime constraints from the approved design:

- HTTP-only provider runtime
- at most two HTTP requests per provider call, redirects included
- no login, captcha solving, proxying, persisted cookies, or manually supplied tokens
- no analytics, logging, tracking, coupon, user-profile, or unrelated endpoints
- committed artifacts must not include raw cookies, tokens, full headers, full raw bodies, or browser snapshots

## Discovery Method

Browser automation was used only for endpoint discovery. Runtime remains
HTTP-only.

Steps performed:

1. Opened the existing landing URL:
   `/en-en/flight?trip=roundtrip&origin=CXR&destination=HAN&departureDate=2026-05-20&currency=USD&locale=en-en&cabin=ECONOMY&adults=1&children=0&infantsInSeat=0&infantsOnLap=0&returnDate=2026-05-25`
2. Captured first-party network requests.
3. Filled the visible Traveloka form through normal UI interaction:
   - origin: `Nha Trang (CXR)`
   - destination: `Hanoi (HAN)`
   - departure date: `May 20, 2026`
   - return date: `May 25, 2026`
4. Clicked `Search Flights` once.
5. Inspected only first-party candidate request metadata and reduced request schemas.

## Observed First-Party Search-Adjacent Requests

### Landing Page

The landing URL returned the Traveloka flight app shell. It did not emit a
replayable flight-results API response.

Safe observed first-party paths during landing:

- `POST /en-en/flight/api/evaluate-namespaces`
- `HEAD /api/setViewerInfo`
- user context/profile/device endpoints
- airport autocomplete
- coupon endpoints
- frontend variant evaluation
- logging/metrics endpoints

These are not flight result APIs.

### Date Picker Summary Candidate

Normal UI interaction emitted:

```text
POST /api/v2/flight/summary
content-type: application/json
```

Reduced request schema:

```json
{
  "fields": [],
  "clientInterface": "desktop",
  "data": {
    "roundTripSourceType": "ALL",
    "routeType": "ONEWAY | ROUNDTRIP_FIXED_DEPARTDATE",
    "timeType": "MONTHLY",
    "routeSearchSpec": {
      "clientInterface": "desktop",
      "currency": "USD",
      "locale": "en_EN",
      "sourceAirportOrArea": "CXR",
      "destinationAirportOrArea": "HAN",
      "numSeats": {
        "numAdults": 1,
        "numChildren": 0,
        "numInfants": 0
      },
      "flightDate": {
        "month": 5,
        "day": 1,
        "year": 2026
      },
      "returnFlightDate": null,
      "seatPublishedClass": "ECONOMY",
      "utmId": null,
      "utmSource": null
    },
    "departFilterSpec": {},
    "isPaxTypePriceAverage": false
  }
}
```

For return-date calendar summaries, `routeType` changed to
`ROUNDTRIP_FIXED_DEPARTDATE`, `routeSearchSpec.flightDate` was the fixed
departure date, and `returnFilterSpec` replaced `departFilterSpec`.

Classification:

- first-party host: `www.traveloka.com`
- status: `200`
- content type: `application/json`
- purpose: monthly/date-picker summary
- not replayable as the Cheapy provider result endpoint

Reason it is not replayable:

- no stable offer collection path was discovered
- no itinerary, leg, segment, airline, fare, booking, or exact round-trip result
  envelope was discovered from this endpoint
- observed request shape is monthly summary-oriented, not exact offer search
- using it as a provider success would reproduce the current false-success bug
  with different data

### Result Route Blocker

Clicking `Search Flights` navigated to:

```text
GET /en-en/flight/fulltwosearch?ap=CXR.HAN&dt=20-5-2026.25-5-2026&ps=1.0.0&sc=ECONOMY&funnelSource=SEO-Homepage-SearchForm
```

Safe response evidence:

- HTTP status: `403`
- content type: `text/html`
- response headers contained `x-datadome: protected`
- HTML contained the marker text `Please enable JS and disable any ad blocker`
- HTML referenced `captcha-delivery.com`

Redacted values:

- Datadome challenge IDs
- Datadome cookie-like values
- request IDs
- full response body
- full request/response headers

## Replayability Decision

Blocked. No replayable HTTP-only Traveloka client API endpoint was discovered
within the approved constraints.

The only first-party JSON candidate discovered before result navigation was
`POST /api/v2/flight/summary`, which is a date-picker/monthly summary endpoint
and does not expose offer-level flight results suitable for Contract V1
normalization.

The actual result route discovered through normal UI interaction was
`/en-en/flight/fulltwosearch`, and it returned a Datadome/captcha-delivery
interstitial with HTTP `403`. Replaying or solving that challenge would violate
the approved constraints.

## Required Runtime Behavior

Phase 2 must not implement a fake successful Traveloka provider using the page
shell, `fulltwosearch` HTML, or `flight/summary` monthly summaries.

For the current adapter, the required runtime behavior is:

- `200 text/html` flight app shells without a supported API payload:
  `failure_type="unsupported_response"`, retryable `false`
- invalid JSON from a supposed API endpoint:
  `failure_type="invalid_json"`, retryable `false`
- HTTP `401` or `403`, Datadome, captcha, WAF, or captcha-delivery interstitials:
  `failure_type="blocked"`, retryable `false`
- attempts to exceed the approved request budget:
  `failure_type="request_budget_exceeded"`, retryable `false`
- redirects outside `https://www.traveloka.com` or to captcha/interstitial paths:
  `failure_type="blocked"`, retryable `false`

`TravelokaProvider` should surface these as structured failed provider results
and allow other enabled providers to continue contributing offers.

## Phase 2 Gate

A full HTTP replayer remains gated until Traveloka support provides, or a later
approved discovery pass finds, a replayable first-party result API contract that
satisfies all constraints:

- exact one-way and round-trip request schema
- response envelope with explicit no-results representation
- offer collection path
- price, currency, airline, segment, duration, and stop-count paths
- no disallowed bootstrap artifact
- no captcha/WAF challenge dependency
- at most two HTTP requests per provider call, redirects included
