"""Cloakbrowser-backed bootstrap helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from inspect import Parameter, signature
from io import StringIO
from time import monotonic

from cheapy.browser_bootstrap.cookies import cookie_header_from_browser_cookies
from cheapy.browser_bootstrap.errors import (
    BrowserBootstrapCookieUnavailable,
    BrowserBootstrapError,
    blocked_error,
    capture_unavailable_error,
    timeout_error,
    unavailable_error,
)
from cheapy.browser_bootstrap.types import (
    BrowserBootstrapSession,
    BrowserLauncher,
    BrowserNetworkCapture,
    CapturedExchange,
    CapturedRequest,
    CapturedResponse,
    RequestPredicate,
    ResponsePredicate,
)


_BLOCKED_HTTP_STATUS_CODES = {401, 403, 429}
_NAVIGATOR_USER_AGENT_SCRIPT = "() => navigator.userAgent"
_CAPTURE_WAIT_SLICE_MS = 100


def launch_browser(**kwargs: object) -> object:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        from cloakbrowser import launch

        return launch(**kwargs)


_DEFAULT_LAUNCH_BROWSER = launch_browser


def bootstrap_cookies(
    page_url: str,
    deadline_monotonic: float,
    *,
    wait_until: str = "domcontentloaded",
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserBootstrapSession:
    browser: object | None = None
    context: object | None = None
    operation_failed = False
    result: BrowserBootstrapSession | None = None
    try:
        browser = _open_browser(
            launch_browser or _DEFAULT_LAUNCH_BROWSER,
            deadline_monotonic,
        )
        context = _new_context(
            browser,
            deadline_monotonic,
            user_agent=user_agent,
        )
        page = _new_page(context, deadline_monotonic)
        _goto(page, page_url, deadline_monotonic, wait_until=wait_until)
        cookie_header = _read_cookie_header(context, deadline_monotonic)
        observed_user_agent = _read_user_agent(page, deadline_monotonic)
        result = BrowserBootstrapSession(
            cookie_header=cookie_header,
            user_agent=observed_user_agent,
            created_monotonic=monotonic(),
        )
    except Exception:
        operation_failed = True
        raise
    finally:
        _cleanup_browser_resources(
            context,
            browser,
            deadline_monotonic,
            suppress_errors=operation_failed,
        )
    assert result is not None
    return result


def capture_first_party_requests(
    page_url: str,
    deadline_monotonic: float,
    *,
    request_predicate: RequestPredicate,
    response_predicate: ResponsePredicate | None = None,
    wait_until: str = "domcontentloaded",
    user_agent: str | None = None,
    launch_browser: BrowserLauncher | None = None,
) -> BrowserNetworkCapture:
    browser: object | None = None
    context: object | None = None
    operation_failed = False
    result: BrowserNetworkCapture | None = None
    try:
        browser = _open_browser(
            launch_browser or _DEFAULT_LAUNCH_BROWSER,
            deadline_monotonic,
        )
        context = _new_context(
            browser,
            deadline_monotonic,
            user_agent=user_agent,
        )
        page = _new_page(context, deadline_monotonic)

        sequence = 0
        exchanges: list[CapturedExchange] = []
        pending_by_request_id: dict[int, int] = {}
        request_id_by_exchange_index: dict[int, int] = {}
        pending_by_url: dict[str, list[int]] = {}

        def next_sequence() -> int:
            nonlocal sequence
            sequence += 1
            return sequence

        def handle_request(request: object) -> None:
            _remaining_timeout_ms(deadline_monotonic, phase="capture_wait")
            captured = CapturedRequest(
                url=_object_url(request),
                method=_request_method(request),
                sequence=next_sequence(),
                headers=_request_headers(request),
                post_data=_request_post_data(request),
            )
            if not request_predicate(captured):
                return
            exchange = CapturedExchange(
                sequence=captured.sequence,
                captured_monotonic=monotonic(),
                request=captured,
            )
            exchanges.append(exchange)
            exchange_index = len(exchanges) - 1
            pending_by_request_id[id(request)] = exchange_index
            request_id_by_exchange_index[exchange_index] = id(request)
            pending_by_url.setdefault(captured.url, []).append(exchange_index)

        def handle_response(response: object) -> None:
            _remaining_timeout_ms(deadline_monotonic, phase="capture_wait")
            response_url = _object_url(response)
            response_request = _response_request(response)
            if response_request is not None:
                exchange_index = pending_by_request_id.get(id(response_request))
                if exchange_index is None:
                    return
                if pair_response(exchange_index, response, response_url):
                    pending_by_request_id.pop(id(response_request), None)
                    remove_pending_url_index(response_url, exchange_index)
                return

            pending = pending_by_url.get(response_url)
            if not pending:
                return
            for pending_index, exchange_index in enumerate(pending):
                if pair_response(exchange_index, response, response_url):
                    pending_by_request_id.pop(
                        request_id_by_exchange_index.get(exchange_index),
                        None,
                    )
                    del pending[pending_index]
                    break
            if not pending:
                pending_by_url.pop(response_url, None)

        def pair_response(
            exchange_index: int,
            response: object,
            response_url: str,
        ) -> bool:
            exchange = exchanges[exchange_index]
            if exchange.response is not None:
                return False
            captured = CapturedResponse(
                url=response_url,
                status_code=_response_status_code(response),
                payload=_response_payload(response),
                sequence=exchange.sequence,
            )
            if response_predicate is not None and not response_predicate(captured):
                return False
            exchanges[exchange_index] = CapturedExchange(
                sequence=exchange.sequence,
                captured_monotonic=exchange.captured_monotonic,
                request=exchange.request,
                response=captured,
            )
            return True

        def remove_pending_url_index(response_url: str, exchange_index: int) -> None:
            pending = pending_by_url.get(response_url)
            if pending is None:
                return
            try:
                pending.remove(exchange_index)
            except ValueError:
                return
            if not pending:
                pending_by_url.pop(response_url, None)

        _remaining_timeout_ms(deadline_monotonic, phase="context_page_setup")
        _register_capture_handlers(page, handle_request, handle_response)
        _goto(page, page_url, deadline_monotonic, wait_until=wait_until)
        response_required = response_predicate is not None
        _wait_for_capture(
            page,
            deadline_monotonic,
            ready=lambda: _capture_ready(
                exchanges,
                response_required=response_required,
            ),
        )
        if not exchanges:
            raise capture_unavailable_error(phase="capture_wait")
        if response_required and not _has_captured_response(exchanges):
            raise capture_unavailable_error(phase="capture_wait")

        cookie_header = _read_cookie_header(context, deadline_monotonic)
        observed_user_agent = _read_user_agent(page, deadline_monotonic)
        result = BrowserNetworkCapture(
            cookie_header=cookie_header,
            user_agent=observed_user_agent,
            exchanges=tuple(exchanges),
            created_monotonic=monotonic(),
        )
    except Exception:
        operation_failed = True
        raise
    finally:
        _cleanup_browser_resources(
            context,
            browser,
            deadline_monotonic,
            suppress_errors=operation_failed,
        )
    assert result is not None
    return result


def _open_browser(
    launcher: BrowserLauncher,
    deadline_monotonic: float,
) -> object:
    try:
        return launcher(
            headless=True,
            timeout=_remaining_timeout_ms(deadline_monotonic, phase="launch"),
        )
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        _raise_mapped_exception(exc, phase="launch")


def _new_context(
    browser: object,
    deadline_monotonic: float,
    *,
    user_agent: str | None,
) -> object:
    try:
        _remaining_timeout_ms(deadline_monotonic, phase="context_page_setup")
        kwargs = {"user_agent": user_agent} if user_agent is not None else {}
        return browser.new_context(**kwargs)  # type: ignore[attr-defined]
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        _raise_mapped_exception(exc, phase="context_page_setup")


def _new_page(context: object, deadline_monotonic: float) -> object:
    try:
        _remaining_timeout_ms(deadline_monotonic, phase="context_page_setup")
        return context.new_page()  # type: ignore[attr-defined]
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        _raise_mapped_exception(exc, phase="context_page_setup")


def _register_capture_handlers(
    page: object,
    request_handler: object,
    response_handler: object,
) -> None:
    try:
        page.on("request", request_handler)  # type: ignore[attr-defined]
        page.on("response", response_handler)  # type: ignore[attr-defined]
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        _raise_mapped_exception(exc, phase="context_page_setup")


def _goto(
    page: object,
    page_url: str,
    deadline_monotonic: float,
    *,
    wait_until: str,
) -> None:
    try:
        response = page.goto(  # type: ignore[attr-defined]
            page_url,
            wait_until=wait_until,
            timeout=_remaining_timeout_ms(deadline_monotonic, phase="navigation"),
        )
        blocked_status = _blocked_status_from_object(response)
        if blocked_status is not None:
            raise blocked_error(
                phase="navigation",
                http_status_code=blocked_status,
            )
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        _raise_mapped_exception(exc, phase="navigation")


def _read_cookie_header(context: object, deadline_monotonic: float) -> str:
    try:
        _remaining_timeout_ms(deadline_monotonic, phase="cookie_read")
        cookies = context.cookies()  # type: ignore[attr-defined]
        return cookie_header_from_browser_cookies(cookies)
    except BrowserBootstrapCookieUnavailable:
        raise
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        _raise_mapped_exception(exc, phase="cookie_read")


def _read_user_agent(page: object, deadline_monotonic: float) -> str:
    try:
        _remaining_timeout_ms(deadline_monotonic, phase="user_agent_read")
        user_agent = page.evaluate(_NAVIGATOR_USER_AGENT_SCRIPT)  # type: ignore[attr-defined]
    except BrowserBootstrapError:
        raise
    except Exception as exc:
        _raise_mapped_exception(exc, phase="user_agent_read")
    return user_agent if isinstance(user_agent, str) else str(user_agent)


def _wait_for_capture(
    page: object,
    deadline_monotonic: float,
    *,
    ready: Callable[[], bool],
) -> None:
    if ready():
        return
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if not callable(wait_for_timeout):
        return

    wait_budget_ms = _remaining_timeout_ms(deadline_monotonic, phase="capture_wait")
    waited_ms = 0
    while not ready() and waited_ms < wait_budget_ms:
        remaining_seconds = deadline_monotonic - monotonic()
        if remaining_seconds <= 0:
            break
        remaining_ms = max(1, round(remaining_seconds * 1000))
        wait_ms = min(_CAPTURE_WAIT_SLICE_MS, remaining_ms, wait_budget_ms - waited_ms)
        try:
            wait_for_timeout(wait_ms)
        except BrowserBootstrapError:
            raise
        except Exception as exc:
            _raise_mapped_exception(exc, phase="capture_wait")
        waited_ms += wait_ms


def _capture_ready(
    exchanges: list[CapturedExchange],
    *,
    response_required: bool,
) -> bool:
    if not response_required:
        return bool(exchanges)
    return _has_captured_response(exchanges)


def _has_captured_response(exchanges: list[CapturedExchange]) -> bool:
    return any(exchange.response is not None for exchange in exchanges)


def _remaining_timeout_ms(deadline_monotonic: float, *, phase: str) -> int:
    remaining_seconds = deadline_monotonic - monotonic()
    if remaining_seconds <= 0:
        raise timeout_error(phase=phase)
    return max(1, round(remaining_seconds * 1000))


def _raise_mapped_exception(exc: Exception, *, phase: str) -> None:
    exception_type = type(exc).__name__
    if _is_timeout_exception(exc):
        raise timeout_error(phase=phase, exception_type=exception_type) from None
    if phase == "navigation":
        blocked_status = _blocked_status_from_exception(exc)
        if blocked_status is not None:
            raise blocked_error(
                phase=phase,
                http_status_code=blocked_status,
                exception_type=exception_type,
            ) from None
    raise unavailable_error(phase=phase, exception_type=exception_type) from None


def _is_timeout_exception(exc: Exception) -> bool:
    type_name = type(exc).__name__.lower()
    module_name = type(exc).__module__.lower()
    return isinstance(exc, TimeoutError) or "timeout" in type_name or (
        "playwright" in module_name and "timeout" in type_name
    )


def _blocked_status_from_exception(exc: Exception) -> int | None:
    status = _blocked_status_from_object(exc)
    if status is not None:
        return status
    match = re.search(r"\b(401|403|429)\b", str(exc))
    if match is None:
        return None
    return int(match.group(1))


def _blocked_status_from_object(source: object | None) -> int | None:
    status = _status_from_object(source)
    if status in _BLOCKED_HTTP_STATUS_CODES:
        return status
    return None


def _status_from_object(source: object | None) -> int | None:
    if source is None:
        return None
    for attribute in ("status", "status_code", "http_status_code"):
        value = _attribute_value(source, attribute, default=None)
        status = _int_or_none(value)
        if status is not None:
            return status
    response = getattr(source, "response", None)
    if response is not None and response is not source:
        return _status_from_object(response)
    return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _object_url(source: object) -> str:
    value = _attribute_value(source, "url", default="")
    return value if isinstance(value, str) else str(value)


def _response_request(response: object) -> object | None:
    return _attribute_value(response, "request", default=None)


def _request_method(request: object) -> str:
    value = _attribute_value(request, "method", default="GET")
    return value if isinstance(value, str) else str(value)


def _request_headers(request: object) -> dict[str, str]:
    value = _attribute_value(request, "headers", default={})
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _request_post_data(request: object) -> str | None:
    value = _attribute_value(request, "post_data", default=None)
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _response_status_code(response: object) -> int:
    status = _status_from_object(response)
    return status if status is not None else 0


def _response_payload(response: object) -> object | None:
    json_reader = getattr(response, "json", None)
    if callable(json_reader):
        try:
            return json_reader()
        except Exception:
            return None
    text_reader = getattr(response, "text", None)
    if callable(text_reader):
        try:
            return text_reader()
        except Exception:
            return None
    return None


def _attribute_value(source: object, name: str, *, default: object) -> object:
    value = getattr(source, name, default)
    if callable(value):
        try:
            return value()
        except TypeError:
            return value
    return value


def _cleanup_browser_resources(
    context: object | None,
    browser: object | None,
    deadline_monotonic: float,
    *,
    suppress_errors: bool,
) -> None:
    cleanup_error: Exception | None = None
    for target in (context, browser):
        try:
            _close_best_effort(target, deadline_monotonic)
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = exc
    if cleanup_error is not None and not suppress_errors:
        raise unavailable_error(
            phase="cleanup",
            exception_type=type(cleanup_error).__name__,
        ) from None


def _close_best_effort(target: object | None, deadline_monotonic: float) -> None:
    if target is None:
        return
    close = getattr(target, "close", None)
    if not callable(close):
        return
    if _supports_timeout_keyword(close):
        close(timeout=_remaining_timeout_ms_for_cleanup(deadline_monotonic))
        return
    close()


def _supports_timeout_keyword(close: object) -> bool:
    try:
        close_signature = signature(close)
    except (TypeError, ValueError):
        return False
    for parameter in close_signature.parameters.values():
        if parameter.kind == Parameter.VAR_KEYWORD:
            return True
        if parameter.name == "timeout" and parameter.kind in {
            Parameter.KEYWORD_ONLY,
            Parameter.POSITIONAL_OR_KEYWORD,
        }:
            return True
    return False


def _remaining_timeout_ms_for_cleanup(deadline_monotonic: float) -> int:
    remaining_seconds = deadline_monotonic - monotonic()
    return max(1, round(remaining_seconds * 1000))
