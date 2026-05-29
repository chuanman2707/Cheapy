from __future__ import annotations

from collections.abc import Iterable


class FakeRequest:
    def __init__(
        self,
        *,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        post_data: str | None = None,
    ) -> None:
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.post_data = post_data


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int = 200,
        payload: object | None = None,
        request: FakeRequest | None = None,
    ) -> None:
        self.url = url
        self.status = status
        self._payload = payload
        self.request = request

    def json(self) -> object | None:
        return self._payload


class FakePage:
    def __init__(
        self,
        *,
        events: Iterable[FakeRequest | FakeResponse] = (),
        wait_events: Iterable[Iterable[FakeRequest | FakeResponse]] = (),
        user_agent: str = "FakeBrowser/1.0",
        goto_exc: Exception | None = None,
        navigation_status: int | None = None,
    ) -> None:
        self.events = list(events)
        self.wait_events = [list(batch) for batch in wait_events]
        self.user_agent = user_agent
        self.goto_exc = goto_exc
        self.navigation_status = navigation_status
        self.handlers: dict[str, object] = {}
        self.goto_calls: list[dict[str, object]] = []
        self.evaluate_calls: list[str] = []
        self.wait_calls: list[int] = []

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> object | None:
        self.goto_calls.append(
            {"url": url, "wait_until": wait_until, "timeout": timeout}
        )
        if self.goto_exc is not None:
            raise self.goto_exc
        self._emit_events(self.events)
        if self.navigation_status is None:
            return None
        return FakeResponse(url=url, status=self.navigation_status)

    def evaluate(self, script: str) -> str:
        self.evaluate_calls.append(script)
        return self.user_agent

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls.append(milliseconds)
        if self.wait_events:
            self._emit_events(self.wait_events.pop(0))

    def _emit_events(self, events: Iterable[FakeRequest | FakeResponse]) -> None:
        for event in events:
            if isinstance(event, FakeRequest):
                handler = self.handlers.get("request")
                if callable(handler):
                    handler(event)
            else:
                handler = self.handlers.get("response")
                if callable(handler):
                    handler(event)


class FakeContext:
    def __init__(
        self,
        page: FakePage,
        *,
        cookies: list[dict[str, object]] | None = None,
        close_exc: Exception | None = None,
    ) -> None:
        self.page = page
        self._cookies = cookies or []
        self.close_exc = close_exc
        self.context_kwargs: dict[str, object] | None = None
        self.closed = False
        self.close_timeout: int | None = None

    def new_page(self) -> FakePage:
        return self.page

    def cookies(self) -> list[dict[str, object]]:
        return list(self._cookies)

    def close(self, *, timeout: int | None = None) -> None:
        self.close_timeout = timeout
        self.closed = True
        if self.close_exc is not None:
            raise self.close_exc


class FakeBrowser:
    def __init__(
        self,
        context: FakeContext,
        *,
        close_exc: Exception | None = None,
    ) -> None:
        self.context = context
        self.close_exc = close_exc
        self.closed = False
        self.new_context_calls: list[dict[str, object]] = []
        self.close_timeout: int | None = None

    def new_context(self, **kwargs: object) -> FakeContext:
        self.new_context_calls.append(kwargs)
        self.context.context_kwargs = kwargs
        return self.context

    def close(self, *, timeout: int | None = None) -> None:
        self.close_timeout = timeout
        self.closed = True
        if self.close_exc is not None:
            raise self.close_exc


def launcher_for(browser: FakeBrowser) -> object:
    def launch_browser(**kwargs: object) -> FakeBrowser:
        launch_browser.calls.append(kwargs)  # type: ignore[attr-defined]
        return browser

    launch_browser.calls = []  # type: ignore[attr-defined]
    return launch_browser
