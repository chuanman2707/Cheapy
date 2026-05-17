# Cheapy Traveloka Return Selection Recovery Design

Date: 2026-05-15

## Summary

Fix the remaining Traveloka exact round-trip browser flow after the live
`CXR -> BKK`, `2026-06-15` to `2026-06-20` smoke.

The current provider times out waiting for return capture because outbound
selection does not actually transition the Traveloka UI. The adapter finds and
binds the cheapest outbound card, but the click primitive used by
`_click_visible_option()` does not activate Traveloka's React Native Web
selection handler. Once outbound selection is activated with a synthetic
pointer/mouse event sequence, Traveloka moves to the return-selection state and
emits a supported `/api/v2/flight/search/poll` response with return inventory.

After that fix, the next known gap is final-total extraction. Live diagnostics
show Traveloka renders selected outbound and return summaries plus price labels
such as `Total USD 239.68/pax` and `Round-trip price USD 239.68/pax`. The
current final-total reader does not recognize those surfaces.

This design keeps Contract V1 unchanged. Raw Traveloka round-trip partial
offers remain non-comparable; only a completed selected round trip with both
legs selected and a final selected total can become comparable.

## Observed Root Cause

Live diagnostics showed these facts:

1. The first outbound capture succeeds and returns usable outbound inventory.
2. Visible outbound cards are discovered and the cheapest card binds to a
   captured payload item id.
3. Normal Playwright clicks on the Traveloka inventory card/button are blocked
   by a full-page `data-focusvisible-polyfill` overlay intercepting pointer
   events.
4. Forced Playwright clicks complete without an exception, but they do not
   trigger the Traveloka selection transition.
5. Keyboard activation, coordinate click, and card force-click did not move the
   UI to return selection in the diagnostic run.
6. Dispatching `pointerdown`, `mousedown`, `pointerup`, `mouseup`, and `click`
   events directly on the inventory card button moved the UI to the return
   state.
7. With production-equivalent capture reset before activation, synthetic
   dispatch captured 67 return items from `/api/v2/flight/search/poll`.

Therefore `return_capture_timeout` is currently a misleading symptom. The
adapter is not really timing out after a successful outbound selection; it is
waiting after a no-op outbound activation.

## Approved Approach

Use Traveloka-specific DOM activation for inventory card buttons.

The activation helper should:

- keep the existing deadline and click-timeout caps
- scroll the option into view before activation
- dispatch a small browser-side event sequence on the selected locator:
  `pointerdown`, `mousedown`, `pointerup`, `mouseup`, `click`
- avoid direct HTTP replay, login, captcha handling, persisted browser state,
  proxying, or retries
- return only after the browser-side dispatch has completed

This is a provider-specific browser automation workaround, not a schema or
normalization contract change.

## Runtime Flow Changes

Round-trip selection should become a state-machine flow:

1. Capture outbound inventory as today.
2. Choose and bind the cheapest visible outbound card as today.
3. Reset capture state.
4. Activate the outbound card with the Traveloka-specific DOM event sequence.
5. Wait for an outbound-selection transition marker that does not require
   return inventory, or treat the first supported return fare capture as proof
   that outbound selection transitioned.
6. Wait for return capture from the existing supported fare paths when it has
   not already arrived while checking transition state.
7. Choose and bind the cheapest visible return card.
8. Activate the return card with the same Traveloka-specific event sequence.
9. Wait for return-selection markers.
10. Read final selected round-trip total from live Traveloka total surfaces.
11. Return one comparable selected round-trip offer only when both selected
    legs and the final selected total are available.

The adapter should not burn the whole provider deadline when a selection action
does not change state. It should distinguish a no-transition failure from a
real return-capture timeout.

## Transition Markers

Outbound selection is considered transitioned when at least one stable marker is
observed after outbound activation:

- URL hash contains the selected outbound key, for example `#SC<key>`
- the page shows a selected departure summary, such as `Change departure
  flight`
- body text includes the requested return route/date block after the selected
  departure summary is visible
- a supported first-party return fare payload from `/api/v2/flight/search/poll`
  arrives after outbound activation

Outbound transition detection must not require visible return inventory. That
inventory can depend on return fare capture, so requiring it before capture
would misclassify a real return-capture timeout as an outbound transition
failure. `return_capture_timeout` is reserved for the case where outbound
selection transitioned but no supported return fare payload arrived before the
deadline.

Return selection is considered transitioned when at least one stable marker is
observed after return activation:

- a selected return summary exists, such as
  `flight-summary-container-1_selected`
- body text includes `Change return flight`
- the bundle/summary tray shows both `Departure` and `Return`

The implementation should use bounded DOM reads and the shared deadline for
these waits.

## Final Total Surfaces

The final selected total reader should support the existing selected/final/
checkout selectors and the live Traveloka surfaces observed after return
selection:

- `[data-testid='label_fl_inventory_price']` text containing
  `Total USD <amount>/pax`
- selected summary/tray text containing `Round-trip price USD <amount>/pax`

When several prices are visible, selected-total parsing must prefer explicit
`Total` or `Round-trip price` amounts over incremental add-on prices such as
`+ USD 0.00/pax`.

The final total is accepted only after return-selection markers indicate that
both legs are selected. A visible return-card price before selection is still
not a comparable final round-trip total.

Generic body-wide parsing is not acceptable unless it is scoped to a selected
summary/tray container or covered by a regression test where unselected
inventory-card prices and add-on prices coexist with the selected total. This
prevents the reader from accidentally treating an unselected card's
`Round-trip price` label as the final selected itinerary total.

## Error Handling

Keep existing partial fallback behavior and add one more precise failure type:

- `outbound_selection_transition_unavailable`: outbound option was discovered
  and activated, but Traveloka did not move into return-selection state

Keep existing failure types for later stages:

- `return_capture_timeout`: outbound transition happened, but no supported
  return fare payload arrived before the deadline
- `return_selection_unavailable`: no selectable return option was found
- `selected_return_binding_unavailable`: chosen return option could not bind to
  captured return payload data
- `final_round_trip_total_unavailable`: both selections happened, but final
  selected total could not be read

Provider-level safe failure metadata should include the new failure type. It
can map to the same provider-failed metadata as other incomplete-selection
partial errors.

## Tests

Default tests remain offline and must not call Traveloka or launch a real
browser.

Add focused adapter/provider tests for:

- Traveloka visible options still discover live-shaped inventory cards.
- `_click_visible_option()` or its replacement dispatches the synthetic
  pointer/mouse sequence instead of relying on `force=True`.
- outbound no-transition returns
  `outbound_selection_transition_unavailable` without consuming the full
  provider deadline.
- outbound transition success proceeds to return capture.
- final-total parsing reads `Total USD 239.68/pax` from
  `label_fl_inventory_price`.
- final-total parsing reads `Round-trip price USD 239.68/pax` from selected
  summary/body text.
- final-total parsing ignores `+ USD 0.00/pax` when an explicit total is
  present.
- final-total parsing ignores unselected inventory-card `Round-trip price`
  labels when a selected summary/tray total is present.
- provider safe failure handling preserves the new failure type in partial
  results.
- selected round-trip success still returns exactly one comparable offer using
  the final selected total.

Run:

```bash
uv run pytest tests/test_traveloka_adapter.py tests/test_traveloka_normalizer.py tests/test_traveloka_provider.py -v
uv run pytest tests/test_search.py -v
uv run pytest -v
```

Optional live smoke:

```text
Traveloka exact round-trip CXR -> BKK, depart 2026-06-15, return 2026-06-20.
```

Acceptance for the live smoke:

- it no longer fails as a no-op outbound click disguised as
  `return_capture_timeout`
- ideal result is one comparable selected Traveloka round-trip offer
- acceptable partial result must identify the later real boundary:
  `outbound_selection_transition_unavailable`,
  `return_capture_timeout`, `return_selection_unavailable`,
  `selected_return_binding_unavailable`, or
  `final_round_trip_total_unavailable`

## Non-Goals

This fix will not:

- make raw Traveloka round-trip partial offers comparable
- change Contract V1 schema
- explore multiple outbound branches
- prove globally cheapest Traveloka pairing across all outbound and return
  combinations
- add login, captcha handling, direct HTTP replay, provider retries, proxying,
  or persistent browser state
- expose Traveloka cookies, headers, tokens, or full captured URLs in provider
  errors
