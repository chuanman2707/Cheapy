"""Skyscanner web search core extracted from the HTTP probe."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
import re
import time
from urllib.parse import quote, urljoin, urlsplit
import uuid

from cheapy.providers.skyscanner.client import JsonHttpClient, JsonHttpResponse


SEARCH_POLL_ATTEMPTS = 8
SEARCH_POLL_INTERVAL_SECONDS = 1.0
NO_USABLE_RESULTS_ATTEMPTS = 2
AUTOSUGGEST_PATH = "/g/autosuggest-search/api/v1/search-flight"
SEARCH_PATH = "/g/radar/api/v2/web-unified-search/"
IATA_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SearchError(Exception):
    """Safe, provider-local search error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.failure_type = code
        self.message = message


class NoUsableResults(SearchError):
    def __init__(self) -> None:
        super().__init__(
            "no_usable_results",
            "Search completed but no usable itinerary was returned.",
        )


@dataclass(frozen=True)
class SkyscannerConfig:
    base_url: str
    market: str
    locale: str
    currency: str
    cookie: str = field(repr=False)
    user_agent: str
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class EntityResult:
    iata: str
    entity_id: str
    name: str
    place_type: str | None = None
    parent_entity_id: str | None = None
    place_of_stay_entity_id: str | None = None


@dataclass(frozen=True)
class SkyscannerSegment:
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    airline_code: str
    flight_number: str
    duration_minutes: int


@dataclass(frozen=True)
class SkyscannerItinerary:
    price_amount: float
    currency: str
    deeplink_url: str
    segments: tuple[SkyscannerSegment, ...]
    total_duration_minutes: int
    stops: int


def normalize_iata(value: str) -> str:
    iata = value.strip().upper()
    if not IATA_RE.fullmatch(iata):
        raise SearchError("invalid_argument", "IATA code must be exactly 3 letters.")
    return iata


def date_parts(value: str) -> dict[str, str]:
    if not DATE_RE.fullmatch(value):
        raise SearchError("invalid_argument", "Date must use YYYY-MM-DD format.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SearchError("invalid_argument", "Date must use YYYY-MM-DD format.") from exc
    year, month, day = value.split("-")
    return {"@type": "date", "year": year, "month": month, "day": day}


def validate_date_range(departure_date: str, return_date: str | None) -> None:
    date_parts(departure_date)
    if return_date is None:
        return
    date_parts(return_date)
    departure = datetime.strptime(departure_date, "%Y-%m-%d")
    returning = datetime.strptime(return_date, "%Y-%m-%d")
    if returning < departure:
        raise SearchError(
            "invalid_argument",
            "Return date must not be earlier than departure date.",
        )


def _base_url(config: SkyscannerConfig) -> str:
    return config.base_url.rstrip("/")


def _cookie_value(cookie: str, name: str) -> str | None:
    expected = name.lower()
    for part in cookie.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == expected:
            text = value.strip()
            return text or None
    return None


def request_headers(
    config: SkyscannerConfig, *, accept_json: bool = True
) -> dict[str, str]:
    headers = {
        "cookie": config.cookie,
        "x-skyscanner-channelid": "website",
        "x-skyscanner-consent-adverts": "true",
        "x-skyscanner-currency": config.currency,
        "x-skyscanner-locale": config.locale,
        "x-skyscanner-market": config.market,
    }
    gateway_served_by = _cookie_value(config.cookie, "X-Gateway-Servedby")
    if gateway_served_by is not None:
        headers["x-gateway-servedby"] = gateway_served_by
    if accept_json:
        headers["accept"] = "application/json"
    return headers


def _field(mapping: object, names: Sequence[str]) -> object | None:
    if not isinstance(mapping, dict):
        return None
    current: object | None
    for name in names:
        if "." in name:
            current = mapping
            for part in name.split("."):
                if not isinstance(current, dict) or part not in current:
                    current = None
                    break
                current = current[part]
            if current is not None:
                return current
        elif name in mapping:
            return mapping[name]
    return None


def _as_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _places_from_payload(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise SearchError(
            "autosuggest_parse_error",
            "Autosuggest response did not contain a JSON object.",
        )
    places = payload.get("places", payload.get("Places"))
    if not isinstance(places, list):
        raise SearchError(
            "autosuggest_parse_error",
            "Autosuggest response did not contain a places list.",
        )
    return places


def _candidate_to_entity(
    candidate: object, *, requested_iata: str, is_destination: bool
) -> EntityResult | None:
    iata = _as_str(_field(candidate, ("iataCode", "IataCode", "iata", "IATA")))
    place_id = _as_str(_field(candidate, ("placeId", "PlaceId")))
    if iata is None and place_id is not None and IATA_RE.fullmatch(place_id.upper()):
        iata = place_id
    if iata is None or iata.upper() != requested_iata:
        return None
    entity_id = _as_str(
        _field(candidate, ("entityId", "EntityId", "GeoId", "geoId", "placeId", "PlaceId"))
    )
    name = _as_str(_field(candidate, ("name", "Name", "PlaceName")))
    if entity_id is None or name is None:
        return None
    place_type = _as_str(_field(candidate, ("type", "Type", "placeType", "PlaceType")))
    if place_type is None and place_id is not None and place_id.upper() == requested_iata:
        place_type = "Airport"
    parent_id = _as_str(
        _field(
            candidate,
            (
                "parentId",
                "ParentId",
                "GeoContainerId",
                "geoContainerId",
                "CityId",
                "cityId",
                "parent.entityId",
            ),
        )
    )
    return EntityResult(
        iata=requested_iata,
        entity_id=entity_id,
        name=name,
        place_type=place_type,
        parent_entity_id=parent_id,
        place_of_stay_entity_id=parent_id if is_destination and parent_id else None,
    )


def _is_airport(entity: EntityResult) -> bool:
    if entity.place_type is None:
        return False
    normalized = entity.place_type.strip().upper()
    return normalized in {"AIRPORT", "PLACE_TYPE_AIRPORT"}


def _http_status_error(
    status_code: int,
    *,
    default_code: str,
    operation: str,
) -> SearchError:
    if status_code == 403:
        return SearchError("blocked", "Skyscanner returned an access challenge.")
    if status_code == 429:
        return SearchError("rate_limited", "Skyscanner rate limited the request.")
    return SearchError(default_code, f"{operation} returned HTTP {status_code}.")


def get_entity(
    iata_code: str,
    *,
    config: SkyscannerConfig,
    client: JsonHttpClient,
    is_destination: bool = False,
) -> EntityResult:
    requested_iata = normalize_iata(iata_code)
    url = (
        f"{_base_url(config)}{AUTOSUGGEST_PATH}/"
        f"{quote(config.market, safe='')}/{quote(config.locale, safe='')}/"
        f"{quote(requested_iata, safe='')}"
    )
    try:
        response = client.get_json(
            url,
            params={
                "isDestination": "true" if is_destination else "false",
                "enable_general_search_v2": "false",
            },
            headers=request_headers(config),
            timeout_seconds=config.timeout_seconds,
        )
    except Exception:
        raise SearchError(
            "autosuggest_transport_error",
            "Autosuggest request failed.",
        ) from None

    if response.status_code < 200 or response.status_code >= 300:
        raise _http_status_error(
            response.status_code,
            default_code="autosuggest_http_error",
            operation="Autosuggest",
        )

    try:
        payload = response.json()
        places = _places_from_payload(payload)
    except SearchError:
        raise
    except Exception:
        raise SearchError(
            "autosuggest_parse_error",
            "Autosuggest response could not be parsed as JSON.",
        ) from None

    entities = [
        entity
        for candidate in places
        if (
            entity := _candidate_to_entity(
                candidate,
                requested_iata=requested_iata,
                is_destination=is_destination,
            )
        )
        is not None
    ]
    if not entities:
        raise SearchError(
            "entity_not_found",
            "Skyscanner did not return an entity for the requested airport.",
        )

    airport_entities = [entity for entity in entities if _is_airport(entity)]
    preferred = airport_entities or entities
    if len(preferred) > 1:
        raise SearchError(
            "entity_ambiguous",
            "Skyscanner returned multiple matching airport entities.",
        )
    return preferred[0]


def _entity_ref(entity: EntityResult) -> dict[str, str]:
    return {"@type": "entity", "entityId": entity.entity_id}


def build_search_body(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
    adults: int = 1,
) -> dict[str, object]:
    validate_date_range(departure_date, return_date)
    if adults < 1:
        raise SearchError("invalid_argument", "Adults must be at least 1.")

    outbound_leg: dict[str, object] = {
        "legOrigin": _entity_ref(origin),
        "legDestination": _entity_ref(destination),
        "dates": date_parts(departure_date),
    }
    if destination.place_of_stay_entity_id is not None:
        outbound_leg["placeOfStay"] = destination.place_of_stay_entity_id

    legs: list[dict[str, object]] = [outbound_leg]
    if return_date is not None:
        legs.append(
            {
                "legOrigin": _entity_ref(destination),
                "legDestination": _entity_ref(origin),
                "dates": date_parts(return_date),
            }
        )

    return {
        "cabinClass": "ECONOMY",
        "childAges": [],
        "adults": adults,
        "legs": legs,
    }


def _referer_date(value: str) -> str:
    parts = date_parts(value)
    return f"{parts['year'][2:]}{parts['month']}{parts['day']}"


def _search_referer(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
    config: SkyscannerConfig,
) -> str:
    path = (
        f"/transport/flights/{origin.iata.lower()}/{destination.iata.lower()}/"
        f"{_referer_date(departure_date)}/"
    )
    if return_date is not None:
        path = f"{path}{_referer_date(return_date)}/"
    rtn = "1" if return_date is not None else "0"
    return (
        f"{_base_url(config)}{path}"
        f"?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn={rtn}"
        "&preferdirects=false&outboundaltsenabled=false&inboundaltsenabled=false"
    )


def search_headers(
    config: SkyscannerConfig,
    *,
    view_id: str,
    referer: str,
    include_content_type: bool = True,
) -> dict[str, str]:
    headers = request_headers(config, accept_json=False)
    headers["accept"] = "application/json"
    headers["accept-language"] = "en-US,en;q=0.9"
    headers["origin"] = _base_url(config)
    headers["referer"] = referer
    headers["user-agent"] = config.user_agent
    if include_content_type:
        headers["content-type"] = "application/json"
    headers["x-skyscanner-viewid"] = view_id
    return headers


def _read_search_response(
    response: JsonHttpResponse, *, operation: str
) -> dict[str, object]:
    if response.status_code < 200 or response.status_code >= 300:
        raise _http_status_error(
            response.status_code,
            default_code="search_http_error",
            operation=operation,
        )
    try:
        payload = response.json()
    except Exception:
        raise SearchError(
            "search_parse_error",
            f"{operation} response could not be parsed as JSON.",
        ) from None
    if not isinstance(payload, dict):
        raise SearchError(
            "search_parse_error",
            f"{operation} response was not a JSON object.",
        )
    return payload


def _poll_search_session(
    *,
    session_id: str,
    config: SkyscannerConfig,
    client: JsonHttpClient,
    view_id: str,
    referer: str,
) -> dict[str, object]:
    url = urljoin(
        _base_url(config) + "/",
        f"{SEARCH_PATH.lstrip('/')}{quote(session_id, safe='')}",
    )
    try:
        response = client.get_json(
            url,
            params={},
            headers=search_headers(
                config,
                view_id=view_id,
                referer=referer,
                include_content_type=False,
            ),
            timeout_seconds=config.timeout_seconds,
        )
    except Exception:
        raise SearchError("search_transport_error", "Search poll failed.") from None
    return _read_search_response(response, operation="Search poll")


def sleep_between_polls() -> None:
    time.sleep(SEARCH_POLL_INTERVAL_SECONDS)


def _search_results(payload: object) -> list[object] | None:
    results = _field(_field(payload, ("itineraries",)), ("results",))
    return results if isinstance(results, list) else None


def fetch_search_payload(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
    config: SkyscannerConfig,
    client: JsonHttpClient,
    adults: int = 1,
) -> dict[str, object]:
    url = urljoin(_base_url(config) + "/", SEARCH_PATH.lstrip("/"))
    view_id = str(uuid.uuid4())
    referer = _search_referer(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        config=config,
    )
    body = build_search_body(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
    )
    try:
        response = client.post_json(
            url,
            json_body=body,
            headers=search_headers(config, view_id=view_id, referer=referer),
            timeout_seconds=config.timeout_seconds,
        )
    except Exception:
        raise SearchError("search_transport_error", "Search request failed.") from None

    payload = _read_search_response(response, operation="Search")
    initial_payload = payload
    status = _field(payload.get("context"), ("status",))
    if status == "incomplete":
        session_id = _as_str(_field(payload.get("context"), ("sessionId",)))
        if session_id is None:
            raise SearchError("search_incomplete", "Search did not complete.")
        for poll_index in range(SEARCH_POLL_ATTEMPTS):
            payload = _poll_search_session(
                session_id=session_id,
                config=config,
                client=client,
                view_id=view_id,
                referer=referer,
            )
            status = _field(payload.get("context"), ("status",))
            if status != "incomplete":
                break
            if poll_index < SEARCH_POLL_ATTEMPTS - 1:
                sleep_between_polls()
        if (
            status == "complete"
            and _search_results(payload) == []
            and bool(_search_results(initial_payload))
        ):
            payload = initial_payload
    if status != "complete":
        raise SearchError("search_incomplete", "Search did not complete.")
    results = _search_results(payload)
    if not isinstance(results, list):
        raise SearchError(
            "search_parse_error",
            "Search response did not contain itineraries.results.",
        )
    return payload


def _float_value(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _int_value(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _route_legs(itinerary: object) -> list[dict[str, object]] | None:
    if not isinstance(itinerary, dict):
        return None
    legs = itinerary.get("legs")
    if not isinstance(legs, list) or not legs:
        return None
    normalized_legs: list[dict[str, object]] = []
    for leg in legs:
        if not isinstance(leg, dict):
            return None
        normalized_legs.append(leg)
    return normalized_legs


def _iter_legs(itinerary: object) -> list[dict[str, object]]:
    legs = _route_legs(itinerary)
    return legs if legs is not None else []


def _segment_flight_number(segment: object) -> str:
    flight_number = _as_str(_field(segment, ("flightNumber", "flightNo", "number")))
    if flight_number is None:
        return ""
    carrier = _field(segment, ("marketingCarrier",))
    carrier_code = _as_str(_field(carrier, ("displayCode", "alternateId", "id")))
    if carrier_code is None:
        return flight_number
    compact_number = flight_number.replace(" ", "")
    compact_carrier = carrier_code.replace(" ", "")
    if compact_number.upper().startswith(compact_carrier.upper()):
        return compact_number
    return f"{compact_carrier}{compact_number}"


def _is_iso_like_datetime(value: str) -> bool:
    if len(value) <= 10 or value[10] not in {"T", " "}:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _segment_from_payload(segment: object) -> SkyscannerSegment | None:
    carrier = _field(segment, ("marketingCarrier",))
    origin = _as_str(_field(_field(segment, ("origin",)), ("displayCode",)))
    destination = _as_str(_field(_field(segment, ("destination",)), ("displayCode",)))
    departure_time = _as_str(_field(segment, ("departure",)))
    arrival_time = _as_str(_field(segment, ("arrival",)))
    airline_code = _as_str(_field(carrier, ("displayCode", "alternateId", "id")))
    flight_number = _segment_flight_number(segment)
    duration_minutes = _int_value(_field(segment, ("durationInMinutes",)))
    if (
        origin is None
        or destination is None
        or departure_time is None
        or arrival_time is None
        or airline_code is None
        or not flight_number
        or duration_minutes is None
        or duration_minutes <= 0
        or not _is_iso_like_datetime(departure_time)
        or not _is_iso_like_datetime(arrival_time)
    ):
        return None
    return SkyscannerSegment(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        arrival_time=arrival_time,
        airline_code=airline_code,
        flight_number=flight_number,
        duration_minutes=duration_minutes,
    )


def _total_duration_minutes(
    itinerary: object, segments: tuple[SkyscannerSegment, ...]
) -> int | None:
    legs = _route_legs(itinerary)
    if legs is None:
        return None
    leg_durations: list[int] = []
    for leg in legs:
        duration = _int_value(_field(leg, ("durationInMinutes",)))
        if duration is None:
            continue
        if duration <= 0:
            return None
        leg_durations.append(duration)
    if leg_durations:
        if len(leg_durations) != len(legs):
            return None
        return sum(leg_durations)
    total = sum(segment.duration_minutes for segment in segments)
    return total if total > 0 else None


def _stops(itinerary: object, segments: tuple[SkyscannerSegment, ...]) -> int | None:
    legs = _route_legs(itinerary)
    if legs is None:
        return None
    leg_stops = [
        stops
        for leg in legs
        if (stops := _int_value(_field(leg, ("stopCount",)))) is not None
    ]
    if any(stops < 0 for stops in leg_stops):
        return None
    if leg_stops:
        if len(leg_stops) != len(legs):
            return None
        return sum(leg_stops)
    calculated_stops = 0
    for leg in legs:
        raw_segments = leg.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            return None
        calculated_stops += max(0, len(raw_segments) - 1)
    return calculated_stops


def _positive_price_option(itinerary: object) -> tuple[float, str] | None:
    if not isinstance(itinerary, dict):
        return None
    options = itinerary.get("pricingOptions")
    if not isinstance(options, list):
        return None
    candidates: list[tuple[float, dict[str, object]]] = []
    for option in options:
        amount = _float_value(_field(option, ("price.amount",)))
        if amount is None or not isinstance(option, dict):
            continue
        candidates.append((amount, option))
    if not candidates:
        return None
    amount, option = sorted(candidates, key=lambda item: item[0])[0]
    items = option.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        url = _as_str(_field(item, ("url",)))
        if url is not None:
            return amount, url
    return None


def _safe_deeplink_url(raw_url: str, *, config: SkyscannerConfig) -> str | None:
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None
    if parsed.scheme == "" and parsed.netloc == "":
        if raw_url.startswith("/transport_deeplink/"):
            return urljoin(_base_url(config) + "/", raw_url)
        return None

    base = urlsplit(_base_url(config))
    if (
        parsed.scheme == base.scheme
        and parsed.netloc.lower() == base.netloc.lower()
        and parsed.path.startswith("/transport_deeplink/")
    ):
        return raw_url
    return None


def _valid_route_segments(itinerary: object) -> tuple[SkyscannerSegment, ...] | None:
    legs = _route_legs(itinerary)
    if legs is None:
        return None
    segments: list[SkyscannerSegment] = []
    for leg in legs:
        raw_segments = leg.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            return None
        for raw_segment in raw_segments:
            segment = _segment_from_payload(raw_segment)
            if segment is None:
                return None
            segments.append(segment)
    return tuple(segments)


def extract_usable_itineraries(
    payload: object, *, config: SkyscannerConfig
) -> list[SkyscannerItinerary]:
    results = _search_results(payload)
    if not isinstance(results, list):
        raise SearchError(
            "search_parse_error",
            "Search response did not contain itineraries.results.",
        )

    extracted: list[SkyscannerItinerary] = []
    for itinerary in sorted(
        results,
        key=lambda item: _float_value(_field(item, ("price.raw",))) or float("inf"),
    ):
        raw_price = _float_value(_field(itinerary, ("price.raw",)))
        if raw_price is None:
            continue
        option = _positive_price_option(itinerary)
        if option is None:
            continue
        selected_price, deeplink = option
        safe_deeplink = _safe_deeplink_url(deeplink, config=config)
        if safe_deeplink is None:
            continue
        segments = _valid_route_segments(itinerary)
        if segments is None:
            continue
        total_duration_minutes = _total_duration_minutes(itinerary, segments)
        stops = _stops(itinerary, segments)
        if total_duration_minutes is None or stops is None:
            continue
        extracted.append(
            SkyscannerItinerary(
                price_amount=selected_price,
                currency=config.currency,
                deeplink_url=safe_deeplink,
                segments=segments,
                total_duration_minutes=total_duration_minutes,
                stops=stops,
            )
        )

    if not extracted:
        raise NoUsableResults()
    return extracted


def fetch_itineraries(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
    config: SkyscannerConfig,
    client: JsonHttpClient,
    adults: int = 1,
    no_usable_results_attempts: int = NO_USABLE_RESULTS_ATTEMPTS,
) -> list[SkyscannerItinerary]:
    if no_usable_results_attempts <= 0:
        raise ValueError("no_usable_results_attempts must be greater than 0.")
    for attempt_index in range(no_usable_results_attempts):
        payload = fetch_search_payload(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            config=config,
            client=client,
            adults=adults,
        )
        try:
            return extract_usable_itineraries(payload, config=config)
        except NoUsableResults:
            if attempt_index == no_usable_results_attempts - 1:
                raise
    raise NoUsableResults()
