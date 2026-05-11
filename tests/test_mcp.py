from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import sys
from typing import Any, TypeVar

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from cheapy.models import SearchResponseV1, SearchStatus


T = TypeVar("T")


async def _with_mcp_session(action: Callable[[ClientSession], Awaitable[T]]) -> T:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cheapy", "mcp"],
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await action(session)


def _input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    assert isinstance(schema, dict)
    return schema


def _structured_content(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    assert isinstance(structured, dict)
    return structured


def _is_error(result: Any) -> bool:
    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", None)
    assert isinstance(is_error, bool)
    return is_error


def _text_content(result: Any) -> str:
    return "\n".join(
        block.text
        for block in result.content
        if isinstance(block, types.TextContent)
    )


def test_mcp_lists_only_search_cheapest_flights_tool() -> None:
    async def action(session: ClientSession) -> list[Any]:
        response = await session.list_tools()
        return list(response.tools)

    tools = asyncio.run(_with_mcp_session(action))

    assert [tool.name for tool in tools] == ["search_cheapest_flights"]


def test_mcp_search_tool_uses_top_level_contract_fields() -> None:
    async def action(session: ClientSession) -> Any:
        response = await session.list_tools()
        return next(
            tool
            for tool in response.tools
            if tool.name == "search_cheapest_flights"
        )

    tool = asyncio.run(_with_mcp_session(action))
    input_schema = _input_schema(tool)
    properties = input_schema["properties"]

    assert "request" not in properties
    assert {
        "schema_version",
        "origin",
        "destination",
        "departure_date",
        "return_date",
        "search_mode",
        "passengers",
        "max_results",
    }.issubset(properties)
    assert {
        "schema_version",
        "origin",
        "destination",
        "departure_date",
    }.issubset(set(input_schema["required"]))


def test_mcp_search_tool_returns_structured_contract_response() -> None:
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": None,
        "search_mode": "exact",
        "passengers": {
            "adults": 1,
            "children": 0,
            "infants_on_lap": 0,
            "infants_in_seat": 0,
        },
        "max_results": 5,
    }

    async def action(session: ClientSession) -> Any:
        return await session.call_tool("search_cheapest_flights", arguments)

    result = asyncio.run(_with_mcp_session(action))

    assert _is_error(result) is False
    payload = _structured_content(result)
    response = SearchResponseV1.model_validate(payload)

    assert response.schema_version == "1"
    assert response.status == SearchStatus.SUCCESS
    assert [offer.offer_id for offer in response.offers] == [
        "manual_fixture:cxr-sgn-20260710-1",
        "manual_fixture:cxr-sgn-20260710-2",
    ]
    assert response.errors == []


def test_mcp_search_tool_rejects_invalid_contract_input() -> None:
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026/07/10",
    }

    async def action(session: ClientSession) -> Any:
        return await session.call_tool("search_cheapest_flights", arguments)

    result = asyncio.run(_with_mcp_session(action))

    assert _is_error(result) is True
    text = _text_content(result).lower()
    assert "date" in text or "validation" in text


def test_mcp_search_tool_rejects_null_passengers() -> None:
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "passengers": None,
    }

    async def action(session: ClientSession) -> Any:
        return await session.call_tool("search_cheapest_flights", arguments)

    result = asyncio.run(_with_mcp_session(action))

    assert _is_error(result) is True
    text = _text_content(result).lower()
    assert "passengers" in text or "validation" in text
