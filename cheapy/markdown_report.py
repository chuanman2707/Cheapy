"""Markdown rendering helpers for Contract V1 search responses."""

from __future__ import annotations

from collections.abc import Iterable

from cheapy.models import (
    ErrorV1,
    FlightOfferV1,
    ProviderStatusV1,
    SearchRequestV1,
    SearchResponseV1,
    WarningV1,
)
from cheapy.public_url_safety import validate_public_search_url


def render_search_report(request: SearchRequestV1, response: SearchResponseV1) -> str:
    """Render a Contract V1 search response as a Markdown report."""

    sections = [
        _render_header(request),
        _render_summary(request, response),
        _render_best_offers(response.offers),
    ]
    if response.provider_statuses:
        sections.append(_render_provider_statuses(response.provider_statuses))
    messages = [*response.warnings, *response.errors]
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
        date = f"{date}, {request.return_date}"
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

    rows: list[tuple[str, str, str, str, str, str]] = []
    for index, offer in enumerate(offers, start=1):
        rows.append(
            (
                str(offer.global_rank or offer.rank_within_currency or index),
                render_offer_price(offer),
                f"{offer.actual_origin} -> {offer.actual_destination}",
                _offer_dates(offer),
                _format_stops(offer.stops),
                _format_duration(offer.total_duration_minutes),
            )
        )
    lines.append(
        _markdown_table(
            ["Rank", "Fare", "Route", "Dates", "Stops", "Duration"], rows
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


def _render_warnings_and_errors(messages: list[WarningV1 | ErrorV1]) -> str:
    rows = [
        (
            message.code.value,
            message.severity.value,
            message.message_en,
            "yes" if message.retryable else "no",
        )
        for message in messages
    ]
    return "\n".join(
        [
            "## Warnings And Errors",
            _markdown_table(["Code", "Severity", "Message", "Retryable"], rows),
        ]
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
        notes.append(f"warning: {warning.message_en}")
    for error in status.errors:
        notes.append(f"error: {error.message_en}")
    return "; ".join(notes) if notes else "-"


def _provider_summary(response: SearchResponseV1) -> str:
    providers = [
        status.provider_name
        for status in response.provider_statuses
        if status.provider_name
    ]
    if not providers:
        providers = [offer.provider for offer in response.offers if offer.provider]
    labels = []
    seen = set()
    for provider in providers:
        label = _provider_label(provider)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return ", ".join(labels) if labels else "-"


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
