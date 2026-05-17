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

## Deeper Result Endpoint Discovery

A headed browser run showed that the `fulltwosearch` navigation can continue
after Traveloka's client-side interstitial completes. No captcha was solved by
the agent. After the interstitial completed, the page title became:

```text
Round Trip: CXR ⇿ HAN, 20 May 2026, HAN ⇿ CXR, 25 May 2026
```

The rendered page contained route and fare markers including:

- `N. Trang (CXR) -> Hanoi (HAN)`
- `USD 192.36`
- `USD 217.96`
- `Vietnam Airlines`
- `Vietravel Airlines`

The first-party offer-result endpoints observed after that point were:

```text
POST /api/v2/flight/search/initial
POST /api/v2/flight/search/poll
```

Reduced request schema:

```json
{
  "fields": [],
  "clientInterface": "desktop",
  "data": {
    "tripType": "ROUND_TRIP",
    "seatPublishedClass": "ECONOMY",
    "journeys": [
      {
        "originCode": "CXR",
        "destinationCode": "HAN",
        "departureDate": "2026-05-20"
      },
      {
        "originCode": "HAN",
        "destinationCode": "CXR",
        "departureDate": "2026-05-25"
      }
    ],
    "journeyIndex": 0,
    "selectedFlights": [],
    "numSeats": {
      "numAdults": 1,
      "numChildren": 0,
      "numInfants": 0
    },
    "searchId": "<uuid>",
    "currency": "USD",
    "additionalData": {
      "utmId": null,
      "utmSource": null,
      "utmIdMarketing": null,
      "pageName": "SEARCH_RESULT",
      "searchSource": "ROUNDTRIP",
      "visitId": "<uuid>",
      "usePromoFinder": true,
      "useDateFlow": false,
      "isBreakSmartCombo": false,
      "prefetchFlag": false,
      "isBaggageFilterEnabled": false
    },
    "filter": {
      "standAlone": true
    },
    "inventoryPricingDisplayType": "COMBINED",
    "sharedFlights": [],
    "trackingMap": {}
  }
}
```

Reduced response schema:

```json
{
  "data": {
    "meta": {
      "searchCompleted": true,
      "searchId": "<uuid>"
    },
    "airlineDataMap": {
      "VJ": {
        "name": "VietJet Air",
        "iataCode": "VJ"
      }
    },
    "airportDataMap": {
      "CXR": {
        "localName": "Cam Ranh",
        "city": "Nha Trang"
      },
      "HAN": {
        "localName": "Noibai International Airport",
        "city": "Hanoi"
      }
    },
    "searchResults": [
      {
        "id": "<result-id>",
        "flightMetadata": {
          "totalNumStop": "0",
          "tripDuration": "110",
          "airlineIds": ["VJ"],
          "totalCombinedPrice": {
            "currencyValue": {
              "currency": "USD",
              "amount": "29890"
            },
            "numOfDecimalPoint": "2"
          }
        },
        "fare": {
          "display": {
            "currencyValue": {
              "currency": "USD",
              "amount": "29890"
            },
            "numOfDecimalPoint": "2"
          }
        },
        "connectingFlightRoutes": [
          {
            "departureAirport": "CXR",
            "arrivalAirport": "HAN",
            "totalNumStop": "0",
            "durationInMinutes": "110",
            "segments": [
              {
                "departureAirport": "CXR",
                "arrivalAirport": "HAN",
                "flightNumber": "VJ-774",
                "airlineCode": "VJ",
                "durationMinutes": "110",
                "departureDate": {
                  "year": "2026",
                  "month": "5",
                  "day": "20"
                },
                "departureTime": {
                  "hour": "15",
                  "minute": "25"
                },
                "arrivalDate": {
                  "year": "2026",
                  "month": "5",
                  "day": "20"
                },
                "arrivalTime": {
                  "hour": "17",
                  "minute": "15"
                }
              }
            ]
          }
        ]
      }
    ]
  }
}
```

Price amounts are minor units. For example, amount `29890` with
`numOfDecimalPoint` `2` represents `USD 298.90`.

## Replayability Decision

Partially discovered, but still blocked under the approved runtime constraints.

The offer-result API exists:

- `POST /api/v2/flight/search/initial`
- `POST /api/v2/flight/search/poll`

However, direct HTTP replay of `search/initial` without the browser-created
Traveloka session and challenge cookies returned:

```text
202 text/html; charset=UTF-8
empty body
```

Adding a first-party-only `fulltwosearch` bootstrap request before
`search/initial` was not sufficient:

```text
request 1: GET /en-en/flight/fulltwosearch... -> 403 Datadome protected
request 2: POST /api/v2/flight/search/initial -> 202 text/html, empty body
```

The browser state after successful page load contained Traveloka session cookies
and anti-abuse artifacts including `datadome`, `aws-waf-token`, `tvl`, `tvo`,
and `tvs`. Replaying `search/initial` with those browser-created artifacts
returned `200 application/json` and 103 search results with prices. This proves
the request schema is valid and the remaining blocker is acquisition of the
runtime session/challenge artifacts, not the offer API shape.

Therefore, a pure HTTP-only runtime with at most two requests cannot reliably
return Traveloka fares unless Traveloka provides an official bootstrap/API
contract or an allowlisted partner endpoint that avoids the consumer-web
Datadome/WAF flow.

## Required Runtime Behavior

Phase 2 must not implement a fake successful Traveloka provider using the page
shell, `fulltwosearch` HTML, or `flight/summary` monthly summaries.

The correct offer-result response shape to normalize is
`data.searchResults[]` from `POST /api/v2/flight/search/initial` and, when
needed, `POST /api/v2/flight/search/poll`.

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

A full price-returning provider has three viable implementation paths:

1. Preferred: Traveloka support provides an official/partner API contract or
   allowlisted HTTP bootstrap that returns the session artifacts without the
   consumer-web Datadome/WAF interstitial. Then Cheapy can implement a pure
   HTTP replayer with `initial` plus at most one `poll`.
2. Relax runtime constraints: allow a browser-assisted Traveloka provider that
   navigates `fulltwosearch`, waits for the first `search/initial` or completed
   `search/poll` JSON response, then normalizes `data.searchResults[]`. This
   works with the current website but is not HTTP-only and uses more than two
   network requests.
3. Temporary local-only diagnostic mode: allow an operator-supplied, short-lived
   browser cookie header for `www.traveloka.com`, then call `search/initial`
   and optionally one `poll`. This is fragile, stateful, and not recommended as
   the default provider behavior.

Without one of those paths, the provider must keep failing closed with
`blocked`, `bootstrap_unavailable`, or `unsupported_response`.

A pure HTTP replayer remains gated until Traveloka support provides, or a later
approved discovery pass finds, a replayable first-party result API contract that
satisfies all constraints:

- exact one-way and round-trip request schema
- response envelope with explicit no-results representation
- offer collection path
- price, currency, airline, segment, duration, and stop-count paths
- no disallowed bootstrap artifact
- no captcha/WAF challenge dependency
- at most two HTTP requests per provider call, redirects included
