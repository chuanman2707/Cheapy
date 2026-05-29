#!/usr/bin/env python3
"""Skyscanner HTTP research probe.

This script is intentionally not a Cheapy provider. It resolves Skyscanner
entity IDs, calls the researched web-unified-search endpoint, and prints a
small terminal report for manual inspection.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys
from typing import Mapping
from urllib.parse import urljoin, urlsplit

from cheapy.providers.skyscanner.adapter import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    CurlClient,
    CurlResponse,
    HttpClient,
    HttpResponse,
    SkyscannerConfig as ProbeConfig,
    SkyscannerEntity as EntityResult,
    SkyscannerProviderError as ProbeError,
    _as_str,
    _field,
    _float_value,
    _search_payload,
    build_search_body,
    date_parts,
    get_entity_id,
    normalize_iata,
    request_headers,
    search_headers,
    validate_date_range,
)


@dataclass(frozen=True)
class FlightProbeResult:
    airline: str
    price_amount: float
    currency: str
    deeplink_url: str
    flight_numbers: str = "unknown"


def validate_limit(value: int) -> int:
    if value < 1:
        raise ProbeError(
            failure_type="invalid_argument",
            message_en="Limit must be at least 1.",
        )
    return value


def require_cookie(env: Mapping[str, str]) -> str:
    cookie = env.get("CHEAPY_SKYSCANNER_COOKIE", "").strip()
    if not cookie:
        raise ProbeError(
            failure_type="missing_cookie",
            message_en="Set CHEAPY_SKYSCANNER_COOKIE before running the Skyscanner probe.",
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
        user_agent=(
            env.get("CHEAPY_SKYSCANNER_USER_AGENT", DEFAULT_USER_AGENT).strip()
            or DEFAULT_USER_AGENT
        ),
    )

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


def _segment_flight_number(segment: object) -> str | None:
    flight_number = _as_str(_field(segment, ("flightNumber", "flightNo", "number")))
    if flight_number is None:
        return None
    carrier = _field(segment, ("marketingCarrier",))
    carrier_code = _as_str(_field(carrier, ("displayCode", "alternateId", "id")))
    if carrier_code is None:
        return flight_number
    compact_number = flight_number.replace(" ", "")
    compact_carrier = carrier_code.replace(" ", "")
    if compact_number.upper().startswith(compact_carrier.upper()):
        return compact_number
    return f"{compact_carrier}{compact_number}"


def _flight_numbers_label(itinerary: object) -> str:
    numbers: list[str] = []
    for segment in _iter_segments(itinerary):
        number = _segment_flight_number(segment)
        if number is not None:
            numbers.append(number)
    return "/".join(numbers) if numbers else "unknown"


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
        raise ProbeError(
            failure_type="parse_error",
            message_en="Search response did not contain itineraries.results.",
        )

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
                flight_numbers=_flight_numbers_label(itinerary),
            )
        )

    if not extracted:
        raise ProbeError(
            failure_type="no_usable_results",
            message_en="Search completed but no itinerary had a positive price and deep link.",
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
        adults=1,
        config=config,
        client=client,
    )
    return _extract_results(payload, config=config)


def _safe_error_text(exc: ProbeError) -> str:
    return f"{exc.failure_type}: {exc.message_en}"


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
    parser.add_argument("--transport", choices=("curl", "httpx"), default="curl")
    return parser


def print_results(results: list[FlightProbeResult], *, limit: int) -> None:
    safe_limit = validate_limit(limit)
    for index, result in enumerate(results[:safe_limit], start=1):
        print(
            f"{index}. {result.airline} | "
            f"{result.flight_numbers} | "
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
    validate_limit(limit)
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
        if args.transport == "curl":
            return run_probe(
                origin_iata=origin_iata,
                destination_iata=destination_iata,
                departure_date=args.departure_date,
                return_date=args.return_date,
                limit=limit,
                config=config,
                client=CurlClient(),
            )
        import httpx

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
        print(_safe_error_text(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
