from __future__ import annotations

from time import monotonic

import pytest

from cheapy.providers.traveloka import selection as traveloka_selection

from .fakes import FakeLocatorCollection, LocatorFakePage, TextFakeLocator

def test_traveloka_selection_module_detects_return_transition() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": FakeLocatorCollection(
                [TextFakeLocator(text="Return\nChange return flight")]
            )
        },
    )

    assert traveloka_selection.return_selection_transitioned(page) is True


def test_wait_for_return_selection_transition_recognizes_selected_summary() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
                text="Return BKK to SGN\nChange return flight"
            )
        },
    )

    assert (
        traveloka_selection.wait_for_return_selection_transition(
            page,
            deadline=monotonic() + 1,
            poll_interval_seconds=0.001,
        )
        is True
    )


def test_wait_for_return_selection_transition_times_out_without_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = LocatorFakePage([], selector_locators={"body": TextFakeLocator(text="Choose")})
    monkeypatch.setattr(
        traveloka_selection,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )

    assert (
        traveloka_selection.wait_for_return_selection_transition(
            page,
            deadline=monotonic() + 1,
            poll_interval_seconds=0.001,
        )
        is False
    )


def test_wait_for_return_selection_transition_ignores_preexisting_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker_text = "Return BKK to SGN\nChange return flight"
    page = LocatorFakePage(
        [],
        selector_locators={
            "[data-testid='flight-summary-container-1_selected']": TextFakeLocator(
                text=marker_text
            ),
            "body": TextFakeLocator(text=f"Choose\n{marker_text}"),
        },
    )
    monkeypatch.setattr(
        traveloka_selection,
        "SELECTION_TRANSITION_TIMEOUT_MS",
        1,
        raising=False,
    )

    assert (
        traveloka_selection.wait_for_return_selection_transition(
            page,
            deadline=monotonic() + 1,
            poll_interval_seconds=0.001,
            before_marker_texts=(marker_text,),
            before_body_text=f"Choose\n{marker_text}",
        )
        is False
    )


def test_outbound_transition_requires_exact_selected_url_fragment() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    page.url = "https://www.traveloka.com/en-en/flight/fulltwosearch#SCtv-10"

    assert (
        traveloka_selection._outbound_selection_transitioned(
            page,
            "tv-1",
            before_url="https://www.traveloka.com/en-en/flight/fulltwosearch",
            before_body_text="Your Flights",
        )
        is False
    )


def test_outbound_transition_accepts_exact_selected_fragment_from_different_baseline() -> None:
    page = LocatorFakePage(
        [],
        selector_locators={"body": TextFakeLocator(text="Your Flights")},
    )
    page.url = "https://www.traveloka.com/en-en/flight/fulltwosearch#SCtv-1"

    assert (
        traveloka_selection._outbound_selection_transitioned(
            page,
            "tv-1",
            before_url="https://www.traveloka.com/en-en/flight/fulltwosearch#SCtv-10",
            before_body_text="Your Flights",
        )
        is True
    )
