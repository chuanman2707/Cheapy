from __future__ import annotations

import pytest

from cheapy.providers.skyscanner import scan_graphql_bundles as scanner


def test_validate_https_url_accepts_https_url_with_host() -> None:
    assert (
        scanner.validate_https_url(
            "https://www.skyscanner.net/transport/flights/sgn/bkk/"
        )
        == "https://www.skyscanner.net/transport/flights/sgn/bkk/"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.skyscanner.net/transport/flights/sgn/bkk/",
        "https:///missing-host",
        "not-a-url",
    ],
)
def test_validate_https_url_rejects_invalid_or_non_https_urls(url: str) -> None:
    with pytest.raises(scanner.ScannerFatalError) as exc_info:
        scanner.validate_https_url(url)

    error = exc_info.value.to_error_payload()
    assert error["schema_version"] == "1"
    assert error["error"] is True
    assert error["error_type"] == "invalid_url"
    assert error["message"] == "Entry URL must be an HTTPS URL with a host."
    assert error["details"] == {"target_url": url}


def test_origin_tuple_normalizes_default_https_port() -> None:
    assert scanner.origin_tuple("https://www.skyscanner.net/path") == (
        "https",
        "www.skyscanner.net",
        443,
    )
    assert scanner.origin_tuple("https://www.skyscanner.net:443/path") == (
        "https",
        "www.skyscanner.net",
        443,
    )


def test_discover_same_origin_scripts_resolves_and_filters_sources() -> None:
    html = """
    <html>
      <head>
        <script src="/assets/app.js"></script>
        <script src="https://www.skyscanner.net/assets/vendor.js"></script>
        <script src="https://cdn.example.test/analytics.js"></script>
        <script>window.inline = true;</script>
      </head>
    </html>
    """

    discovery = scanner.discover_same_origin_scripts(
        html,
        final_entry_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
    )

    assert discovery.script_count == 3
    assert discovery.same_origin_urls == [
        "https://www.skyscanner.net/assets/app.js",
        "https://www.skyscanner.net/assets/vendor.js",
    ]
    assert discovery.skipped_cross_origin_script_count == 1


def test_extract_graphql_matches_finds_operation_names() -> None:
    text = """
    query FlightSearchQuery($input: FlightSearchInput!) { search(input: $input) { id } }
    mutation TrackFlightSearchMutation { track { ok } }
    subscription PriceAlertSubscription { priceChanged { amount } }
    """

    matches = scanner.extract_graphql_matches(text)

    assert matches["operation_names"] == [
        "FlightSearchQuery",
        "PriceAlertSubscription",
        "TrackFlightSearchMutation",
    ]


def test_extract_graphql_matches_finds_persisted_query_ids() -> None:
    text = """
    {"sha256Hash":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}
    {"operationId":"flightSearch_abc12345"}
    {"queryId":"query_67890_xyz"}
    """

    matches = scanner.extract_graphql_matches(text)

    assert matches["persisted_query_ids"] == [
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "flightSearch_abc12345",
        "query_67890_xyz",
    ]


def test_extract_graphql_matches_finds_graphql_paths_and_deduplicates() -> None:
    text = """
    fetch("/graphql");
    fetch('/graphql');
    const endpoint = "/g/conductor/graphql";
    const url = "https://www.skyscanner.net/graphql";
    """

    matches = scanner.extract_graphql_matches(text)

    assert matches["graphql_paths"] == [
        "/g/conductor/graphql",
        "/graphql",
        "https://www.skyscanner.net/graphql",
    ]


class FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, name: str, default: str = "") -> str:
        return self._values.get(name.lower(), default)


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int,
        content_type: str,
        body: bytes,
    ) -> None:
        self.url = url
        self.status = status
        self.headers = FakeHeaders({"content-type": content_type})
        self._body = body

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._body
        return self._body[:size]


class FakeOpener:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.requests: list[object] = []
        self.timeouts: list[float] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_fetch_url_reads_at_most_byte_limit_plus_one() -> None:
    opener = FakeOpener(
        FakeResponse(
            url="https://www.skyscanner.net/assets/app.js",
            status=200,
            content_type="application/javascript",
            body=b"abcdef",
        )
    )

    result = scanner.fetch_url(
        "https://www.skyscanner.net/assets/app.js",
        timeout_seconds=3,
        max_bytes=5,
        allowed_origin_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
        opener=opener,
    )

    assert isinstance(result, scanner.FetchSuccess)
    assert result.body == b"abcde"
    assert result.truncated is True
    assert result.status_code == 200
    assert result.content_type == "application/javascript"
    assert result.final_url == "https://www.skyscanner.net/assets/app.js"
    assert opener.timeouts == [3]


def test_fetch_url_blocks_cross_origin_final_url() -> None:
    opener = FakeOpener(
        FakeResponse(
            url="https://evil.example.test/assets/app.js",
            status=200,
            content_type="application/javascript",
            body=b"console.log(1)",
        )
    )

    result = scanner.fetch_url(
        "https://www.skyscanner.net/assets/app.js",
        timeout_seconds=3,
        max_bytes=100,
        allowed_origin_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
        opener=opener,
    )

    assert isinstance(result, scanner.FetchFailure)
    assert result.error_type == "cross_origin_redirect"
    assert result.url == "https://www.skyscanner.net/assets/app.js"
    assert result.details == {"final_url": "https://evil.example.test/assets/app.js"}


def test_fetch_url_reports_network_failure() -> None:
    opener = FakeOpener(TimeoutError("slow"))

    result = scanner.fetch_url(
        "https://www.skyscanner.net/assets/app.js",
        timeout_seconds=3,
        max_bytes=100,
        allowed_origin_url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
        opener=opener,
    )

    assert isinstance(result, scanner.FetchFailure)
    assert result.error_type == "fetch_failed"
    assert result.message == "Fetch failed."
    assert result.details == {"exception_type": "TimeoutError"}


def test_same_origin_redirect_handler_blocks_cross_origin_redirect() -> None:
    handler = scanner.SameOriginRedirectHandler("https://www.skyscanner.net/page")

    with pytest.raises(scanner.CrossOriginRedirectError) as exc_info:
        handler.redirect_request(
            req=object(),
            fp=object(),
            code=302,
            msg="Found",
            headers={},
            newurl="https://evil.example.test/assets/app.js",
        )

    assert exc_info.value.new_url == "https://evil.example.test/assets/app.js"


def _success(
    *,
    url: str,
    content_type: str,
    body: bytes,
    status_code: int = 200,
    truncated: bool = False,
) -> scanner.FetchSuccess:
    return scanner.FetchSuccess(
        url=url,
        final_url=url,
        status_code=status_code,
        content_type=content_type,
        body=body,
        truncated=truncated,
    )


def test_scan_url_returns_stable_json_shape_for_no_match_scan() -> None:
    responses = {
        "https://www.skyscanner.net/transport/flights/sgn/bkk/": _success(
            url="https://www.skyscanner.net/transport/flights/sgn/bkk/",
            content_type="text/html; charset=utf-8",
            body=b'<script src="/assets/app.js"></script>',
        ),
        "https://www.skyscanner.net/assets/app.js": _success(
            url="https://www.skyscanner.net/assets/app.js",
            content_type="application/javascript",
            body=b"console.log('hello');",
        ),
    }

    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        return responses[url]

    payload = scanner.scan_url(
        "https://www.skyscanner.net/transport/flights/sgn/bkk/",
        max_bundles=20,
        max_bytes_per_bundle=1000,
        timeout_seconds=15,
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    assert payload == {
        "schema_version": "1",
        "target_url": "https://www.skyscanner.net/transport/flights/sgn/bkk/",
        "fetched_at": "2026-05-18T00:00:00Z",
        "entry": {
            "status_code": 200,
            "final_url": "https://www.skyscanner.net/transport/flights/sgn/bkk/",
            "content_type": "text/html; charset=utf-8",
            "script_count": 1,
            "same_origin_script_count": 1,
            "skipped_cross_origin_script_count": 0,
        },
        "limits": {
            "max_bundles": 20,
            "max_bytes_per_bundle": 1000,
            "timeout_seconds": 15,
        },
        "bundles": [
            {
                "url": "https://www.skyscanner.net/assets/app.js",
                "final_url": "https://www.skyscanner.net/assets/app.js",
                "status_code": 200,
                "content_type": "application/javascript",
                "bytes_scanned": 21,
                "truncated": False,
                "matches": {
                    "operation_names": [],
                    "persisted_query_ids": [],
                    "graphql_paths": [],
                },
            }
        ],
        "errors": [],
    }


def test_scan_url_applies_bundle_cap_and_reports_graphql_matches() -> None:
    html = b"""
    <script src="/assets/a.js"></script>
    <script src="/assets/b.js"></script>
    """
    responses = {
        "https://www.skyscanner.net/page": _success(
            url="https://www.skyscanner.net/page",
            content_type="text/html",
            body=html,
        ),
        "https://www.skyscanner.net/assets/a.js": _success(
            url="https://www.skyscanner.net/assets/a.js",
            content_type="application/javascript",
            body=b'query FlightSearchQuery { search { id } } fetch("/graphql")',
        ),
    }

    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        return responses[url]

    payload = scanner.scan_url(
        "https://www.skyscanner.net/page",
        max_bundles=1,
        max_bytes_per_bundle=1000,
        timeout_seconds=15,
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    assert len(payload["bundles"]) == 1
    assert payload["entry"]["same_origin_script_count"] == 2
    assert payload["bundles"][0]["matches"] == {
        "operation_names": ["FlightSearchQuery"],
        "persisted_query_ids": [],
        "graphql_paths": ["/graphql"],
    }


def test_scan_url_reports_bundle_failure_and_continues() -> None:
    responses = {
        "https://www.skyscanner.net/page": _success(
            url="https://www.skyscanner.net/page",
            content_type="text/html",
            body=b'<script src="/assets/a.js"></script><script src="/assets/b.js"></script>',
        ),
        "https://www.skyscanner.net/assets/a.js": scanner.FetchFailure(
            error_type="fetch_failed",
            message="Fetch failed.",
            url="https://www.skyscanner.net/assets/a.js",
            details={"exception_type": "TimeoutError"},
        ),
        "https://www.skyscanner.net/assets/b.js": _success(
            url="https://www.skyscanner.net/assets/b.js",
            content_type="application/javascript",
            body=b"query FlightSearchQuery { search { id } }",
        ),
    }

    def fake_fetcher(
        url: str,
        **kwargs: object,
    ) -> scanner.FetchSuccess | scanner.FetchFailure:
        return responses[url]

    payload = scanner.scan_url(
        "https://www.skyscanner.net/page",
        max_bundles=20,
        max_bytes_per_bundle=1000,
        timeout_seconds=15,
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    assert payload["errors"] == [
        {
            "scope": "bundle",
            "error_type": "bundle_fetch_failed",
            "message": "Fetch failed.",
            "url": "https://www.skyscanner.net/assets/a.js",
            "status_code": None,
            "details": {"exception_type": "TimeoutError"},
        }
    ]
    assert [bundle["url"] for bundle in payload["bundles"]] == [
        "https://www.skyscanner.net/assets/b.js"
    ]


def test_scan_url_sanitizes_bundle_failure_details() -> None:
    responses = {
        "https://www.skyscanner.net/page": _success(
            url="https://www.skyscanner.net/page",
            content_type="text/html",
            body=b'<script src="/assets/a.js"></script>',
        ),
        "https://www.skyscanner.net/assets/a.js": scanner.FetchFailure(
            error_type="fetch_failed",
            message="Fetch failed.",
            url="https://www.skyscanner.net/assets/a.js",
            details={
                "exception_type": "TimeoutError",
                "final_url": "https://www.skyscanner.net/assets/a.js",
                "body": "secret body",
                "headers": {"authorization": "secret"},
                "raw": b"raw bytes",
                "snippet": "debug snippet",
            },
        ),
    }

    def fake_fetcher(
        url: str,
        **kwargs: object,
    ) -> scanner.FetchSuccess | scanner.FetchFailure:
        return responses[url]

    payload = scanner.scan_url(
        "https://www.skyscanner.net/page",
        max_bundles=20,
        max_bytes_per_bundle=1000,
        timeout_seconds=15,
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    assert payload["errors"][0]["details"] == {
        "exception_type": "TimeoutError",
        "final_url": "https://www.skyscanner.net/assets/a.js",
    }


def test_scan_url_rejects_non_html_entry_response() -> None:
    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        return _success(
            url=url,
            content_type="application/json",
            body=b"{}",
        )

    with pytest.raises(scanner.ScannerFatalError) as exc_info:
        scanner.scan_url(
            "https://www.skyscanner.net/page",
            max_bundles=20,
            max_bytes_per_bundle=1000,
            timeout_seconds=15,
            fetcher=fake_fetcher,
            now=lambda: "2026-05-18T00:00:00Z",
        )

    assert exc_info.value.to_error_payload()["error_type"] == (
        "unsupported_entry_content_type"
    )


def test_scan_url_maps_entry_fetch_failure() -> None:
    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchFailure:
        return scanner.FetchFailure(
            error_type="fetch_failed",
            message="Fetch failed.",
            url=url,
            details={"exception_type": "TimeoutError"},
        )

    with pytest.raises(scanner.ScannerFatalError) as exc_info:
        scanner.scan_url(
            "https://www.skyscanner.net/page",
            max_bundles=20,
            max_bytes_per_bundle=1000,
            timeout_seconds=15,
            fetcher=fake_fetcher,
            now=lambda: "2026-05-18T00:00:00Z",
        )

    assert exc_info.value.to_error_payload()["error_type"] == "entry_fetch_failed"


def test_scan_url_sanitizes_entry_fetch_failure_details() -> None:
    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchFailure:
        return scanner.FetchFailure(
            error_type="fetch_failed",
            message="Fetch failed.",
            url=url,
            details={
                "exception_type": "TimeoutError",
                "final_url": "https://www.skyscanner.net/login",
                "body": "secret body",
                "headers": {"authorization": "secret"},
                "raw": b"raw bytes",
                "snippet": "debug snippet",
            },
        )

    with pytest.raises(scanner.ScannerFatalError) as exc_info:
        scanner.scan_url(
            "https://www.skyscanner.net/page",
            max_bundles=20,
            max_bytes_per_bundle=1000,
            timeout_seconds=15,
            fetcher=fake_fetcher,
            now=lambda: "2026-05-18T00:00:00Z",
        )

    assert exc_info.value.to_error_payload()["details"] == {
        "target_url": "https://www.skyscanner.net/page",
        "status_code": None,
        "exception_type": "TimeoutError",
        "final_url": "https://www.skyscanner.net/login",
    }


def test_main_prints_success_payload_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_fetcher(url: str, **kwargs: object) -> scanner.FetchSuccess:
        if url.endswith("/page"):
            return _success(
                url=url,
                content_type="text/html",
                body=b'<script src="/assets/app.js"></script>',
            )
        return _success(
            url=url,
            content_type="application/javascript",
            body=b"query FlightSearchQuery { search { id } }",
        )

    exit_code = scanner.main(
        [
            "--url",
            "https://www.skyscanner.net/page",
            "--max-bundles",
            "1",
            "--max-bytes-per-bundle",
            "1000",
            "--timeout-seconds",
            "3",
        ],
        fetcher=fake_fetcher,
        now=lambda: "2026-05-18T00:00:00Z",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert '"schema_version": "1"' in captured.out
    assert '"FlightSearchQuery"' in captured.out


def test_main_prints_fatal_error_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = scanner.main(["--url", "http://example.test"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert '"error": true' in captured.err
    assert '"error_type": "invalid_url"' in captured.err


def test_main_prints_missing_url_as_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = scanner.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert '"error": true' in captured.err
    assert '"error_type": "invalid_url"' in captured.err
