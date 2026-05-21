"""Runtime-safe HTTP helpers for the Skyscanner provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json as jsonlib
import os
import subprocess
import tempfile
from typing import Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit


class JsonHttpResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class JsonHttpClient(Protocol):
    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> JsonHttpResponse: ...

    def post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> JsonHttpResponse: ...


class SkyscannerHttpError(Exception):
    """Safe transport error raised by HTTP clients."""

    def __init__(
        self,
        *,
        failure_type: str,
        message_en: str,
        http_status_code: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(message_en)
        self.failure_type = failure_type
        self.message_en = message_en
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


def _url_with_params(url: str, params: Mapping[str, object]) -> str:
    query = urlencode({key: str(value) for key, value in params.items()})
    if not query:
        return url
    parsed = urlsplit(url)
    combined_query = f"{parsed.query}&{query}" if parsed.query else query
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            combined_query,
            parsed.fragment,
        )
    )


class CurlClient:
    """Small curl wrapper that keeps headers and JSON bodies out of argv."""

    def __init__(
        self,
        *,
        curl_path: str = "curl",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._curl_path = curl_path
        self._runner = runner

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> CurlResponse:
        return self._request(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout_seconds=timeout_seconds,
            json_body=None,
        )

    def post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> CurlResponse:
        return self._request(
            "POST",
            url,
            params={},
            headers=headers,
            timeout_seconds=timeout_seconds,
            json_body=json_body,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
        json_body: Mapping[str, object] | None,
    ) -> CurlResponse:
        final_url = _url_with_params(url, params)
        with tempfile.TemporaryDirectory(prefix="skyscanner-curl-") as tmpdir:
            config_path = os.path.join(tmpdir, "curl.conf")
            self._write_private_text(
                config_path,
                "\n".join(self._config_lines(final_url, headers)) + "\n",
            )
            args = [
                self._curl_path,
                "--silent",
                "--show-error",
                "--compressed",
                "--http2",
                "--max-time",
                str(timeout_seconds),
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
                    timeout=timeout_seconds + 5.0,
                    check=False,
                )
            except Exception as exc:
                raise SkyscannerHttpError(
                    failure_type="transport_error",
                    message_en=f"Skyscanner HTTP request failed with {type(exc).__name__}.",
                    exception_type=type(exc).__name__,
                ) from None
        if completed.returncode != 0:
            raise SkyscannerHttpError(
                failure_type="transport_error",
                message_en=(
                    "Skyscanner HTTP request failed with exit code "
                    f"{completed.returncode}."
                ),
            )
        body, separator, status_text = completed.stdout.rpartition("\n")
        if not separator:
            raise SkyscannerHttpError(
                failure_type="transport_error",
                message_en="Skyscanner HTTP response did not include a status code.",
            )
        try:
            status_code = int(status_text)
        except ValueError:
            raise SkyscannerHttpError(
                failure_type="transport_error",
                message_en="Skyscanner HTTP response included an invalid status code.",
            ) from None
        return CurlResponse(status_code=status_code, body=body)

    @staticmethod
    def _config_lines(url: str, headers: Mapping[str, str]) -> list[str]:
        lines = [f"url = {_curl_config_quote(url)}"]
        for name, value in headers.items():
            if name.lower() == "cookie":
                lines.append(f"cookie = {_curl_config_quote(value)}")
            else:
                lines.append(f"header = {_curl_config_quote(f'{name}: {value}')}")
        return lines

    @staticmethod
    def _write_private_text(path: str, value: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(value)
