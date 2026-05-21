from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess

import pytest

from cheapy.providers.skyscanner.client import CurlClient, SkyscannerHttpError


def test_curl_client_post_uses_temp_config_without_cookie_in_argv() -> None:
    calls: list[dict[str, object]] = []

    def runner(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        config_path = args[args.index("--config") + 1]
        body_path = args[args.index("--data-binary") + 1].removeprefix("@")
        config_mode = stat.S_IMODE(os.stat(config_path).st_mode)
        body_mode = stat.S_IMODE(os.stat(body_path).st_mode)
        calls.append(
            {
                "args": args,
                "config": Path(config_path).read_text(encoding="utf-8"),
                "body": Path(body_path).read_text(encoding="utf-8"),
                "config_mode": config_mode,
                "body_mode": body_mode,
            }
        )
        return subprocess.CompletedProcess(args, 0, stdout='{"ok":true}\n200', stderr="")

    client = CurlClient(runner=runner)

    response = client.post_json(
        "https://example.test/search",
        json_body={"secret": "body-token"},
        headers={"cookie": "session=secret-cookie", "x-test": "1"},
        timeout_seconds=3.0,
    )

    call = calls[0]
    args = call["args"]
    assert isinstance(args, list)
    assert all("secret-cookie" not in arg for arg in args)
    assert call["config_mode"] == 0o600
    assert call["body_mode"] == 0o600
    assert '"session=secret-cookie"' in str(call["config"])
    assert call["body"] == '{"secret":"body-token"}'
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_curl_client_get_encodes_params_and_omits_body() -> None:
    calls: list[dict[str, object]] = []

    def runner(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        config_path = args[args.index("--config") + 1]
        config_mode = stat.S_IMODE(os.stat(config_path).st_mode)
        calls.append(
            {
                "args": args,
                "config": Path(config_path).read_text(encoding="utf-8"),
                "config_mode": config_mode,
            }
        )
        return subprocess.CompletedProcess(args, 0, stdout='{"ok":true}\n200', stderr="")

    client = CurlClient(runner=runner)

    client.get_json(
        "https://example.test/autosuggest?existing=url-secret",
        params={"q": "SIN/SGN", "enabled": True, "token": "query-secret"},
        headers={"cookie": "session=secret-cookie"},
        timeout_seconds=3.0,
    )

    call = calls[0]
    args = call["args"]
    assert isinstance(args, list)
    assert "--data-binary" not in args
    assert call["config_mode"] == 0o600
    assert (
        '"https://example.test/autosuggest?existing=url-secret'
        '&q=SIN%2FSGN&enabled=True&token=query-secret"'
    ) in str(call["config"])
    assert all("secret-cookie" not in arg for arg in args)
    assert all("url-secret" not in arg for arg in args)
    assert all("query-secret" not in arg for arg in args)
    assert all("SIN%2FSGN" not in arg for arg in args)


def test_curl_client_transport_error_is_sanitized() -> None:
    def runner(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 7, stdout="", stderr="raw secret-cookie")

    client = CurlClient(runner=runner)

    with pytest.raises(SkyscannerHttpError) as exc_info:
        client.get_json(
            "https://example.test/autosuggest",
            params={},
            headers={"cookie": "session=secret-cookie"},
            timeout_seconds=3.0,
        )

    assert "secret-cookie" not in str(exc_info.value)
    assert "raw secret-cookie" not in str(exc_info.value)
    assert exc_info.value.failure_type == "transport_error"
