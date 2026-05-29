from __future__ import annotations

from typing import Any

from cheapy.markdown_report import render_offer_price, render_search_report
from cheapy.models import (
    CandidateFamily,
    CurrencyGroupV1,
    ErrorCode,
    ErrorV1,
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
    Severity,
    WarningCode,
    WarningV1,
)


TRAVELOKA_URL = (
    "https://www.traveloka.com/en-en/flight/fulltwosearch"
    "?ap=CXR.SGN&dt=10-07-2026.NA&ps=1.0.0&sc=ECONOMY&funnelSource=flight"
)
SKYSCANNER_PUBLIC_SEARCH_URL = (
    "https://www.skyscanner.com.sg/transport/flights/cxr/sgn/260710/"
    "?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0"
)
INTERNAL_OUTPUT_DENYLIST = (
    "/transport_deeplink/",
    "transport_deeplink",
    "sessionId",
    "session_id",
    "cookie",
    "headers",
    "request_body",
    "post_data",
    "raw_payload",
    "challenge",
    "datadome",
    "aws-waf-token",
    "tvl=",
    "tvo=",
    "tvs=",
    "mozilla/5.0 secret",
)


def _request(**overrides: Any) -> SearchRequestV1:
    data: dict[str, Any] = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "search_mode": SearchMode.EXACT,
        "passengers": PassengersV1(),
        "max_results": 5,
    }
    data.update(overrides)
    return SearchRequestV1.model_validate(data)


def _offer(**overrides: Any) -> FlightOfferV1:
    data: dict[str, Any] = {
        "offer_id": "offer-1",
        "price_amount": 4_920_000.0,
        "currency": "VND",
        "comparable": True,
        "rank_within_currency": 1,
        "global_rank": 1,
        "provider": "traveloka",
        "requested_origin": "CXR",
        "requested_destination": "SGN",
        "actual_origin": "CXR",
        "actual_destination": "SGN",
        "nearby_origin_distance_km": None,
        "nearby_destination_distance_km": None,
        "requested_departure_date": "2026-07-10",
        "actual_departure_date": "2026-07-10",
        "departure_offset_days": 0,
        "requested_return_date": None,
        "actual_return_date": None,
        "return_offset_days": None,
        "legs": [
            FlightLegV1(
                origin="CXR",
                destination="SGN",
                departure_time="2026-07-10T08:15:00",
                arrival_time="2026-07-10T09:25:00",
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=70,
            )
        ],
        "total_duration_minutes": 70,
        "stops": 0,
        "flags": OfferFlagsV1(),
        "fare_details_status": "not_collected",
        "public_search_url": TRAVELOKA_URL,
    }
    data.update(overrides)
    return FlightOfferV1.model_validate(data)


def _provider_status(**overrides: Any) -> ProviderStatusV1:
    data: dict[str, Any] = {
        "provider_name": "traveloka",
        "capability": "exact_one_way",
        "status": ProviderStatusCode.SUCCESS,
        "planned_call_count": 1,
        "executed_call_count": 1,
        "succeeded_call_count": 1,
        "failed_call_count": 0,
        "duration_ms": 12,
        "warnings": [],
        "errors": [],
        "retryable": False,
    }
    data.update(overrides)
    return ProviderStatusV1.model_validate(data)


def _search_plan(**overrides: Any) -> SearchPlanV1:
    data: dict[str, Any] = {
        "search_mode": SearchMode.EXACT,
        "planned_candidate_count": 1,
        "executed_candidate_count": 1,
        "planned_provider_call_count": 1,
        "executed_provider_call_count": 1,
        "candidate_count_by_family": {CandidateFamily.EXACT: 1},
        "provider_call_count_by_family": {CandidateFamily.EXACT: 1},
        "truncated": False,
        "truncated_families": [],
        "candidate_families": [CandidateFamily.EXACT],
    }
    data.update(overrides)
    return SearchPlanV1.model_validate(data)


def _response(**overrides: Any) -> SearchResponseV1:
    offers = list(overrides.pop("offers", [_offer()]))
    mixed_currency = overrides.pop(
        "mixed_currency", len({offer.currency for offer in offers}) > 1
    )
    data: dict[str, Any] = {
        "schema_version": "1",
        "status": SearchStatus.SUCCESS,
        "request_id": "req-markdown-report",
        "offers": offers,
        "warnings": [],
        "errors": [],
        "provider_statuses": [_provider_status()],
        "search_plan": _search_plan(),
        "mixed_currency": mixed_currency,
        "currency_groups": [
            CurrencyGroupV1(
                currency=currency,
                offer_ids=[offer.offer_id for offer in offers if offer.currency == currency],
            )
            for currency in sorted({offer.currency for offer in offers})
        ],
        "currency_notes": ([] if not mixed_currency else ["Currencies differ."]),
        "candidates": None,
    }
    data.update(overrides)
    return SearchResponseV1.model_validate(data)


def test_render_offer_price_links_safe_public_search_url() -> None:
    assert (
        render_offer_price(_offer())
        == f"[4,920,000 VND on Traveloka]({TRAVELOKA_URL})"
    )


def test_render_offer_price_links_skyscanner_public_search_url() -> None:
    rendered = render_offer_price(
        _offer(
            provider="skyscanner",
            public_search_url=SKYSCANNER_PUBLIC_SEARCH_URL,
        )
    )

    assert (
        rendered
        == f"[4,920,000 VND on Skyscanner]({SKYSCANNER_PUBLIC_SEARCH_URL})"
    )
    assert rendered.count(SKYSCANNER_PUBLIC_SEARCH_URL) == 1
    for token in INTERNAL_OUTPUT_DENYLIST:
        assert token not in rendered


def test_render_offer_price_without_public_search_url_is_plain_text() -> None:
    offer = _offer(price_amount=1_280_000.0, public_search_url=None)

    assert render_offer_price(offer) == "1,280,000 VND on Traveloka"


def test_render_offer_price_revalidates_and_omits_unsafe_public_search_url() -> None:
    unsafe_url = "https://www.traveloka.com/en-en/flight/fulltwosearch?token=secret"
    valid_offer = _offer(price_amount=1_280_000.0)
    unsafe_offer = valid_offer.model_copy(update={"public_search_url": unsafe_url})

    assert render_offer_price(unsafe_offer) == "1,280,000 VND on Traveloka"


def test_report_renders_header_summary_and_best_offers_without_raw_url_field() -> None:
    response = _response()

    report = render_search_report(_request(), response)

    assert "## CXR -> SGN | 2026-07-10 | 1 adult | Economy" in report
    assert "| Status | success |" in report
    assert "| Offers | 1 |" in report
    assert "| Search mode | exact |" in report
    assert "| Providers | Traveloka success 1/1 |" in report
    assert "| Mixed currency | no |" in report
    assert f"[4,920,000 VND on Traveloka]({TRAVELOKA_URL})" in report
    assert report.count(TRAVELOKA_URL) == 1
    assert "public_search_url" not in report


def test_report_best_offers_include_flight_numbers_column() -> None:
    offer = _offer(
        legs=[
            FlightLegV1(
                origin="CXR",
                destination="HAN",
                departure_time="2026-07-10T08:15:00",
                arrival_time="2026-07-10T10:10:00",
                airline_code="VJ",
                flight_number="VJ601",
                duration_minutes=115,
            ),
            FlightLegV1(
                origin="HAN",
                destination="SGN",
                departure_time="2026-07-10T11:25:00",
                arrival_time="2026-07-10T13:20:00",
                airline_code="VN",
                flight_number="VN134",
                duration_minutes=115,
            ),
        ],
        stops=1,
        total_duration_minutes=305,
    )

    report = render_search_report(_request(), _response(offers=[offer]))

    assert "| Rank | Fare | Flights | Route | Dates | Stops | Duration |" in report
    assert "| 1 | [4,920,000 VND on Traveloka]" in report
    assert " | VJ601, VN134 | CXR -> SGN | 2026-07-10 | 1 stop | 5h 5m |" in report


def test_report_best_offers_flight_numbers_fallback_when_no_legs() -> None:
    report = render_search_report(
        _request(),
        _response(offers=[_offer(legs=[])]),
    )

    assert "| Rank | Fare | Flights | Route | Dates | Stops | Duration |" in report
    assert " | - | CXR -> SGN | 2026-07-10 | nonstop | 1h 10m |" in report


def test_report_round_trip_header_uses_clear_date_arrow() -> None:
    report = render_search_report(
        _request(return_date="2026-07-17"),
        _response(
            offers=[
                _offer(
                    requested_return_date="2026-07-17",
                    actual_return_date="2026-07-17",
                    return_offset_days=0,
                )
            ]
        ),
    )

    assert "## CXR -> SGN | 2026-07-10 -> 2026-07-17 | 1 adult | Economy" in report


def test_report_empty_offers() -> None:
    report = render_search_report(_request(), _response(offers=[]))

    assert "No offers returned." in report


def test_provider_status_warnings_and_errors_include_safe_context_hide_details() -> None:
    warning = WarningV1(
        code=WarningCode.SEARCH_TRUNCATED,
        severity=Severity.WARNING,
        message_en="Provider warning is safe.",
        details={"url": "https://internal.example/path", "token": "secret-token"},
        retryable=True,
    )
    error = ErrorV1(
        code=ErrorCode.PROVIDER_TIMEOUT,
        severity=Severity.ERROR,
        message_en="Provider error is safe.",
        details={"payload": "secret-payload", "headers": {"auth": "secret-header"}},
        retryable=False,
    )
    response = _response(
        warnings=[warning],
        errors=[error],
        search_plan=_search_plan(
            planned_provider_call_count=4,
            executed_provider_call_count=3,
        ),
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.PARTIAL,
                succeeded_call_count=1,
                failed_call_count=1,
                planned_call_count=2,
                executed_call_count=2,
                warnings=[warning],
                errors=[error],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "## Provider Status" in report
    assert "| Providers | Traveloka partial 2/2, failed: 1, warnings: 1, errors: 1, retryable |" in report
    assert "| Traveloka | partial | 2/2 | succeeded: 1; failed: 1; retryable: yes; warning search_truncated: Provider warning is safe. retryable: yes; error provider_timeout: Provider error is safe. retryable: no |" in report
    assert "## Warnings And Errors" in report
    assert "| Report | success | 3/4 | search_truncated | warning | Provider warning is safe. | yes |" in report
    assert "| Report | success | 3/4 | provider_timeout | error | Provider error is safe. | no |" in report
    assert "| Traveloka | partial | 2/2 | search_truncated | warning | Provider warning is safe. | yes |" in report
    assert "| Traveloka | partial | 2/2 | provider_timeout | error | Provider error is safe. | no |" in report
    for unsafe_text in (
        "details",
        "url",
        "token",
        "payload",
        "headers",
        "https://internal.example/path",
        "secret-token",
        "secret-payload",
        "secret-header",
    ):
        assert unsafe_text not in report


def test_provider_summary_includes_multiple_provider_status_details() -> None:
    warning = WarningV1(
        code=WarningCode.FARE_DETAILS_NOT_COLLECTED,
        severity=Severity.INFO,
        message_en="Fare details were skipped.",
        details={},
        retryable=False,
    )
    response = _response(
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.PARTIAL,
                planned_call_count=2,
                executed_call_count=2,
                failed_call_count=1,
                warnings=[warning],
                retryable=True,
            ),
            _provider_status(
                provider_name="google_fli",
                status=ProviderStatusCode.FAILED,
                planned_call_count=1,
                executed_call_count=1,
                succeeded_call_count=0,
                failed_call_count=1,
                errors=[
                    ErrorV1(
                        code=ErrorCode.PROVIDER_FAILED,
                        severity=Severity.ERROR,
                        message_en="Provider failed safely.",
                        details={},
                        retryable=True,
                    )
                ],
            ),
        ],
    )

    report = render_search_report(_request(), response)

    assert (
        "| Providers | Traveloka partial 2/2, failed: 1, warnings: 1, retryable; "
        "Google Fli failed 1/1, failed: 1, errors: 1 |"
    ) in report


def test_top_level_sensitive_messages_are_redacted() -> None:
    warning = WarningV1(
        code=WarningCode.SEARCH_TRUNCATED,
        severity=Severity.WARNING,
        message_en="Provider failed at https://example.test/challenge?token=top-secret",
        details={},
        retryable=True,
    )
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Request body included header auth material.",
        details={},
        retryable=False,
    )

    report = render_search_report(
        _request(),
        _response(
            warnings=[warning],
            errors=[error],
            search_plan=_search_plan(
                planned_provider_call_count=4,
                executed_provider_call_count=3,
            ),
        ),
    )

    assert "| Report | success | 3/4 | search_truncated | warning | [redacted] | yes |" in report
    assert "| Report | success | 3/4 | provider_failed | error | [redacted] | no |" in report
    for unsafe_text in (
        "https://example.test/challenge",
        "token",
        "top-secret",
        "Request body",
        "header",
        "auth",
    ):
        assert unsafe_text not in report


def test_nested_sensitive_messages_are_redacted_in_all_sections() -> None:
    warning = WarningV1(
        code=WarningCode.SEARCH_TRUNCATED,
        severity=Severity.WARNING,
        message_en="Provider returned jwt abc.def.ghi from challenge.",
        details={},
        retryable=True,
    )
    error = ErrorV1(
        code=ErrorCode.PROVIDER_TIMEOUT,
        severity=Severity.ERROR,
        message_en="Provider response included payload\x1fmetadata.",
        details={},
        retryable=False,
    )
    response = _response(
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.PARTIAL,
                planned_call_count=2,
                executed_call_count=2,
                warnings=[warning],
                errors=[error],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "warning search_truncated: [redacted] retryable: yes" in report
    assert "error provider_timeout: [redacted] retryable: no" in report
    assert "| Traveloka | partial | 2/2 | search_truncated | warning | [redacted] | yes |" in report
    assert "| Traveloka | partial | 2/2 | provider_timeout | error | [redacted] | no |" in report
    for unsafe_text in (
        "abc.def.ghi",
        "challenge",
        "payload",
        "metadata",
    ):
        assert unsafe_text not in report


def test_provider_internal_runtime_messages_are_redacted_in_all_sections() -> None:
    warning = WarningV1(
        code=WarningCode.SEARCH_TRUNCATED,
        severity=Severity.WARNING,
        message_en="Traveloka browser runtime is unavailable.",
        details={},
        retryable=True,
    )
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Browserless session failed via Playwright and CloakBrowser.",
        details={},
        retryable=False,
    )
    response = _response(
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.FAILED,
                planned_call_count=2,
                executed_call_count=2,
                succeeded_call_count=0,
                failed_call_count=2,
                warnings=[warning],
                errors=[error],
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "warning search_truncated: [redacted] retryable: yes" in report
    assert "error provider_failed: [redacted] retryable: no" in report
    assert "| Traveloka | failed | 2/2 | search_truncated | warning | [redacted] | yes |" in report
    assert "| Traveloka | failed | 2/2 | provider_failed | error | [redacted] | no |" in report
    for unsafe_text in (
        "browser",
        "Browserless",
        "session",
        "Playwright",
        "CloakBrowser",
    ):
        assert unsafe_text not in report


def test_sensitive_provider_message_keeps_safe_failure_reason() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_TIMEOUT,
        severity=Severity.ERROR,
        message_en="Skyscanner request included cookie and session data.",
        details={
            "provider": "skyscanner",
            "capability": "exact_round_trip",
            "failure_type": "timeout",
            "cookie": "secret-cookie",
        },
        retryable=True,
    )
    response = _response(
        errors=[error],
        provider_statuses=[
            _provider_status(
                provider_name="skyscanner",
                status=ProviderStatusCode.FAILED,
                succeeded_call_count=0,
                failed_call_count=1,
                errors=[error],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "[redacted] (reason: timeout)" in report
    assert "cookie" not in report.lower()
    assert "secret-cookie" not in report
    assert "| Skyscanner | failed | 1/1 | failed: 1; retryable: yes; error provider_timeout: [redacted] (reason: timeout) retryable: yes |" in report


def test_sensitive_provider_warning_keeps_safe_failure_reason() -> None:
    warning = WarningV1(
        code=WarningCode.SEARCH_TRUNCATED,
        severity=Severity.WARNING,
        message_en="Provider warning included token and session data.",
        details={"failure_type": "timeout", "token": "secret-token"},
        retryable=True,
    )
    response = _response(
        warnings=[warning],
        provider_statuses=[
            _provider_status(
                provider_name="skyscanner",
                status=ProviderStatusCode.PARTIAL,
                planned_call_count=1,
                executed_call_count=1,
                warnings=[warning],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "[redacted] (reason: timeout)" in report
    assert "| Report | success | 1/1 | search_truncated | warning | [redacted] (reason: timeout) | yes |" in report
    assert "| Skyscanner | partial | 1/1 | succeeded: 1; retryable: yes; warning search_truncated: [redacted] (reason: timeout) retryable: yes |" in report
    assert "| Skyscanner | partial | 1/1 | search_truncated | warning | [redacted] (reason: timeout) | yes |" in report
    assert "secret-token" not in report
    assert "token" not in report
    assert "session" not in report


def test_provider_reason_can_come_from_safe_http_status_code() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_BLOCKED,
        severity=Severity.ERROR,
        message_en="Provider blocked the request at a challenge URL.",
        details={
            "provider": "skyscanner",
            "capability": "exact_one_way",
            "http_status_code": 403,
        },
        retryable=False,
    )

    report = render_search_report(
        _request(),
        _response(errors=[error], provider_statuses=[_provider_status(errors=[error])]),
    )

    assert "[redacted] (reason: provider_blocked)" in report
    assert "challenge URL" not in report
    assert "http_status_code" not in report
    assert "403" not in report


def test_unsafe_failure_type_is_not_rendered_as_reason() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Provider failed at https://example.test/challenge?token=secret",
        details={
            "provider": "skyscanner",
            "capability": "exact_one_way",
            "failure_type": "token_session_header_dump",
        },
        retryable=False,
    )

    report = render_search_report(
        _request(),
        _response(errors=[error], provider_statuses=[_provider_status(errors=[error])]),
    )

    assert "[redacted]" in report
    assert "token_session_header_dump" not in report
    assert "https://example.test" not in report


def test_http_status_reason_overrides_generic_failure_type() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en="Provider returned HTTP 500 with header token.",
        details={
            "provider": "skyscanner",
            "capability": "exact_one_way",
            "failure_type": "http_error",
            "http_status_code": 500,
        },
        retryable=True,
    )

    report = render_search_report(
        _request(),
        _response(errors=[error], provider_statuses=[_provider_status(errors=[error])]),
    )

    assert "(reason: transport_error)" in report
    assert "(reason: http_error)" not in report
    assert "http_status_code" not in report
    assert "500" not in report


def test_timeout_shaped_exception_basename_maps_to_timeout_reason() -> None:
    for exception_type in (
        "PlaywrightTimeoutError",
        "InjectedTimeout",
        "some.module.TimeoutError",
    ):
        error = ErrorV1(
            code=ErrorCode.PROVIDER_TIMEOUT,
            severity=Severity.ERROR,
            message_en="Provider timed out with internal browser session data.",
            details={
                "provider": "traveloka",
                "capability": "exact_one_way",
                "exception_type": exception_type,
            },
            retryable=True,
        )

        report = render_search_report(
            _request(),
            _response(
                errors=[error],
                provider_statuses=[_provider_status(errors=[error])],
            ),
        )

        assert "[redacted] (reason: timeout)" in report
        assert exception_type not in report
        assert exception_type.rsplit(".", 1)[-1] not in report
        assert "exception_type" not in report


def test_browser_bootstrap_material_is_redacted_with_safe_reason() -> None:
    error = ErrorV1(
        code=ErrorCode.PROVIDER_FAILED,
        severity=Severity.ERROR,
        message_en=(
            "Traveloka replay failed with datadome secret-cookie, "
            "post_data, aws-waf-token, and Mozilla/5.0 secret."
        ),
        details={
            "provider": "traveloka",
            "capability": "exact_one_way",
            "failure_type": "network_capture_unavailable",
            "headers": {"cookie": "datadome=secret-cookie"},
            "post_data": '{"searchId":"secret"}',
        },
        retryable=True,
    )
    response = _response(
        errors=[error],
        provider_statuses=[
            _provider_status(
                status=ProviderStatusCode.FAILED,
                succeeded_call_count=0,
                failed_call_count=1,
                errors=[error],
                retryable=True,
            )
        ],
    )

    report = render_search_report(_request(), response)

    assert "[redacted] (reason: network_capture_unavailable)" in report
    for unsafe_text in INTERNAL_OUTPUT_DENYLIST:
        assert unsafe_text not in report.lower()
