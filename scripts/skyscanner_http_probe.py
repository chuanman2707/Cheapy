#!/usr/bin/env python3
"""Skyscanner HTTP research probe.

This script remains a manual probe. Production provider code lives under
``cheapy.providers.skyscanner``; the probe only adapts that reusable core to a
small CLI and a human-readable fare report.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, field
import os
import subprocess
import sys
from typing import Protocol

import httpx

from cheapy.providers.skyscanner import search as skyscanner_search
from cheapy.providers.skyscanner.client import (
    CurlClient as CoreCurlClient,
    CurlResponse,
    JsonHttpClient,
    SkyscannerHttpError,
)


DEFAULT_BASE_URL = "https://www.skyscanner.com.sg"
DEFAULT_TIMEOUT_SECONDS = 20.0
SEARCH_POLL_ATTEMPTS = skyscanner_search.SEARCH_POLL_ATTEMPTS
SEARCH_POLL_INTERVAL_SECONDS = skyscanner_search.SEARCH_POLL_INTERVAL_SECONDS
NO_USABLE_RESULTS_ATTEMPTS = 2
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
EntityResult = skyscanner_search.EntityResult
uuid = skyscanner_search.uuid


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    market: str
    locale: str
    currency: str
    cookie: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(frozen=True)
class FlightProbeResult:
    airline: str
    price_amount: float
    currency: str
    deeplink_url: str
    flight_numbers: str = "unknown"


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


class CurlClient:
    """Probe-facing curl transport backed by the provider curl client."""

    def __init__(
        self,
        *,
        curl_path: str = "curl",
        runner: object = subprocess.run,
    ) -> None:
        self._client = CoreCurlClient(curl_path=curl_path, runner=runner)

    def get(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> CurlResponse:
        try:
            return self._client.get_json(
                url,
                params=params,
                headers=headers,
                timeout_seconds=timeout,
            )
        except SkyscannerHttpError as exc:
            raise RuntimeError(exc.message_en) from None

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> CurlResponse:
        try:
            return self._client.post_json(
                url,
                json_body=json,
                headers=headers,
                timeout_seconds=timeout,
            )
        except SkyscannerHttpError as exc:
            raise RuntimeError(exc.message_en) from None


class _HttpxProbeClient:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        return self._client.get(url, params=params, headers=headers, timeout=timeout)

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        return self._client.post(url, json=json, headers=headers, timeout=timeout)


class _JsonClientAdapter:
    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        return self._client.get(
            url,
            params=dict(params),
            headers=dict(headers),
            timeout=timeout_seconds,
        )

    def post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        return self._client.post(
            url,
            json=dict(json_body),
            headers=dict(headers),
            timeout=timeout_seconds,
        )


def _json_client(client: HttpClient | JsonHttpClient) -> JsonHttpClient:
    if callable(getattr(client, "get_json", None)) and callable(
        getattr(client, "post_json", None)
    ):
        return client  # type: ignore[return-value]
    return _JsonClientAdapter(client)  # type: ignore[arg-type]


def _core_config(config: ProbeConfig) -> skyscanner_search.SkyscannerConfig:
    return skyscanner_search.SkyscannerConfig(
        base_url=config.base_url,
        market=config.market,
        locale=config.locale,
        currency=config.currency,
        cookie=config.cookie,
        user_agent=config.user_agent,
        timeout_seconds=config.timeout_seconds,
    )


def _probe_error(exc: skyscanner_search.SearchError) -> ProbeError:
    return ProbeError(exc.code, exc.message)


def normalize_iata(value: str) -> str:
    try:
        return skyscanner_search.normalize_iata(value)
    except skyscanner_search.SearchError as exc:
        raise _probe_error(exc) from None


def date_parts(value: str) -> dict[str, str]:
    try:
        return skyscanner_search.date_parts(value)
    except skyscanner_search.SearchError as exc:
        raise _probe_error(exc) from None


def validate_date_range(departure_date: str, return_date: str | None) -> None:
    try:
        skyscanner_search.validate_date_range(departure_date, return_date)
    except skyscanner_search.SearchError as exc:
        raise _probe_error(exc) from None


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
        user_agent=(
            env.get("CHEAPY_SKYSCANNER_USER_AGENT", DEFAULT_USER_AGENT).strip()
            or DEFAULT_USER_AGENT
        ),
    )


def request_headers(config: ProbeConfig, *, accept_json: bool = True) -> dict[str, str]:
    return skyscanner_search.request_headers(
        _core_config(config),
        accept_json=accept_json,
    )


def build_search_body(
    *,
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None,
) -> dict[str, object]:
    try:
        return skyscanner_search.build_search_body(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
        )
    except skyscanner_search.SearchError as exc:
        raise _probe_error(exc) from None


def search_headers(
    config: ProbeConfig,
    *,
    view_id: str,
    referer: str,
    include_content_type: bool = True,
) -> dict[str, str]:
    return skyscanner_search.search_headers(
        _core_config(config),
        view_id=view_id,
        referer=referer,
        include_content_type=include_content_type,
    )


def _sleep_between_search_polls() -> None:
    skyscanner_search.sleep_between_polls()


def get_entity_id(
    iata_code: str,
    *,
    config: ProbeConfig,
    client: HttpClient | JsonHttpClient,
    is_destination: bool = False,
) -> EntityResult:
    try:
        return skyscanner_search.get_entity(
            iata_code,
            config=_core_config(config),
            client=_json_client(client),
            is_destination=is_destination,
        )
    except skyscanner_search.SearchError as exc:
        raise _probe_error(exc) from None


def _flight_probe_result(
    itinerary: skyscanner_search.SkyscannerItinerary,
) -> FlightProbeResult:
    airlines: list[str] = []
    flight_numbers: list[str] = []
    for segment in itinerary.segments:
        if segment.airline_code not in airlines:
            airlines.append(segment.airline_code)
        flight_numbers.append(segment.flight_number)
    return FlightProbeResult(
        airline="+".join(airlines) if airlines else "unknown",
        price_amount=itinerary.price_amount,
        currency=itinerary.currency,
        deeplink_url=itinerary.deeplink_url,
        flight_numbers="/".join(flight_numbers) if flight_numbers else "unknown",
    )


def fetch_flights(
    origin: EntityResult,
    destination: EntityResult,
    departure_date: str,
    return_date: str | None = None,
    *,
    config: ProbeConfig,
    client: HttpClient | JsonHttpClient,
) -> list[FlightProbeResult]:
    try:
        itineraries = skyscanner_search.fetch_itineraries(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            config=_core_config(config),
            client=_json_client(client),
            no_usable_results_attempts=NO_USABLE_RESULTS_ATTEMPTS,
        )
    except skyscanner_search.SearchError as exc:
        raise _probe_error(exc) from None
    return [_flight_probe_result(itinerary) for itinerary in itineraries]


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
    client: HttpClient | JsonHttpClient,
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
        with httpx.Client() as client:
            return run_probe(
                origin_iata=origin_iata,
                destination_iata=destination_iata,
                departure_date=args.departure_date,
                return_date=args.return_date,
                limit=limit,
                config=config,
                client=_HttpxProbeClient(client),
            )
    except ProbeError as exc:
        print(exc.safe_text(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
