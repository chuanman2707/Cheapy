"""Provider-neutral browser bootstrap data structures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BrowserBootstrapSession:
    cookie_header: str = field(repr=False)
    user_agent: str = field(repr=False)
    created_monotonic: float


@dataclass(frozen=True, slots=True)
class CapturedRequest:
    url: str = field(repr=False)
    method: str
    sequence: int
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    post_data: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CapturedResponse:
    url: str = field(repr=False)
    status_code: int
    payload: Any = field(repr=False)
    sequence: int


@dataclass(frozen=True, slots=True)
class CapturedExchange:
    sequence: int
    captured_monotonic: float
    request: CapturedRequest = field(repr=False)
    response: CapturedResponse | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class BrowserNetworkCapture:
    cookie_header: str = field(repr=False)
    user_agent: str = field(repr=False)
    exchanges: tuple[CapturedExchange, ...] = field(repr=False)
    created_monotonic: float


BrowserLauncher = Callable[..., object]
RequestPredicate = Callable[[CapturedRequest], bool]
ResponsePredicate = Callable[[CapturedResponse], bool]
