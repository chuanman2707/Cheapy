"""Visible option activation helpers for the Traveloka research provider."""

from __future__ import annotations

from cheapy.providers.traveloka.inventory import TravelokaVisibleOption


VISIBLE_OPTION_CLICK_TIMEOUT_MS = 10_000
TRAVELOKA_OPTION_ACTIVATION_SCRIPT = """
node => {
  const base = {bubbles: true, cancelable: true, composed: true, view: window};
  const pointer = (type, buttons) => {
    if (typeof PointerEvent === 'function') {
      return new PointerEvent(type, Object.assign({}, base, {
        button: 0,
        buttons,
        pointerType: 'mouse',
        isPrimary: true,
      }));
    }
    return new MouseEvent(type, Object.assign({}, base, {button: 0, buttons}));
  };
  node.dispatchEvent(pointer('pointerdown', 1));
  node.dispatchEvent(new MouseEvent(
    'mousedown',
    Object.assign({}, base, {button: 0, buttons: 1})
  ));
  node.dispatchEvent(pointer('pointerup', 0));
  node.dispatchEvent(new MouseEvent(
    'mouseup',
    Object.assign({}, base, {button: 0, buttons: 0})
  ));
  node.dispatchEvent(new MouseEvent(
    'click',
    Object.assign({}, base, {button: 0, buttons: 0})
  ));
}
"""


def click_visible_option(
    option: TravelokaVisibleOption,
    *,
    timeout_ms: int = VISIBLE_OPTION_CLICK_TIMEOUT_MS,
) -> None:
    click_timeout_ms = max(1, min(timeout_ms, VISIBLE_OPTION_CLICK_TIMEOUT_MS))
    scroll = getattr(option.locator, "scroll_into_view_if_needed", None)
    if scroll is not None:
        try:
            scroll(timeout=click_timeout_ms)
        except Exception:
            pass

    evaluate = getattr(option.locator, "evaluate", None)
    if evaluate is not None:
        try:
            evaluate(TRAVELOKA_OPTION_ACTIVATION_SCRIPT, timeout=click_timeout_ms)
        except TypeError:
            evaluate(TRAVELOKA_OPTION_ACTIVATION_SCRIPT)
        return

    option.locator.click(timeout=click_timeout_ms)  # type: ignore[attr-defined]
