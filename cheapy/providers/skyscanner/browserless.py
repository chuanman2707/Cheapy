"""Browserless BrowserQL bootstrap for Skyscanner sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import os
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cheapy.providers.skyscanner import errors


DEFAULT_BROWSERLESS_ENDPOINT = "https://production-sfo.browserless.io/stealth/bql"
DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS = 25.0
DEFAULT_BOOTSTRAP_PAGE_URL = "https://www.skyscanner.com.sg/transport/flights"
DEFAULT_BROWSERLESS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class BrowserlessResponse:
    status_code: int
    payload: object = field(repr=False)


@dataclass(frozen=True)
class BrowserlessSession:
    cookie_header: str = field(repr=False)
    user_agent: str


class BrowserlessClient(Protocol):
    def post_bql(
        self,
        *,
        endpoint: str,
        token: str,
        query: str,
        timeout_seconds: float,
    ) -> BrowserlessResponse: ...


class UrlopenBrowserlessClient:
    """Browserless client implemented with stdlib urllib."""

    def post_bql(
        self,
        *,
        endpoint: str,
        token: str,
        query: str,
        timeout_seconds: float,
    ) -> BrowserlessResponse:
        url = f"{endpoint}?{urlencode({'token': token})}"
        body = json.dumps({"query": query}, separators=(",", ":")).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = _decode_json_payload(response.read())
                return BrowserlessResponse(
                    status_code=int(getattr(response, "status", 200)),
                    payload=payload,
                )
        except HTTPError as exc:
            return BrowserlessResponse(
                status_code=int(exc.code),
                payload=_decode_http_error_payload(exc),
            )


def bootstrap_session(
    *,
    env: Mapping[str, str] = os.environ,
    client: BrowserlessClient | None = None,
    endpoint: str = DEFAULT_BROWSERLESS_ENDPOINT,
    page_url: str = DEFAULT_BOOTSTRAP_PAGE_URL,
    timeout_seconds: float = DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
) -> BrowserlessSession:
    token = env.get("BROWSERLESS_TOKEN", "").strip()
    if not token:
        raise errors.browserless_cookie_unavailable()
    browserless_client = client if client is not None else UrlopenBrowserlessClient()
    query = _session_query(page_url)
    try:
        response = browserless_client.post_bql(
            endpoint=endpoint,
            token=token,
            query=query,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        raise errors.browserless_bootstrap_failed(
            exception_type=type(exc).__name__,
        ) from None
    if response.status_code in {401, 403}:
        raise errors.blocked_error(http_status_code=response.status_code)
    if response.status_code == 429:
        raise errors.rate_limited_error(http_status_code=response.status_code)
    if response.status_code < 200 or response.status_code >= 300:
        raise errors.browserless_bootstrap_failed()
    navigation_status = _payload_navigation_status(response.payload)
    if navigation_status in {401, 403}:
        raise errors.blocked_error(http_status_code=navigation_status)
    if navigation_status == 429:
        raise errors.rate_limited_error(http_status_code=navigation_status)
    cookie_header = cookies_to_header(_payload_cookies(response.payload))
    if not cookie_header:
        raise errors.browserless_cookie_unavailable()
    user_agent = _payload_user_agent(response.payload)
    return BrowserlessSession(cookie_header=cookie_header, user_agent=user_agent)


def _session_query(page_url: str) -> str:
    return (
        "mutation SkyscannerBootstrap {"
        f" userAgent(userAgent: {json.dumps(DEFAULT_BROWSERLESS_USER_AGENT)}) {{ time }}"
        f" goto(url: {json.dumps(page_url)}, waitUntil: networkIdle) {{ status }}"
        " cookies { cookies { name value } }"
        "}"
    )


def _payload_cookies(payload: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    cookies = data.get("cookies")
    if isinstance(cookies, Mapping):
        nested_cookies = cookies.get("cookies")
        if isinstance(nested_cookies, list):
            return [cookie for cookie in nested_cookies if isinstance(cookie, Mapping)]
    if not isinstance(cookies, list):
        return []
    return [cookie for cookie in cookies if isinstance(cookie, Mapping)]


def _decode_http_error_payload(exc: HTTPError) -> object:
    try:
        return _decode_json_payload(exc.read())
    except Exception:
        return {}


def _decode_json_payload(body: bytes) -> object:
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _payload_navigation_status(payload: object) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    goto = data.get("goto")
    if not isinstance(goto, Mapping):
        return None
    status = goto.get("status")
    if isinstance(status, int):
        return status
    return None


def _payload_user_agent(payload: object) -> str:
    if isinstance(payload, Mapping):
        data = payload.get("data")
        if isinstance(data, Mapping):
            value = data.get("userAgent")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return DEFAULT_BROWSERLESS_USER_AGENT


def cookies_to_header(cookies: Sequence[Mapping[str, object]]) -> str:
    pairs: list[str] = []
    for cookie in cookies:
        raw_name = cookie.get("name", "")
        raw_value = cookie.get("value", "")
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            continue
        name = raw_name.strip()
        value = raw_value.strip()
        if name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)
