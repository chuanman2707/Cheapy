"""Markdown rendering helpers for Contract V1 search responses."""

from __future__ import annotations

from collections.abc import Iterable
import re

from cheapy.models import (
    ErrorV1,
    FlightOfferV1,
    ProviderStatusV1,
    SearchRequestV1,
    SearchResponseV1,
    WarningV1,
)
from cheapy.public_url_safety import validate_public_search_url


_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_JWT_SHAPE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])"
)
_SENSITIVE_MESSAGE_TERMS = (
    "auth",
    "browser",
    "browserless",
    "challenge",
    "cloakbrowser",
    "cookie",
    "header",
    "headers",
    "jwt",
    "payload",
    "playwright",
    "request body",
    "request_body",
    "secret",
    "session",
    "token",
)
_SAFE_FAILURE_REASONS = frozenset(
    {
        "timeout",
        "provider_blocked",
        "blocked",
        "rate_limited",
        "parse_failed",
        "parse_error",
        "no_usable_results",
        "missing_cookie",
        "transport_error",
        "unsupported_passengers",
        "http_error",
        "invalid_argument",
        "entity_not_found",
        "entity_ambiguous",
        "unexpected_error",
        "no_usable_outbound_data",
        "unsupported_response",
        "return_capture_timeout",
        "final_round_trip_total_unavailable",
        "outbound_selection_unavailable",
        "outbound_selection_transition_unavailable",
        "return_selection_unavailable",
        "selected_outbound_binding_unavailable",
        "selected_return_binding_unavailable",
        "partial_failure",
    }
)
_FAILURE_REASON_ALIASES = {
    "blocked": "provider_blocked",
    "parse_error": "parse_failed",
}
_TIMEOUT_EXCEPTION_TYPES = frozenset(
    {
        "TimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "TimeoutException",
    }
)


def render_search_report(request: SearchRequestV1, response: SearchResponseV1) -> str:
    """Render a Contract V1 search response as a Markdown report."""

    sections = [
        _render_header(request),
        _render_summary(request, response),
        _render_best_offers(response.offers),
    ]
    if response.provider_statuses:
        sections.append(_render_provider_statuses(response.provider_statuses))
    messages = _warning_error_rows(response)
    if messages:
        sections.append(_render_warnings_and_errors(messages))
    return "\n\n".join(sections).rstrip() + "\n"


def render_offer_price(offer: FlightOfferV1) -> str:
    """Render an offer fare/provider label, linking only safe public search URLs."""

    label = f"{_format_amount(offer.price_amount)} {offer.currency} on {_provider_label(offer.provider)}"
    url = offer.public_search_url
    if url is None:
        return label
    safe_url = validate_public_search_url(offer.provider, url)
    if safe_url is None:
        return label
    return f"[{_escape_markdown_link_text(label)}]({safe_url})"


def _render_header(request: SearchRequestV1) -> str:
    date = request.departure_date
    if request.return_date is not None:
        date = f"{date} -> {request.return_date}"
    return (
        f"## {_table_text(request.origin)} -> {_table_text(request.destination)}"
        f" | {_table_text(date)} | {_table_text(_passenger_summary(request))} | Economy"
    )


def _render_summary(request: SearchRequestV1, response: SearchResponseV1) -> str:
    providers = _provider_summary(response)
    rows = [
        ("Status", response.status.value),
        ("Offers", str(len(response.offers))),
        ("Search mode", request.search_mode.value),
        ("Providers", providers),
        ("Mixed currency", "yes" if response.mixed_currency else "no"),
    ]
    if response.currency_notes:
        rows.append(("Currency notes", "; ".join(response.currency_notes)))
    return _markdown_table(["Field", "Value"], rows)


def _render_best_offers(offers: list[FlightOfferV1]) -> str:
    lines = ["## Best Offers"]
    if not offers:
        lines.append("")
        lines.append("No offers returned.")
        return "\n".join(lines)

    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for index, offer in enumerate(offers, start=1):
        rows.append(
            (
                str(offer.global_rank or offer.rank_within_currency or index),
                render_offer_price(offer),
                _offer_flight_numbers(offer),
                f"{offer.actual_origin} -> {offer.actual_destination}",
                _offer_dates(offer),
                _format_stops(offer.stops),
                _format_duration(offer.total_duration_minutes),
            )
        )
    lines.append(
        _markdown_table(
            ["Rank", "Fare", "Flights", "Route", "Dates", "Stops", "Duration"],
            rows,
        )
    )
    return "\n".join(lines)


def _render_provider_statuses(statuses: list[ProviderStatusV1]) -> str:
    rows = [
        (
            _provider_label(status.provider_name),
            status.status.value,
            f"{status.executed_call_count}/{status.planned_call_count}",
            _provider_notes(status),
        )
        for status in statuses
    ]
    return "\n".join(
        [
            "## Provider Status",
            _markdown_table(["Provider", "Status", "Calls", "Notes"], rows),
        ]
    )


def _render_warnings_and_errors(rows: list[tuple[str, str, str, str, str, str, str]]) -> str:
    return "\n".join(
        [
            "## Warnings And Errors",
            _markdown_table(
                [
                    "Provider",
                    "Status",
                    "Calls",
                    "Code",
                    "Severity",
                    "Message",
                    "Retryable",
                ],
                rows,
            ),
        ]
    )


def _warning_error_rows(
    response: SearchResponseV1,
) -> list[tuple[str, str, str, str, str, str, str]]:
    rows = []
    report_calls = (
        f"{response.search_plan.executed_provider_call_count}/"
        f"{response.search_plan.planned_provider_call_count}"
    )
    rows.extend(
        _message_row(
            provider="Report",
            status=response.status.value,
            calls=report_calls,
            message=message,
        )
        for message in [*response.warnings, *response.errors]
    )
    for provider_status in response.provider_statuses:
        calls = (
            f"{provider_status.executed_call_count}/"
            f"{provider_status.planned_call_count}"
        )
        rows.extend(
            _message_row(
                provider=_provider_label(provider_status.provider_name),
                status=provider_status.status.value,
                calls=calls,
                message=message,
            )
            for message in [*provider_status.warnings, *provider_status.errors]
        )
    return rows


def _message_row(
    *,
    provider: str,
    status: str,
    calls: str,
    message: WarningV1 | ErrorV1,
) -> tuple[str, str, str, str, str, str, str]:
    return (
        provider,
        status,
        calls,
        message.code.value,
        message.severity.value,
        _safe_message_with_reason(message),
        "yes" if message.retryable else "no",
    )


def _markdown_table(headers: list[str], rows: Iterable[tuple[str, ...]]) -> str:
    rendered = [
        "| " + " | ".join(_table_text(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        rendered.append("| " + " | ".join(_table_text(cell) for cell in row) + " |")
    return "\n".join(rendered)


def _provider_notes(status: ProviderStatusV1) -> str:
    notes: list[str] = []
    if status.succeeded_call_count:
        notes.append(f"succeeded: {status.succeeded_call_count}")
    if status.failed_call_count:
        notes.append(f"failed: {status.failed_call_count}")
    if status.retryable:
        notes.append("retryable: yes")
    for warning in status.warnings:
        notes.append(_provider_message_note("warning", warning))
    for error in status.errors:
        notes.append(_provider_message_note("error", error))
    return "; ".join(notes) if notes else "-"


def _provider_message_note(kind: str, message: WarningV1 | ErrorV1) -> str:
    retryable = "yes" if message.retryable else "no"
    return (
        f"{kind} {message.code.value}: {_safe_message_with_reason(message)} "
        f"retryable: {retryable}"
    )


def _offer_flight_numbers(offer: FlightOfferV1) -> str:
    flight_numbers = [
        leg.flight_number.strip() for leg in offer.legs if leg.flight_number.strip()
    ]
    return ", ".join(flight_numbers) if flight_numbers else "-"


def _safe_message(message: str) -> str:
    if _message_appears_sensitive(message):
        return "[redacted]"
    return message


def _safe_message_with_reason(message: WarningV1 | ErrorV1) -> str:
    rendered = _safe_message(message.message_en)
    reason = _safe_reason(message)
    if reason is None:
        return rendered
    return f"{rendered} (reason: {reason})"


def _safe_reason(message: WarningV1 | ErrorV1) -> str | None:
    details = message.details

    failure_type = _safe_detail_token(details.get("failure_type"))
    if failure_type in _SAFE_FAILURE_REASONS:
        return _FAILURE_REASON_ALIASES.get(failure_type, failure_type)

    http_status_code = _detail_http_status_code(details.get("http_status_code"))
    if http_status_code in {401, 403}:
        return "provider_blocked"
    if http_status_code == 429:
        return "rate_limited"
    if http_status_code is not None and http_status_code >= 500:
        return "transport_error"

    exception_type = _safe_detail_token(details.get("exception_type"), preserve_case=True)
    if exception_type in _TIMEOUT_EXCEPTION_TYPES:
        return "timeout"

    return None


def _safe_detail_token(value: object, *, preserve_case: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token:
        return None
    if preserve_case:
        return token.rsplit(".", 1)[-1]
    return token.lower()


def _detail_http_status_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _message_appears_sensitive(message: str) -> bool:
    if _URL_RE.search(message):
        return True
    if _JWT_SHAPE_RE.search(message):
        return True
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in message):
        return True
    lowered = message.lower()
    return any(term in lowered for term in _SENSITIVE_MESSAGE_TERMS)


def _provider_summary(response: SearchResponseV1) -> str:
    if response.provider_statuses:
        return "; ".join(_provider_status_summary(status) for status in response.provider_statuses)

    labels = []
    seen = set()
    for provider in [offer.provider for offer in response.offers if offer.provider]:
        label = _provider_label(provider)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return ", ".join(labels) if labels else "-"


def _provider_status_summary(status: ProviderStatusV1) -> str:
    parts = [
        (
            f"{_provider_label(status.provider_name)} {status.status.value} "
            f"{status.executed_call_count}/{status.planned_call_count}"
        )
    ]
    if status.failed_call_count:
        parts.append(f"failed: {status.failed_call_count}")
    if status.warnings:
        parts.append(f"warnings: {len(status.warnings)}")
    if status.errors:
        parts.append(f"errors: {len(status.errors)}")
    if status.retryable:
        parts.append("retryable")
    return ", ".join(parts)


def _passenger_summary(request: SearchRequestV1) -> str:
    passenger_counts = [
        (request.passengers.adults, "adult"),
        (request.passengers.children, "child"),
        (request.passengers.infants_on_lap, "infant on lap"),
        (request.passengers.infants_in_seat, "infant in seat"),
    ]
    labels = [
        f"{count} {_pluralize(label, count)}"
        for count, label in passenger_counts
        if count
    ]
    return ", ".join(labels)


def _pluralize(label: str, count: int) -> str:
    if count == 1:
        return label
    if label == "child":
        return "children"
    return f"{label}s"


def _offer_dates(offer: FlightOfferV1) -> str:
    if offer.actual_return_date is None:
        return offer.actual_departure_date
    return f"{offer.actual_departure_date}, {offer.actual_return_date}"


def _format_amount(amount: float) -> str:
    if amount.is_integer():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def _provider_label(provider: str) -> str:
    return provider.replace("_", " ").title()


def _format_duration(minutes: int) -> str:
    hours, remaining_minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if remaining_minutes or not parts:
        parts.append(f"{remaining_minutes}m")
    return " ".join(parts)


def _format_stops(stops: int) -> str:
    if stops == 0:
        return "nonstop"
    if stops == 1:
        return "1 stop"
    return f"{stops} stops"


def _table_text(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|")


def _escape_markdown_link_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
