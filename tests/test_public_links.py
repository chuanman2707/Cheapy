from __future__ import annotations

import pytest

from cheapy.public_url_safety import validate_public_search_url


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        (
            "traveloka",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK&dt=10-7-2026&ps=1.0.0&sc=ECONOMY&funnelSource=SEO-Homepage-SearchForm",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?q=Flights+from+SGN+to+BKK+on+2026-07-10",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/260710/?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0",
        ),
    ],
)
def test_validate_public_search_url_accepts_provider_search_urls(
    provider: str, url: str
) -> None:
    assert validate_public_search_url(provider, url) == url


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        (
            "traveloka",
            "https://www.google.com/travel/flights?q=Flights+from+SGN+to+BKK+on+2026-07-10",
        ),
        (
            "google_fli",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK",
        ),
        (
            "skyscanner",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK",
        ),
    ],
)
def test_validate_public_search_url_rejects_cross_provider_urls(
    provider: str, url: str
) -> None:
    assert validate_public_search_url(provider, url) is None


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("traveloka", "http://www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK"),
        ("traveloka", "https://evil.test/en-en/flight/fulltwosearch?ap=SGN.BKK"),
        (
            "traveloka",
            "https://www.traveloka.com.evil.test/en-en/flight/fulltwosearch?ap=SGN.BKK",
        ),
        ("traveloka", "//www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK"),
        (
            "traveloka",
            "https://user:pass@www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK",
        ),
        (
            "traveloka",
            "https://www.traveloka.com:8443/en-en/flight/fulltwosearch?ap=SGN.BKK",
        ),
        (
            "traveloka",
            "https://www.traveloka.com:notaport/en-en/flight/fulltwosearch?ap=SGN.BKK",
        ),
        (
            "traveloka",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK#results",
        ),
        ("traveloka", "https://www.traveloka.com/api/search?ap=SGN.BKK"),
        ("traveloka", "https://www.traveloka.com/API/search?ap=SGN.BKK"),
        ("traveloka", "https://www.traveloka.com/g/radar/search?ap=SGN.BKK"),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport_deeplink/sgn/bkk",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%2Ftransport_deeplink%2F",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/../transport_deeplink/",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%25252525252Ftransport_deeplink%25252525252F",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%5c..%5ctransport_deeplink/",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%5capi%5csearch",
        ),
        (
            "google_fli",
            "https://www.google.com/Travel/Flights?q=Flights+from+SGN+to+BKK",
        ),
        (
            "google_fli",
            "https://www.google.com/travel//flights?q=Flights+from+SGN+to+BKK",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights/?q=Flights+from+SGN+to+BKK",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?token=secret&q=Flights+from+SGN+to+BKK",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?next=https%3A%2F%2Fexample.test%2Fchallenge%2Fabc",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?cookie%5Bsession%5D=secret",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?q=Bearer%20header%20payload",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?request_id=req_123",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?jwt=eyJhbGciOiJIUzI1NiJ9.abc.sig",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?auth=basic",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?body=raw-provider-data",
        ),
        ("unknown", "https://www.google.com/travel/flights?q=Flights"),
        ("google_fli", "not a url"),
    ],
)
def test_validate_public_search_url_rejects_unsafe_urls(
    provider: str, url: str
) -> None:
    assert validate_public_search_url(provider, url) is None
