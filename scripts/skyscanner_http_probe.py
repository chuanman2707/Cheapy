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
from urllib.parse import quote


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
    if return_date is None:
        return
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
        ) from exc

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
        ) from exc

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


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        normalize_iata(args.origin)
        normalize_iata(args.destination)
        date_parts(args.departure_date)
        if args.return_date is not None:
            date_parts(args.return_date)
        validate_date_range(args.departure_date, args.return_date)
        validate_limit(args.limit)
        config_from_env(
            os.environ,
            market=args.market,
            locale=args.locale,
            currency=args.currency,
        )
    except ProbeError as exc:
        print(exc.safe_text(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
