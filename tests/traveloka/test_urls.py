from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from cheapy.providers.base import (
    ProviderExactOneWayRequest,
    ProviderExactRoundTripRequest,
)
from cheapy.providers.traveloka import adapter as traveloka_adapter
from cheapy.providers.traveloka import urls as traveloka_urls


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


def test_build_full_search_url_maps_one_way_request_to_traveloka_route() -> None:
    url = traveloka_urls.build_full_search_url(
        _one_way_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.traveloka.com"
    assert parsed.path == "/en-en/flight/fulltwosearch"
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026"]
    assert params["ps"] == ["1.0.0"]
    assert params["sc"] == ["ECONOMY"]
    assert params["funnelSource"] == ["SEO-Homepage-SearchForm"]


def test_build_full_search_url_maps_round_trip_request_to_traveloka_route() -> None:
    url = traveloka_urls.build_full_search_url(
        _round_trip_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    params = parse_qs(urlparse(url).query)
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026.17-7-2026"]
    assert params["ps"] == ["1.0.0"]
    assert params["sc"] == ["ECONOMY"]


def test_traveloka_urls_module_builds_full_search_url() -> None:
    url = traveloka_urls.build_full_search_url(
        _round_trip_request(),
        base_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
    )

    assert not hasattr(traveloka_adapter, "build_full_search_url")
    params = parse_qs(urlparse(url).query)
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026.17-7-2026"]
