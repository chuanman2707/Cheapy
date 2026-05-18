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
