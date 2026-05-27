from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from cheapy.models import (
    CandidateFamily,
    CurrencyGroupV1,
    FlightLegV1,
    FlightOfferV1,
    OfferFlagsV1,
    PassengersV1,
    ProviderStatusCode,
    ProviderStatusV1,
    SearchMode,
    SearchPlanV1,
    SearchRequestV1,
    SearchResponseV1,
    SearchStatus,
)
from cheapy.public_links import attach_public_search_urls, build_public_search_url
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
            "google_fli",
            "https://www.google.com:443/travel/flights?q=Flights+from+SGN+to+BKK+on+2026-07-10",
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


def test_validate_public_search_url_accepts_traveloka_numeric_version_query() -> None:
    url = (
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
        "ap=SGN.BKK&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
    )

    assert validate_public_search_url("traveloka", url) == url


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
        ("google_fli", "https://www.google.com:/travel/flights?q=x"),
        (
            "traveloka",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?ap=SGN.BKK#results",
        ),
        ("google_fli", "https://www.google.com/travel/flights#"),
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
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/api;foo/search",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/g;foo/radar/search",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights//sgn/bkk/260710/",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%2fnotinternal",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/foo%3bbar",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%252fnotinternal",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/foo%253bbar",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/%2",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/anything",
        ),
        ("google_fli", "\x00https://www.google.com/travel/flights?q=x"),
        ("google_fli", " https://www.google.com/travel/flights?q=x"),
        ("google_fli", "https://www.google.com\n/travel/flights?q=x"),
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
            "traveloka",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?"
            "ap=SGN.BKK&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
            "&next=%2Fapi%2Fv2%2Fflight%2Fsearch%2Finitial",
        ),
        (
            "traveloka",
            "https://www.traveloka.com/en-en/flight/fulltwosearch?"
            "ap=SGN.BKK&dt=10-7-2026&ps=1.0.0&sc=ECONOMY"
            "&funnelSource=%2Fapi%2Fv2%2Fflight%2Fsearch%2Finitial",
        ),
        (
            "skyscanner",
            "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/260710/?"
            "adultsv2=1&cabinclass=economy&childrenv2=&ref=%2Ftransport_deeplink%2Fcheap&rtn=0",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?q=%2Fg%2Fradar%2Fsearch",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?q=%2F%2Fevil.example%2Fsearch",
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
            "https://www.google.com/travel/flights?id=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.sig",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?id=eyJhbGciOiJIUzI1NiJ9.e30.c2lnbmF0dXJl",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?id=e30.e30.sig",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?auth=basic",
        ),
        (
            "google_fli",
            "https://www.google.com/travel/flights?body=raw-provider-data",
        ),
        ("google_fli", "https://www.google.com/travel/flights?q=%00"),
        ("unknown", "https://www.google.com/travel/flights?q=Flights"),
        ("google_fli", "not a url"),
    ],
)
def test_validate_public_search_url_rejects_unsafe_urls(
    provider: str, url: str
) -> None:
    assert validate_public_search_url(provider, url) is None


def _request(**overrides: Any) -> SearchRequestV1:
    data: dict[str, Any] = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SIN",
        "departure_date": "2026-07-01",
        "return_date": None,
        "passengers": {
            "adults": 1,
            "children": 0,
            "infants_on_lap": 0,
            "infants_in_seat": 0,
        },
        "max_results": 5,
    }
    data.update(overrides)
    return SearchRequestV1.model_validate(data)


def _offer(**overrides: Any) -> FlightOfferV1:
    data: dict[str, Any] = {
        "offer_id": "offer-1",
        "price_amount": 120.0,
        "currency": "USD",
        "comparable": True,
        "rank_within_currency": 1,
        "global_rank": 1,
        "provider": "google_fli",
        "requested_origin": "CXR",
        "requested_destination": "SIN",
        "actual_origin": "SGN",
        "actual_destination": "BKK",
        "nearby_origin_distance_km": None,
        "nearby_destination_distance_km": None,
        "requested_departure_date": "2026-07-01",
        "actual_departure_date": "2026-07-10",
        "departure_offset_days": 9,
        "requested_return_date": None,
        "actual_return_date": None,
        "return_offset_days": None,
        "legs": [
            FlightLegV1(
                origin="SGN",
                destination="BKK",
                departure_time="2026-07-10T08:15:00",
                arrival_time="2026-07-10T09:45:00",
                airline_code="VJ",
                flight_number="VJ801",
                duration_minutes=90,
            )
        ],
        "total_duration_minutes": 90,
        "stops": 0,
        "flags": OfferFlagsV1(),
        "fare_details_status": "not_collected",
    }
    data.update(overrides)
    return FlightOfferV1.model_validate(data)


def _response(*offers: FlightOfferV1) -> SearchResponseV1:
    response_offers = list(offers) or [_offer()]
    return SearchResponseV1(
        schema_version="1",
        status=SearchStatus.SUCCESS,
        request_id="req-public-links",
        offers=response_offers,
        warnings=[],
        errors=[],
        provider_statuses=[
            ProviderStatusV1(
                provider_name="google_fli",
                capability="exact_one_way",
                status=ProviderStatusCode.SUCCESS,
                planned_call_count=1,
                executed_call_count=1,
                succeeded_call_count=1,
                failed_call_count=0,
                duration_ms=10,
                warnings=[],
                errors=[],
                retryable=False,
            )
        ],
        search_plan=SearchPlanV1(
            search_mode=SearchMode.EXACT,
            planned_candidate_count=1,
            executed_candidate_count=1,
            planned_provider_call_count=1,
            executed_provider_call_count=1,
            candidate_count_by_family={CandidateFamily.EXACT: 1},
            provider_call_count_by_family={CandidateFamily.EXACT: 1},
            truncated=False,
            truncated_families=[],
            candidate_families=[CandidateFamily.EXACT],
        ),
        mixed_currency=False,
        currency_groups=[
            CurrencyGroupV1(
                currency="USD",
                offer_ids=[offer.offer_id for offer in response_offers],
            )
        ],
        currency_notes=[],
        candidates=None,
    )


def test_build_public_search_url_builds_traveloka_one_way_from_offer_actuals() -> None:
    offer = _offer(provider="traveloka")

    url = build_public_search_url("traveloka", _request(), offer)

    assert url is not None
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.traveloka.com"
    assert parsed.path == "/en-en/flight/fulltwosearch"
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026"]
    assert params["ps"] == ["1.0.0"]
    assert validate_public_search_url("traveloka", url) == url


def test_build_public_search_url_rejects_provider_mismatch() -> None:
    offer = _offer(provider="google_fli")

    assert build_public_search_url("traveloka", _request(), offer) is None


def test_build_public_search_url_builds_traveloka_round_trip_actuals() -> None:
    offer = _offer(provider="traveloka", actual_return_date="2026-07-17")

    url = build_public_search_url(
        "traveloka",
        _request(return_date="2026-07-20"),
        offer,
    )

    assert url is not None
    params = parse_qs(urlparse(url).query)
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026.17-7-2026"]
    assert validate_public_search_url("traveloka", url) == url


def test_build_public_search_url_builds_traveloka_one_way_no_actual_return() -> None:
    request = _request(return_date="2026-07-20")
    offer = _offer(provider="traveloka", actual_return_date=None)

    url = build_public_search_url("traveloka", request, offer)

    assert url is not None
    params = parse_qs(urlparse(url).query)
    assert params["dt"] == ["10-7-2026"]


def test_build_public_search_url_builds_traveloka_non_default_passengers() -> None:
    request = _request(passengers=PassengersV1(adults=2, children=1, infants_in_seat=2))
    offer = _offer(provider="traveloka")

    url = build_public_search_url("traveloka", request, offer)

    assert url is not None
    assert parse_qs(urlparse(url).query)["ps"] == ["2.1.2"]


def test_build_public_search_url_builds_google_one_way_from_offer_actuals() -> None:
    offer = _offer(provider="google_fli")

    url = build_public_search_url("google_fli", _request(), offer)

    assert url is not None
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.google.com"
    assert parsed.path == "/travel/flights"
    assert parse_qs(parsed.query) == {
        "q": [
            "Flights from SGN to BKK on 2026-07-10 "
            "for 1 adult, 0 children, 0 infants"
        ]
    }
    assert validate_public_search_url("google_fli", url) == url


def test_build_public_search_url_returns_none_for_google_bad_departure() -> None:
    offer = _offer(provider="google_fli").model_copy(
        update={"actual_departure_date": "not-a-date"}
    )

    assert build_public_search_url("google_fli", _request(), offer) is None


@pytest.mark.parametrize("actual_departure_date", ["20260710", "2026-W29-5"])
def test_build_public_search_url_rejects_google_non_contract_departure(
    actual_departure_date: str,
) -> None:
    offer = _offer(provider="google_fli").model_copy(
        update={"actual_departure_date": actual_departure_date}
    )

    assert build_public_search_url("google_fli", _request(), offer) is None


def test_build_public_search_url_builds_google_round_trip_from_offer_actuals() -> None:
    offer = _offer(provider="google_fli", actual_return_date="2026-07-17")

    url = build_public_search_url(
        "google_fli",
        _request(return_date="2026-07-20"),
        offer,
    )

    assert url is not None
    assert parse_qs(urlparse(url).query) == {
        "q": [
            "Flights from SGN to BKK on 2026-07-10 returning 2026-07-17 "
            "for 1 adult, 0 children, 0 infants"
        ]
    }
    assert validate_public_search_url("google_fli", url) == url


def test_build_public_search_url_returns_none_for_google_invalid_return_date() -> None:
    offer = _offer(provider="google_fli").model_copy(
        update={"actual_return_date": "not-a-date"}
    )

    assert (
        build_public_search_url(
            "google_fli",
            _request(return_date="2026-07-20"),
            offer,
        )
        is None
    )


@pytest.mark.parametrize("actual_return_date", ["20260717", "2026-W29-5"])
def test_build_public_search_url_rejects_google_non_contract_return(
    actual_return_date: str,
) -> None:
    offer = _offer(provider="google_fli").model_copy(
        update={"actual_return_date": actual_return_date}
    )

    assert (
        build_public_search_url(
            "google_fli",
            _request(return_date="2026-07-20"),
            offer,
        )
        is None
    )


def test_build_public_search_url_builds_google_one_way_without_actual_return() -> None:
    request = _request(return_date="2026-07-20")
    offer = _offer(provider="google_fli", actual_return_date=None)

    url = build_public_search_url("google_fli", request, offer)

    assert url is not None
    query = parse_qs(urlparse(url).query)["q"][0]
    assert "on 2026-07-10" in query
    assert "returning" not in query
    assert "2026-07-20" not in query


def test_build_public_search_url_builds_google_non_default_passengers() -> None:
    request = _request(
        passengers=PassengersV1(
            adults=2,
            children=1,
            infants_on_lap=1,
            infants_in_seat=1,
        )
    )
    offer = _offer(provider="google_fli")

    url = build_public_search_url("google_fli", request, offer)

    assert url is not None
    assert parse_qs(urlparse(url).query)["q"] == [
        "Flights from SGN to BKK on 2026-07-10 "
        "for 2 adults, 1 child, 2 infants"
    ]


def test_build_public_search_url_builds_skyscanner_one_way_from_offer_actuals() -> None:
    offer = _offer(provider="skyscanner")

    url = build_public_search_url("skyscanner", _request(), offer)

    assert url == (
        "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/260710/"
        "?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0"
    )
    assert validate_public_search_url("skyscanner", url) == url


def test_build_public_search_url_builds_skyscanner_multiple_adults() -> None:
    request = _request(passengers=PassengersV1(adults=3))
    offer = _offer(provider="skyscanner")

    url = build_public_search_url("skyscanner", request, offer)

    assert url is not None
    assert parse_qs(urlparse(url).query)["adultsv2"] == ["3"]


@pytest.mark.parametrize("actual_departure_date", ["20260710", "2026-W29-5"])
def test_build_public_search_url_rejects_skyscanner_non_contract_departure(
    actual_departure_date: str,
) -> None:
    offer = _offer(provider="skyscanner").model_copy(
        update={"actual_departure_date": actual_departure_date}
    )

    assert build_public_search_url("skyscanner", _request(), offer) is None


def test_build_public_search_url_builds_skyscanner_round_trip_actuals() -> None:
    offer = _offer(provider="skyscanner", actual_return_date="2026-07-17")

    url = build_public_search_url(
        "skyscanner",
        _request(return_date="2026-07-20"),
        offer,
    )

    assert url == (
        "https://www.skyscanner.com.sg/transport/flights/sgn/bkk/260710/260717/"
        "?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=1"
    )
    assert validate_public_search_url("skyscanner", url) == url


@pytest.mark.parametrize("actual_return_date", ["20260717", "2026-W29-5"])
def test_build_public_search_url_rejects_skyscanner_non_contract_return(
    actual_return_date: str,
) -> None:
    offer = _offer(provider="skyscanner").model_copy(
        update={"actual_return_date": actual_return_date}
    )

    assert (
        build_public_search_url(
            "skyscanner",
            _request(return_date="2026-07-20"),
            offer,
        )
        is None
    )


def test_build_public_search_url_returns_none_for_skyscanner_child_passengers() -> None:
    request = _request(passengers=PassengersV1(adults=1, children=1))
    offer = _offer(provider="skyscanner")

    assert build_public_search_url("skyscanner", request, offer) is None


def test_build_public_search_url_returns_none_for_skyscanner_infants() -> None:
    request = _request(passengers=PassengersV1(adults=1, infants_on_lap=1))
    offer = _offer(provider="skyscanner")

    assert build_public_search_url("skyscanner", request, offer) is None


def test_build_public_search_url_uses_one_way_without_actual_return_date() -> None:
    request = _request(return_date="2026-07-20")
    offer = _offer(provider="skyscanner", actual_return_date=None)

    url = build_public_search_url("skyscanner", request, offer)

    assert url is not None
    assert "/260710/?" in url
    assert "260720" not in url
    assert "rtn=0" in url


def test_attach_public_search_urls_uses_offer_actual_route_and_dates() -> None:
    request = _request(
        origin="CXR",
        destination="SIN",
        departure_date="2026-07-01",
        return_date="2026-07-20",
    )
    offer = _offer(
        provider="traveloka",
        requested_origin="CXR",
        requested_destination="SIN",
        actual_origin="SGN",
        actual_destination="BKK",
        requested_departure_date="2026-07-01",
        actual_departure_date="2026-07-10",
        requested_return_date="2026-07-20",
        actual_return_date="2026-07-17",
    )
    response = _response(offer)

    updated = attach_public_search_urls(request, response)

    assert updated is not response
    assert updated.offers[0] is not offer
    assert updated.warnings == response.warnings
    assert updated.errors == response.errors
    assert updated.provider_statuses == response.provider_statuses
    assert updated.search_plan == response.search_plan
    assert updated.currency_groups == response.currency_groups
    params = parse_qs(urlparse(updated.offers[0].public_search_url or "").query)
    assert params["ap"] == ["SGN.BKK"]
    assert params["dt"] == ["10-7-2026.17-7-2026"]


def test_attach_public_search_urls_keeps_unknown_provider_url_none() -> None:
    offer = _offer(provider="manual_fixture")
    response = _response(offer)

    updated = attach_public_search_urls(_request(), response)

    assert updated.offers[0].public_search_url is None
