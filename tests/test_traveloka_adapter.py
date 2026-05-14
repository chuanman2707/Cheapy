from __future__ import annotations

from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from cheapy.models import ErrorCode
from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import adapter as traveloka_adapter
from cheapy.providers.traveloka.adapter import (
    TravelokaAdapter,
    TravelokaHTTPResponse,
    TravelokaProviderError,
    build_search_url,
)


def _one_way_request() -> ProviderExactOneWayRequest:
    return ProviderExactOneWayRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
    )


def _round_trip_request() -> ProviderExactRoundTripRequest:
    return ProviderExactRoundTripRequest(
        origin="SGN",
        destination="BKK",
        departure_date="2026-07-10",
        return_date="2026-07-17",
    )


def test_build_search_url_maps_one_way_request_to_safe_query() -> None:
    url = build_search_url(
        _one_way_request(),
        base_url="https://www.traveloka.com/en-en/flight",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.traveloka.com"
    assert parsed.path == "/en-en/flight"
    assert params["trip"] == ["oneway"]
    assert params["origin"] == ["SGN"]
    assert params["destination"] == ["BKK"]
    assert params["departureDate"] == ["2026-07-10"]
    assert params["currency"] == ["USD"]
    assert params["locale"] == ["en-en"]
    assert params["cabin"] == ["ECONOMY"]
    assert params["adults"] == ["1"]
    assert "returnDate" not in params


def test_build_search_url_maps_round_trip_request_to_safe_query() -> None:
    url = build_search_url(
        _round_trip_request(),
        base_url="https://www.traveloka.com/en-en/flight",
    )

    params = parse_qs(urlparse(url).query)
    assert params["trip"] == ["roundtrip"]
    assert params["origin"] == ["SGN"]
    assert params["destination"] == ["BKK"]
    assert params["departureDate"] == ["2026-07-10"]
    assert params["returnDate"] == ["2026-07-17"]
    assert params["currency"] == ["USD"]


def test_stdlib_http_get_reads_success_response_once_with_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_sizes: list[int] = []
    timeouts: list[float] = []

    class FakeResponse:
        status = 200
        headers = {"content-type": "application/json"}
        url = "https://example.test/final"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            return b'{"ok": true}'

    def fake_urlopen(request, timeout: float):
        timeouts.append(timeout)
        return FakeResponse()

    monkeypatch.setattr(traveloka_adapter, "urlopen", fake_urlopen)

    response = traveloka_adapter._stdlib_http_get(
        "https://example.test/search",
        {"User-Agent": "CheapyTest"},
        7.5,
        12,
    )

    assert timeouts == [7.5]
    assert read_sizes == [13]
    assert response == TravelokaHTTPResponse(
        status_code=200,
        body=b'{"ok": true}',
        content_type="application/json",
        final_url="https://example.test/final",
    )


def test_stdlib_http_get_reads_http_error_response_once_with_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    read_sizes: list[int] = []
    retained_errors: list[HTTPError] = []

    class FakeErrorBody:
        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            return b"blocked"

        def close(self) -> None:
            closed.append(True)
            return None

    def fake_urlopen(request, timeout: float):
        error = HTTPError(
            "https://example.test/search",
            503,
            "Service Unavailable",
            {"content-type": "text/plain"},
            FakeErrorBody(),
        )
        retained_errors.append(error)
        raise error

    monkeypatch.setattr(traveloka_adapter, "urlopen", fake_urlopen)

    response = traveloka_adapter._stdlib_http_get(
        "https://example.test/search",
        {"User-Agent": "CheapyTest"},
        7.5,
        12,
    )

    assert retained_errors
    assert read_sizes == [13]
    assert closed == [True]
    assert response == TravelokaHTTPResponse(
        status_code=503,
        body=b"blocked",
        content_type="text/plain",
        final_url="https://example.test/search",
    )


def test_stdlib_http_get_maps_timeout_without_raw_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout: float):
        raise TimeoutError("raw timeout secret")

    monkeypatch.setattr(traveloka_adapter, "urlopen", fake_urlopen)

    with pytest.raises(TravelokaProviderError) as exc_info:
        traveloka_adapter._stdlib_http_get(
            "https://example.test/search",
            {"User-Agent": "CheapyTest"},
            7.5,
            12,
        )

    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True
    assert exc_info.value.exception_type == "TimeoutError"
    assert exc_info.value.__cause__ is None
    assert "raw timeout secret" not in str(exc_info.value)


def test_adapter_fetches_once_without_retry() -> None:
    calls: list[str] = []

    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        calls.append(url)
        return TravelokaHTTPResponse(
            status_code=200,
            body=b'{"data": {"itineraries": []}}',
            content_type="application/json",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    payload = adapter.search_exact_one_way(_one_way_request())

    assert payload == {"data": {"itineraries": []}}
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("status_code", "failure_type", "error_code", "retryable"),
    [
        (403, "blocked", ErrorCode.PROVIDER_BLOCKED, False),
        (429, "rate_limited", ErrorCode.PROVIDER_RATE_LIMITED, True),
        (503, "transport_error", ErrorCode.PROVIDER_FAILED, True),
    ],
)
def test_adapter_maps_http_status_to_structured_error(
    status_code: int,
    failure_type: str,
    error_code: ErrorCode,
    retryable: bool,
) -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=status_code,
            body=b"blocked",
            content_type="text/plain",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == failure_type
    assert exc_info.value.error_code == error_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.http_status_code == status_code


def test_adapter_detects_bot_challenge_body() -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=b"<html><title>captcha required</title></html>",
            content_type="text/html",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.retryable is False


def test_adapter_detects_robot_check_body() -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=b"<html>robot check required</html>",
            content_type="text/html",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.retryable is False


@pytest.mark.parametrize(
    "body",
    [
        "<html>automated bot traffic detected</html>",
        "<html>verify you are not a bot</html>",
    ],
)
def test_adapter_detects_explicit_bot_challenge_phrases(body: str) -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=body.encode("utf-8"),
            content_type="text/html",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.retryable is False


@pytest.mark.parametrize(
    "body",
    [
        '<html><head><meta name="robots" content="index,follow"></head></html>',
        "<html><body>See the fare rules at the bottom of the page.</body></html>",
    ],
)
def test_adapter_returns_html_fallback_for_ordinary_html_with_bot_substrings(
    body: str,
) -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=body.encode("utf-8"),
            content_type="text/html",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    payload = adapter.search_exact_one_way(_one_way_request())

    assert payload == {"_html": body, "_content_type": "text/html"}


def test_adapter_detects_access_challenge_body() -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=b"<html><body>Access challenge required</body></html>",
            content_type="text/html",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "blocked"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_BLOCKED
    assert exc_info.value.retryable is False


def test_adapter_fetches_once_for_503_without_retry() -> None:
    calls: list[str] = []

    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        calls.append(url)
        return TravelokaHTTPResponse(
            status_code=503,
            body=b"service unavailable",
            content_type="text/plain",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert len(calls) == 1
    assert exc_info.value.failure_type == "transport_error"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is True
    assert exc_info.value.http_status_code == 503


def test_adapter_fetches_once_for_transport_exception_without_retry() -> None:
    calls: list[str] = []

    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        calls.append(url)
        raise RuntimeError("raw payload secret")

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert len(calls) == 1
    assert exc_info.value.failure_type == "transport_error"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is True
    assert exc_info.value.exception_type == "RuntimeError"
    assert str(exc_info.value) == "Traveloka transport failed."
    assert exc_info.value.__cause__ is None
    assert "raw payload secret" not in str(exc_info.value)


def test_adapter_fetches_once_for_timeout_without_raw_cause() -> None:
    calls: list[str] = []

    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        calls.append(url)
        raise TimeoutError("raw timeout secret")

    adapter = TravelokaAdapter(http_get=fake_http_get)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert len(calls) == 1
    assert exc_info.value.failure_type == "timeout"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_TIMEOUT
    assert exc_info.value.retryable is True
    assert exc_info.value.exception_type == "TimeoutError"
    assert exc_info.value.__cause__ is None
    assert "raw timeout secret" not in str(exc_info.value)


def test_adapter_rejects_oversized_response() -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        assert max_bytes == 16
        return TravelokaHTTPResponse(
            status_code=200,
            body=b"x" * 17,
            content_type="application/json",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get, max_response_bytes=16)

    with pytest.raises(TravelokaProviderError) as exc_info:
        adapter.search_exact_one_way(_one_way_request())

    assert exc_info.value.failure_type == "response_too_large"
    assert exc_info.value.error_code == ErrorCode.PROVIDER_FAILED
    assert exc_info.value.retryable is False


def test_adapter_rejects_invalid_max_response_bytes() -> None:
    with pytest.raises(ValueError, match="max_response_bytes"):
        TravelokaAdapter(max_response_bytes=0)


def test_adapter_rejects_invalid_timeout_seconds() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        TravelokaAdapter(timeout_seconds=0)


def test_adapter_returns_html_fallback_for_invalid_json_body() -> None:
    def fake_http_get(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TravelokaHTTPResponse:
        return TravelokaHTTPResponse(
            status_code=200,
            body=b"{invalid-json",
            content_type="application/json",
            final_url=url,
        )

    adapter = TravelokaAdapter(http_get=fake_http_get)

    payload = adapter.search_exact_one_way(_one_way_request())

    assert payload == {
        "_html": "{invalid-json",
        "_content_type": "application/json",
    }
