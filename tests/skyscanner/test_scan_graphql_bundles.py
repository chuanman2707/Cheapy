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
