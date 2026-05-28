"""Skyscanner HTTP adapter core."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import json as jsonlib
import os
import re
import subprocess
import tempfile
import time
from typing import Protocol
from urllib.parse import quote, urlencode, urljoin, urlsplit
import uuid

from cheapy.models import ErrorCode
from cheapy.providers.base import ProviderExactOneWayRequest, ProviderExactRoundTripRequest


DEFAULT_BASE_URL = "https://www.skyscanner.com.sg"
DEFAULT_TIMEOUT_SECONDS = 20.0
SEARCH_POLL_ATTEMPTS = 8
SEARCH_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
AUTOSUGGEST_PATH = "/g/autosuggest-search/api/v1/search-flight"
SEARCH_PATH = "/g/radar/api/v2/web-unified-search/"
IATA_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


@dataclass(frozen=True)
class SkyscannerConfig:
    base_url: str
    market: str
    locale: str
    currency: str
    cookie: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    user_agent: str = DEFAULT_USER_AGENT
    deadline_monotonic: float | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SkyscannerEntity:
    iata: str
    entity_id: str
    name: str
    place_type: str | None = None
    parent_entity_id: str | None = None
    place_of_stay_entity_id: str | None = None


@dataclass(frozen=True)
class SkyscannerLegCandidate:
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    airline_code: str
    flight_number: str
    duration_minutes: int


@dataclass(frozen=True)
class SkyscannerItineraryCandidate:
    item_id: str
    price_amount: float
    currency: str
    legs: tuple[SkyscannerLegCandidate, ...]
    total_duration_minutes: int
    stops: int


class SkyscannerProviderError(Exception):
    """Sanitized adapter error safe for provider-level mapping."""

    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        error_code: ErrorCode = ErrorCode.PROVIDER_FAILED,
        retryable: bool = False,
        http_status_code: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
        self.error_code = error_code
        self.retryable = retryable
        self.http_status_code = http_status_code
        self.exception_type = exception_type


@dataclass(frozen=True)
class CurlResponse:
    status_code: int
    body: str

    def json(self) -> object:
        return jsonlib.loads(self.body)


def _curl_config_quote(value: str) -> str:
    sanitized = value.replace("\r", " ").replace("\n", " ")
    escaped = sanitized.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class CurlClient:
    def __init__(
        self,
        *,
        curl_path: str = "curl",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._curl_path = curl_path
        self._runner = runner

    def get(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> CurlResponse:
        return self._request("GET", url, params=params, headers=headers, timeout=timeout)

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> CurlResponse:
        return self._request(
            "POST",
            url,
            params={},
            headers=headers,
            timeout=timeout,
            json_body=json,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
        json_body: dict[str, object] | None = None,
    ) -> CurlResponse:
        query = urlencode({key: str(value) for key, value in params.items()})
        final_url = f"{url}?{query}" if query else url
        with tempfile.TemporaryDirectory(prefix="skyscanner-curl-") as tmpdir:
            config_path = os.path.join(tmpdir, "curl.conf")
            config_lines = [
                *self._config_lines(headers),
                f"url = {_curl_config_quote(final_url)}",
            ]
            self._write_private_text(
                config_path,
                "\n".join(config_lines) + "\n",
            )

            args = [
                self._curl_path,
                "--silent",
                "--show-error",
                "--compressed",
                "--http2",
                "--max-time",
                str(timeout),
                "--config",
                config_path,
                "--request",
                method,
                "--write-out",
                "\n%{http_code}",
            ]
            if json_body is not None:
                body_path = os.path.join(tmpdir, "body.json")
                body = jsonlib.dumps(json_body, separators=(",", ":"))
                self._write_private_text(body_path, body)
                args.extend(["--data-binary", f"@{body_path}"])
            try:
                completed = self._runner(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=timeout + 5.0,
                    check=False,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"curl request failed with {type(exc).__name__}"
                ) from None

        if completed.returncode != 0:
            raise RuntimeError(f"curl request failed with exit code {completed.returncode}")
        body, separator, status_text = completed.stdout.rpartition("\n")
        if not separator:
            raise RuntimeError("curl response did not include an HTTP status code")
        try:
            status_code = int(status_text)
        except ValueError:
            raise RuntimeError("curl response included an invalid HTTP status code") from None
        return CurlResponse(status_code=status_code, body=body)

    @staticmethod
    def _config_lines(headers: dict[str, str]) -> list[str]:
        lines: list[str] = []
        for name, value in headers.items():
            if name.lower() == "cookie":
                lines.append(f"cookie = {_curl_config_quote(value)}")
            else:
                lines.append(f"header = {_curl_config_quote(f'{name}: {value}')}")
        return lines

    @staticmethod
    def _write_private_text(path: str, value: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(value)


def normalize_iata(value: str) -> str:
    iata = value.strip().upper()
    if not IATA_RE.fullmatch(iata):
        raise SkyscannerProviderError(
            failure_type="invalid_argument",
            message_en="IATA code must be exactly 3 letters.",
        )
    return iata


def date_parts(value: str) -> dict[str, str]:
    if not DATE_RE.fullmatch(value):
        raise SkyscannerProviderError(
            failure_type="invalid_argument",
            message_en="Date must use YYYY-MM-DD format.",
        )
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SkyscannerProviderError(
            failure_type="invalid_argument",
            message_en="Date must use YYYY-MM-DD format.",
        ) from exc
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
        raise SkyscannerProviderError(
            failure_type="invalid_argument",
            message_en="Return date must not be earlier than departure date.",
        )


def _cookie_value(cookie: str, name: str) -> str | None:
    expected = name.lower()
    for part in cookie.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == expected:
            text = value.strip()
            return text or None
    return None


def _field(mapping: object, names: Sequence[str]) -> object | None:
    if not isinstance(mapping, dict):
        return None
    for name in names:
        if "." in name:
            current: object | None = mapping
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


def config_from_env(
    env: Mapping[str, str],
    *,
    market: str = "SG",
    locale: str = "en-GB",
    currency: str = "SGD",
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    deadline_monotonic: float | None = None,
) -> SkyscannerConfig:
    cookie = env.get("CHEAPY_SKYSCANNER_COOKIE", "").strip()
    if not cookie:
        raise SkyscannerProviderError(
            failure_type="missing_cookie",
            message_en="Skyscanner cookie is not configured.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    return SkyscannerConfig(
        base_url=base_url.rstrip("/"),
        market=market,
        locale=locale,
        currency=currency,
        cookie=cookie,
        timeout_seconds=timeout_seconds,
        user_agent=(
            env.get("CHEAPY_SKYSCANNER_USER_AGENT", DEFAULT_USER_AGENT).strip()
            or DEFAULT_USER_AGENT
        ),
        deadline_monotonic=deadline_monotonic,
    )


def request_headers(config: SkyscannerConfig, *, accept_json: bool = True) -> dict[str, str]:
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


def _http_error(status_code: int, *, operation: str) -> SkyscannerProviderError:
    if status_code in (401, 403):
        return SkyscannerProviderError(
            failure_type="blocked",
            message_en=f"{operation} returned HTTP {status_code}.",
            error_code=ErrorCode.PROVIDER_BLOCKED,
            retryable=False,
            http_status_code=status_code,
        )
    if status_code == 429:
        return SkyscannerProviderError(
            failure_type="rate_limited",
            message_en=f"{operation} returned HTTP {status_code}.",
            error_code=ErrorCode.PROVIDER_RATE_LIMITED,
            retryable=True,
            http_status_code=status_code,
        )
    return SkyscannerProviderError(
        failure_type="http_error",
        message_en=f"{operation} returned HTTP {status_code}.",
        error_code=ErrorCode.PROVIDER_FAILED,
        retryable=False,
        http_status_code=status_code,
    )


def _timeout_error(message_en: str) -> SkyscannerProviderError:
    return SkyscannerProviderError(
        failure_type="timeout",
        message_en=message_en,
        error_code=ErrorCode.PROVIDER_TIMEOUT,
        retryable=True,
    )


def _remaining_timeout_seconds(config: SkyscannerConfig) -> float:
    if config.deadline_monotonic is None:
        return config.timeout_seconds
    remaining = min(config.timeout_seconds, config.deadline_monotonic - time.monotonic())
    if remaining <= 0:
        raise _timeout_error("Skyscanner attempt deadline expired.")
    return remaining


def _sleep_with_deadline(config: SkyscannerConfig, seconds: float) -> None:
    if seconds <= 0:
        return
    if config.deadline_monotonic is None:
        time.sleep(seconds)
        return
    remaining = config.deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise _timeout_error("Skyscanner attempt deadline expired.")
    time.sleep(min(seconds, remaining))


def _read_json_response(response: HttpResponse, *, operation: str) -> object:
    if response.status_code < 200 or response.status_code >= 300:
        raise _http_error(response.status_code, operation=operation)
    try:
        return response.json()
    except Exception as exc:
        raise SkyscannerProviderError(
            failure_type="parse_error",
            message_en=f"{operation} response could not be parsed as JSON.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
            exception_type=type(exc).__name__,
        ) from None


def _read_json_object_response(
    response: HttpResponse,
    *,
    operation: str,
) -> dict[str, object]:
    payload = _read_json_response(response, operation=operation)
    if not isinstance(payload, dict):
        raise SkyscannerProviderError(
            failure_type="parse_error",
            message_en=f"{operation} response was not a JSON object.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    return payload


def _places_from_payload(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise SkyscannerProviderError(
            failure_type="parse_error",
            message_en="Autosuggest response did not contain a JSON object.",
        )
    places = payload.get("places", payload.get("Places"))
    if not isinstance(places, list):
        raise SkyscannerProviderError(
            failure_type="parse_error",
            message_en="Autosuggest response did not contain a places list.",
        )
    return places


def _candidate_to_entity(
    candidate: object,
    *,
    requested_iata: str,
    is_destination: bool,
) -> SkyscannerEntity | None:
    iata = _as_str(_field(candidate, ("iataCode", "IataCode", "iata", "IATA")))
    place_id = _as_str(_field(candidate, ("placeId", "PlaceId")))
    if iata is None and place_id is not None and IATA_RE.fullmatch(place_id.upper()):
        iata = place_id
    if iata is None or iata.upper() != requested_iata:
        return None
    entity_id = _as_str(
        _field(candidate, ("entityId", "EntityId", "GeoId", "geoId", "PlaceId"))
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
    return SkyscannerEntity(
        iata=requested_iata,
        entity_id=entity_id,
        name=name,
        place_type=place_type,
        parent_entity_id=parent_id,
        place_of_stay_entity_id=parent_id if is_destination and parent_id else None,
    )


def _is_airport(entity: SkyscannerEntity) -> bool:
    if entity.place_type is None:
        return False
    normalized = entity.place_type.upper()
    return "AIRPORT" in normalized or normalized == "AIRPORT"


def get_entity_id(
    iata_code: str,
    *,
    config: SkyscannerConfig,
    client: HttpClient,
    is_destination: bool = False,
) -> SkyscannerEntity:
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
            timeout=_remaining_timeout_seconds(config),
        )
    except SkyscannerProviderError:
        raise
    except Exception as exc:
        raise SkyscannerProviderError(
            failure_type="transport_error",
            message_en="Autosuggest request failed.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=type(exc).__name__,
        ) from None

    payload = _read_json_response(response, operation="Autosuggest")
    places = _places_from_payload(payload)
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
        raise SkyscannerProviderError(
            failure_type="entity_not_found",
            message_en=f"No Skyscanner entity matched IATA {requested_iata}.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )

    airport_entities = [entity for entity in entities if _is_airport(entity)]
    preferred = airport_entities or entities
    if len(preferred) > 1:
        raise SkyscannerProviderError(
            failure_type="entity_ambiguous",
            message_en=f"Multiple Skyscanner entities matched IATA {requested_iata}.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    return preferred[0]


def _entity_ref(entity: SkyscannerEntity) -> dict[str, str]:
    return {"@type": "entity", "entityId": entity.entity_id}


def build_search_body(
    *,
    origin: SkyscannerEntity,
    destination: SkyscannerEntity,
    departure_date: str,
    return_date: str | None,
    adults: int,
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
        "adults": adults,
        "legs": legs,
    }


def _referer_date(value: str) -> str:
    parts = date_parts(value)
    return f"{parts['year'][2:]}{parts['month']}{parts['day']}"


def _search_referer(
    *,
    origin: SkyscannerEntity,
    destination: SkyscannerEntity,
    departure_date: str,
    return_date: str | None,
    adults: int,
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
        f"{config.base_url}{path}"
        f"?adultsv2={adults}&cabinclass=economy&childrenv2=&ref=home&rtn={rtn}"
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
    headers["origin"] = config.base_url
    headers["referer"] = referer
    headers["user-agent"] = config.user_agent
    if include_content_type:
        headers["content-type"] = "application/json"
    headers["x-skyscanner-viewid"] = view_id
    return headers


def _search_results(payload: object) -> list[object] | None:
    results = _field(_field(payload, ("itineraries",)), ("results",))
    return results if isinstance(results, list) else None


def _poll_search_session(
    *,
    session_id: str,
    config: SkyscannerConfig,
    client: HttpClient,
    view_id: str,
    referer: str,
) -> dict[str, object]:
    url = urljoin(
        config.base_url + "/",
        f"{SEARCH_PATH.lstrip('/')}{quote(session_id, safe='')}",
    )
    try:
        response = client.get(
            url,
            params={},
            headers=search_headers(
                config,
                view_id=view_id,
                referer=referer,
                include_content_type=False,
            ),
            timeout=_remaining_timeout_seconds(config),
        )
    except SkyscannerProviderError:
        raise
    except Exception as exc:
        raise SkyscannerProviderError(
            failure_type="transport_error",
            message_en="Search poll failed.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=type(exc).__name__,
        ) from None
    return _read_json_object_response(response, operation="Search poll")


def _search_payload(
    *,
    origin: SkyscannerEntity,
    destination: SkyscannerEntity,
    departure_date: str,
    return_date: str | None,
    adults: int,
    config: SkyscannerConfig,
    client: HttpClient,
) -> dict[str, object]:
    url = urljoin(config.base_url + "/", SEARCH_PATH.lstrip("/"))
    view_id = str(uuid.uuid4())
    referer = _search_referer(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
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
        response = client.post(
            url,
            json=body,
            headers=search_headers(config, view_id=view_id, referer=referer),
            timeout=_remaining_timeout_seconds(config),
        )
    except SkyscannerProviderError:
        raise
    except Exception as exc:
        raise SkyscannerProviderError(
            failure_type="transport_error",
            message_en="Search request failed.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=True,
            exception_type=type(exc).__name__,
        ) from None

    payload = _read_json_object_response(response, operation="Search")
    initial_payload = payload
    status = _field(payload.get("context"), ("status",))
    if status == "incomplete":
        session_id = _as_str(_field(payload.get("context"), ("sessionId",)))
        if session_id is None:
            raise SkyscannerProviderError(
                failure_type="timeout",
                message_en="Search did not complete.",
                error_code=ErrorCode.PROVIDER_TIMEOUT,
                retryable=True,
            )
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
                _sleep_with_deadline(config, SEARCH_POLL_INTERVAL_SECONDS)
        if (
            status == "complete"
            and _search_results(payload) == []
            and bool(_search_results(initial_payload))
        ):
            payload = initial_payload
    if status != "complete":
        raise SkyscannerProviderError(
            failure_type="timeout",
            message_en="Search did not complete.",
            error_code=ErrorCode.PROVIDER_TIMEOUT,
            retryable=True,
        )
    if not isinstance(_search_results(payload), list):
        raise SkyscannerProviderError(
            failure_type="parse_error",
            message_en="Search response did not contain itineraries.results.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
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


def _safe_transport_deeplink(raw_url: str, *, config: SkyscannerConfig) -> bool:
    parsed = urlsplit(raw_url)
    if parsed.scheme == "" and parsed.netloc == "":
        return raw_url.startswith("/transport_deeplink/")
    base = urlsplit(config.base_url)
    return (
        parsed.scheme == base.scheme
        and parsed.netloc.lower() == base.netloc.lower()
        and parsed.path.startswith("/transport_deeplink/")
    )


def _has_usable_pricing_option(
    itinerary: object,
    *,
    config: SkyscannerConfig,
) -> bool:
    if not isinstance(itinerary, dict):
        return False
    options = itinerary.get("pricingOptions")
    if not isinstance(options, list):
        return False
    for option in options:
        if _float_value(_field(option, ("price.amount",))) is None:
            continue
        items = option.get("items") if isinstance(option, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            raw_url = _as_str(_field(item, ("url",)))
            if raw_url is not None and _safe_transport_deeplink(raw_url, config=config):
                return True
    return False


def _segment_flight_number(segment: object) -> tuple[str, str] | None:
    flight_number = _as_str(_field(segment, ("flightNumber", "flightNo", "number")))
    if flight_number is None:
        return None
    carrier = _field(segment, ("marketingCarrier",))
    carrier_code = _as_str(_field(carrier, ("displayCode", "alternateId", "id")))
    if carrier_code is None:
        carrier_code = ""
    compact_number = flight_number.replace(" ", "")
    compact_carrier = carrier_code.replace(" ", "")
    if compact_carrier and compact_number.upper().startswith(compact_carrier.upper()):
        return compact_carrier, compact_number
    return compact_carrier, f"{compact_carrier}{compact_number}"


def _candidate_leg(leg: object) -> SkyscannerLegCandidate | None:
    if not isinstance(leg, dict):
        return None
    origin = _as_str(_field(leg, ("origin.displayCode",)))
    destination = _as_str(_field(leg, ("destination.displayCode",)))
    departure = _as_str(_field(leg, ("departure",)))
    arrival = _as_str(_field(leg, ("arrival",)))
    duration = _int_value(_field(leg, ("durationInMinutes",)))
    segments = leg.get("segments")
    if (
        origin is None
        or destination is None
        or departure is None
        or arrival is None
        or duration is None
        or not isinstance(segments, list)
        or len(segments) != 1
    ):
        return None
    segment = segments[0]
    segment_origin = _as_str(_field(segment, ("origin.displayCode",)))
    segment_destination = _as_str(_field(segment, ("destination.displayCode",)))
    segment_departure = _as_str(_field(segment, ("departure",)))
    segment_arrival = _as_str(_field(segment, ("arrival",)))
    if (
        segment_origin != origin
        or segment_destination != destination
        or segment_departure != departure
        or segment_arrival != arrival
    ):
        return None
    flight = _segment_flight_number(segment)
    if flight is None:
        return None
    airline_code, flight_number = flight
    if not airline_code or not flight_number:
        return None
    return SkyscannerLegCandidate(
        origin=origin,
        destination=destination,
        departure_time=departure,
        arrival_time=arrival,
        airline_code=airline_code,
        flight_number=flight_number,
        duration_minutes=duration,
    )


def _extract_candidate(
    itinerary: object,
    *,
    config: SkyscannerConfig,
    expected_routes: tuple[tuple[str, str], ...],
) -> SkyscannerItineraryCandidate | None:
    if not isinstance(itinerary, dict):
        return None
    item_id = _as_str(_field(itinerary, ("id", "itemId")))
    price_amount = _float_value(_field(itinerary, ("price.raw",)))
    legs_payload = itinerary.get("legs")
    if (
        item_id is None
        or price_amount is None
        or not isinstance(legs_payload, list)
        or not _has_usable_pricing_option(itinerary, config=config)
    ):
        return None
    legs = tuple(
        candidate
        for leg in legs_payload
        if (candidate := _candidate_leg(leg)) is not None
    )
    if not legs or len(legs) != len(legs_payload) or len(legs) != len(expected_routes):
        return None
    for leg, (expected_origin, expected_destination) in zip(
        legs,
        expected_routes,
        strict=True,
    ):
        if leg.origin != expected_origin or leg.destination != expected_destination:
            return None
    total_duration = sum(leg.duration_minutes for leg in legs)
    stop_count = 0
    for leg in legs_payload:
        stops = _int_value(_field(leg, ("stopCount",)))
        if stops is None or stops < 0:
            return None
        stop_count += stops
    return SkyscannerItineraryCandidate(
        item_id=item_id,
        price_amount=price_amount,
        currency=config.currency,
        legs=legs,
        total_duration_minutes=total_duration,
        stops=stop_count,
    )


def _extract_candidates(
    payload: object,
    *,
    config: SkyscannerConfig,
    expected_routes: tuple[tuple[str, str], ...],
) -> list[SkyscannerItineraryCandidate]:
    results = _search_results(payload)
    if not isinstance(results, list):
        raise SkyscannerProviderError(
            failure_type="parse_error",
            message_en="Search response did not contain itineraries.results.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    candidates = [
        candidate
        for itinerary in results
        if (
            candidate := _extract_candidate(
                itinerary,
                config=config,
                expected_routes=expected_routes,
            )
        )
        is not None
    ]
    if not candidates:
        raise SkyscannerProviderError(
            failure_type="no_usable_results",
            message_en="Search completed but no usable itinerary was found.",
            error_code=ErrorCode.PROVIDER_FAILED,
            retryable=False,
        )
    return sorted(candidates, key=lambda candidate: (candidate.price_amount, candidate.item_id))


class SkyscannerAdapter:
    def __init__(self, *, config: SkyscannerConfig, client: HttpClient) -> None:
        self._config = config
        self._client = client

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        client: HttpClient | None = None,
        market: str = "SG",
        locale: str = "en-GB",
        currency: str = "SGD",
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        deadline_monotonic: float | None = None,
    ) -> "SkyscannerAdapter":
        config = config_from_env(
            env,
            market=market,
            locale=locale,
            currency=currency,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        )
        return cls(config=config, client=client or CurlClient())

    def search_exact_one_way(
        self,
        request: ProviderExactOneWayRequest,
    ) -> list[SkyscannerItineraryCandidate]:
        return self._search(
            origin_iata=request.origin,
            destination_iata=request.destination,
            departure_date=request.departure_date,
            return_date=None,
            adults=request.passengers.adults,
        )

    def search_exact_round_trip(
        self,
        request: ProviderExactRoundTripRequest,
    ) -> list[SkyscannerItineraryCandidate]:
        return self._search(
            origin_iata=request.origin,
            destination_iata=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            adults=request.passengers.adults,
        )

    def _search(
        self,
        *,
        origin_iata: str,
        destination_iata: str,
        departure_date: str,
        return_date: str | None,
        adults: int,
    ) -> list[SkyscannerItineraryCandidate]:
        origin = get_entity_id(
            origin_iata,
            config=self._config,
            client=self._client,
            is_destination=False,
        )
        destination = get_entity_id(
            destination_iata,
            config=self._config,
            client=self._client,
            is_destination=True,
        )
        payload = _search_payload(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            config=self._config,
            client=self._client,
        )
        expected_routes = (
            ((origin.iata, destination.iata),)
            if return_date is None
            else ((origin.iata, destination.iata), (destination.iata, origin.iata))
        )
        return _extract_candidates(
            payload,
            config=self._config,
            expected_routes=expected_routes,
        )
