#!/usr/bin/env python3
"""Browserless Skyscanner HTTP research probe.

This script is intentionally not a Cheapy provider. It resolves Skyscanner
entity IDs, calls the researched web-unified-search endpoint, and prints a
small terminal report for manual inspection.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
import os
import re
import sys
from typing import Mapping, Protocol
from urllib.parse import quote, urljoin, urlsplit
import uuid

import httpx


DEFAULT_BASE_URL = "https://www.skyscanner.com.sg"
DEFAULT_TIMEOUT_SECONDS = 20.0
IATA_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    market: str
    locale: str
    currency: str
    cookie: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class EntityResult:
    iata: str
    entity_id: str
    name: str
    place_type: str | None = None
    parent_entity_id: str | None = None
    place_of_stay_entity_id: str | None = None


@dataclass(frozen=True)
class FlightProbeResult:
    airline: str
    price_amount: float
    currency: str
    deeplink_url: str


class HttpResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse: ...

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class ProbeError(Exception):
    """Safe, user-facing probe error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def safe_text(self) -> str:
        return f"{self.code}: {self.message}"


def normalize_iata(value: str) -> str:
    iata = value.strip().upper()
    if not IATA_RE.fullmatch(iata):
        raise ProbeError("invalid_argument", "IATA code must be exactly 3 letters.")
    return iata


def date_parts(value: str) -> dict[str, str]:
    if not DATE_RE.fullmatch(value):
        raise ProbeError("invalid_argument", "Date must use YYYY-MM-DD format.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ProbeError("invalid_argument", "Date must use YYYY-MM-DD format.") from exc
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
        raise ProbeError(
            "invalid_argument",
            "Return date must not be earlier than departure date.",
        )


def validate_limit(value: int) -> int:
    if value < 1:
        raise ProbeError("invalid_argument", "Limit must be at least 1.")
    return value


def require_cookie(env: Mapping[str, str]) -> str:
    cookie = env.get("CHEAPY_SKYSCANNER_COOKIE", "").strip()
    if not cookie:
        raise ProbeError(
            "missing_cookie",
            "Set CHEAPY_SKYSCANNER_COOKIE before running the Skyscanner probe.",
        )
    return cookie


def config_from_env(
    env: Mapping[str, str],
    *,
    market: str,
    locale: str,
    currency: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProbeConfig:
    return ProbeConfig(
        base_url=base_url.rstrip("/"),
        market=market,
        locale=locale,
        currency=currency,
        cookie=require_cookie(env),
        timeout_seconds=timeout_seconds,
    )


AUTOSUGGEST_PATH = "/g/autosuggest-search/api/v1/search-flight"
SEARCH_PATH = "/g/radar/api/v2/web-unified-search/"


def request_headers(config: ProbeConfig, *, accept_json: bool = True) -> dict[str, str]:
    headers = {
        "cookie": config.cookie,
        "x-skyscanner-channelid": "website",
        "x-skyscanner-currency": config.currency,
        "x-skyscanner-locale": config.locale,
        "x-skyscanner-market": config.market,
    }
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
    if not isinstance(payload, dict):
        raise ProbeError(
            "autosuggest_parse_error",
            "Autosuggest response did not contain a JSON object.",
        )
    places = payload.get("places", payload.get("Places"))
    if not isinstance(places, list):
        raise ProbeError(
            "autosuggest_parse_error",
            "Autosuggest response did not contain a places list.",
        )
    return places


def _candidate_to_entity(candidate: object, *, requested_iata: str, is_destination: bool) -> EntityResult | None:
    iata = _as_str(_field(candidate, ("iataCode", "IataCode", "iata", "IATA")))
    if iata is None or iata.upper() != requested_iata:
        return None
    entity_id = _as_str(_field(candidate, ("entityId", "EntityId", "PlaceId")))
    name = _as_str(_field(candidate, ("name", "Name", "PlaceName")))
    if entity_id is None or name is None:
        return None
    place_type = _as_str(_field(candidate, ("type", "Type", "placeType", "PlaceType")))
    parent_id = _as_str(
        _field(
            candidate,
            ("parentId", "ParentId", "CityId", "cityId", "parent.entityId"),
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
    normalized = entity.place_type.upper()
    return "AIRPORT" in normalized or normalized == "AIRPORT"


def _safe_candidate_summary(entities: list[EntityResult]) -> str:
    return "; ".join(
        f"{entity.iata} {entity.entity_id} {entity.name} {entity.place_type or 'unknown'}"
        for entity in entities
    )


def get_entity_id(
    iata_code: str,
    *,
    config: ProbeConfig,
    client: HttpClient,
    is_destination: bool = False,
) -> EntityResult:
    requested_iata = normalize_iata(iata_code)
    url = (
        f"{config.base_url}{AUTOSUGGEST_PATH}/"
        f"{quote(config.market, safe='')}/{quote(config.locale, safe='')}/"
        f"{quote(requested_iata, safe='')}"
    )
    try:
        response = client.get(
            url,
            params={
                "isDestination": "true" if is_destination else "false",
                "enable_general_search_v2": "false",
            },
            headers=request_headers(config),
            timeout=config.timeout_seconds,
        )
    except Exception as exc:
        raise ProbeError(
            "autosuggest_transport_error",
            f"Autosuggest request failed with {type(exc).__name__}.",
        ) from None

    if response.status_code < 200 or response.status_code >= 300:
        raise ProbeError(
            "autosuggest_http_error",
            f"Autosuggest returned HTTP {response.status_code}.",
        )

    try:
        payload = response.json()
        places = _places_from_payload(payload)
    except ProbeError:
        raise
    except Exception as exc:
        raise ProbeError(
            "autosuggest_parse_error",
            f"Autosuggest response could not be parsed as JSON: {type(exc).__name__}.",
        ) from None

    entities = [
        entity
        for candidate in places
        if (entity := _candidate_to_entity(
            candidate,
            requested_iata=requested_iata,
            is_destination=is_destination,
        ))
        is not None
    ]
    if not entities:
        raise ProbeError(
            "entity_not_found",
            f"No Skyscanner entity matched IATA {requested_iata}.",
        )

    airport_entities = [entity for entity in entities if _is_airport(entity)]
    preferred = airport_entities or entities
    if len(preferred) > 1:
        raise ProbeError(
            "entity_ambiguous",
            f"Multiple Skyscanner entities matched {requested_iata}: {_safe_candidate_summary(preferred)}",
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
) -> dict[str, object]:
    validate_date_range(departure_date, return_date)
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
        "adults": 1,
        "legs": legs,
    }


def search_headers(config: ProbeConfig, *, view_id: str) -> dict[str, str]:
    headers = request_headers(config, accept_json=False)
    headers["content-type"] = "application/json"
    headers["x-skyscanner-viewid"] = view_id
    return headers


def _search_payload(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
    config: ProbeConfig,
    client: HttpClient,
) -> object:
    url = urljoin(config.base_url + "/", SEARCH_PATH.lstrip("/"))
    body = build_search_body(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
    )
    try:
        response = client.post(
            url,
            json=body,
            headers=search_headers(config, view_id=str(uuid.uuid4())),
            timeout=config.timeout_seconds,
        )
    except Exception as exc:
        raise ProbeError(
            "search_transport_error",
            f"Search request failed with {type(exc).__name__}.",
        ) from None

    if response.status_code < 200 or response.status_code >= 300:
        raise ProbeError("search_http_error", f"Search returned HTTP {response.status_code}.")

    try:
        payload = response.json()
    except Exception as exc:
        raise ProbeError(
            "search_parse_error",
            f"Search response could not be parsed as JSON: {type(exc).__name__}.",
        ) from None

    if not isinstance(payload, dict):
        raise ProbeError("search_parse_error", "Search response was not a JSON object.")
    status = _field(payload.get("context"), ("status",))
    if status != "complete":
        raise ProbeError("search_incomplete", "Search did not complete.")
    itineraries = payload.get("itineraries")
    results = _field(itineraries, ("results",))
    if not isinstance(results, list):
        raise ProbeError("search_parse_error", "Search response did not contain itineraries.results.")
    return payload


def _float_value(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _iter_segments(itinerary: object) -> list[object]:
    if not isinstance(itinerary, dict):
        return []
    segments: list[object] = []
    legs = itinerary.get("legs")
    if not isinstance(legs, list):
        return []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        leg_segments = leg.get("segments")
        if isinstance(leg_segments, list):
            segments.extend(leg_segments)
    return segments


def _airline_label(itinerary: object) -> str:
    labels: list[str] = []
    for segment in _iter_segments(itinerary):
        carrier = _field(segment, ("marketingCarrier",))
        label = _as_str(_field(carrier, ("displayCode", "name")))
        if label is not None and label not in labels:
            labels.append(label)
    return "+".join(labels) if labels else "unknown"


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


def _safe_deeplink_url(raw_url: str, *, config: ProbeConfig) -> str | None:
    parsed = urlsplit(raw_url)
    if parsed.scheme == "" and parsed.netloc == "":
        if raw_url.startswith("/transport_deeplink/"):
            return urljoin(config.base_url + "/", raw_url)
        return None

    base = urlsplit(config.base_url)
    if (
        parsed.scheme == base.scheme
        and parsed.netloc.lower() == base.netloc.lower()
        and parsed.path.startswith("/transport_deeplink/")
    ):
        return raw_url
    return None


def _extract_results(payload: object, *, config: ProbeConfig) -> list[FlightProbeResult]:
    results = _field(_field(payload, ("itineraries",)), ("results",))
    if not isinstance(results, list):
        raise ProbeError("search_parse_error", "Search response did not contain itineraries.results.")

    extracted: list[FlightProbeResult] = []
    for itinerary in sorted(
        results,
        key=lambda item: _float_value(_field(item, ("price.raw",))) or float("inf"),
    ):
        canonical_price = _float_value(_field(itinerary, ("price.raw",)))
        if canonical_price is None:
            continue
        option = _positive_price_option(itinerary)
        if option is None:
            continue
        _, deeplink = option
        safe_deeplink = _safe_deeplink_url(deeplink, config=config)
        if safe_deeplink is None:
            continue
        extracted.append(
            FlightProbeResult(
                airline=_airline_label(itinerary),
                price_amount=canonical_price,
                currency=config.currency,
                deeplink_url=safe_deeplink,
            )
        )

    if not extracted:
        raise ProbeError(
            "no_usable_results",
            "Search completed but no itinerary had a positive price and deep link.",
        )
    return extracted


def fetch_flights(
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None = None,
    *,
    config: ProbeConfig,
    client: HttpClient,
) -> list[FlightProbeResult]:
    payload = _search_payload(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        config=config,
        client=client,
    )
    return _extract_results(payload, config=config)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Skyscanner HTTP search.")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--departure-date", required=True)
    parser.add_argument("--return-date")
    parser.add_argument("--market", default="SG")
    parser.add_argument("--locale", default="en-GB")
    parser.add_argument("--currency", default="SGD")
    parser.add_argument("--limit", type=int, default=3)
    return parser


def print_results(results: list[FlightProbeResult], *, limit: int) -> None:
    safe_limit = validate_limit(limit)
    for index, result in enumerate(results[:safe_limit], start=1):
        print(
            f"{index}. {result.airline} | "
            f"{result.price_amount:.2f} {result.currency} | "
            f"{result.deeplink_url}"
        )


def run_probe(
    *,
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    return_date: str | None,
    limit: int,
    config: ProbeConfig,
    client: HttpClient,
) -> int:
    origin = get_entity_id(
        origin_iata,
        config=config,
        client=client,
        is_destination=False,
    )
    destination = get_entity_id(
        destination_iata,
        config=config,
        client=client,
        is_destination=True,
    )
    results = fetch_flights(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        config=config,
        client=client,
    )
    print_results(results, limit=limit)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        origin_iata = normalize_iata(args.origin)
        destination_iata = normalize_iata(args.destination)
        validate_date_range(args.departure_date, args.return_date)
        limit = validate_limit(args.limit)
        config = config_from_env(
            os.environ,
            market=args.market,
            locale=args.locale,
            currency=args.currency,
        )
        with httpx.Client() as client:
            return run_probe(
                origin_iata=origin_iata,
                destination_iata=destination_iata,
                departure_date=args.departure_date,
                return_date=args.return_date,
                limit=limit,
                config=config,
                client=client,
            )
    except ProbeError as exc:
        print(exc.safe_text(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
