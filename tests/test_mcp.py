from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import sys
from typing import Any, TypeVar

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from cheapy.mcp import create_mcp_server
from cheapy.models import (
    FlightLegV1,
    OfferFlagsV1,
    SearchMode,
    SearchResponseV1,
    SearchStatus,
)
from cheapy.search_service import SearchWithStorageResult


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


def _mcp_tool() -> Any:
    server = create_mcp_server()
    tool = server._tool_manager.get_tool("search_cheapest_flights")
    assert tool is not None
    return tool


def _structured_content(result: Any) -> dict[str, Any]:
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        assert isinstance(structured, dict)
        return structured
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


def test_mcp_does_not_expose_skyscanner_discovery_tool() -> None:
    server = create_mcp_server()

    assert server._tool_manager.get_tool("skyscanner_graphql_bundle_scan") is None


def test_python_module_mcp_entrypoint_still_lists_tools_after_cli_nesting() -> None:
    async def action(session: ClientSession) -> list[str]:
        response = await session.list_tools()
        return [tool.name for tool in response.tools]

    tool_names = asyncio.run(_with_mcp_session(action))

    assert tool_names == ["search_cheapest_flights"]


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
    assert input_schema["additionalProperties"] is False
    assert properties["max_results"]["minimum"] == 1
    assert properties["max_results"]["maximum"] == 20


def test_mcp_search_tool_annotations_reflect_local_history_write() -> None:
    tool = _mcp_tool()

    assert tool.annotations.openWorldHint is True
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.idempotentHint is False
    assert tool.annotations.destructiveHint is False


def test_mcp_search_tool_returns_structured_contract_response(
    monkeypatch: Any,
) -> None:
    public_search_url = (
        "https://www.traveloka.com/en-en/flight/fulltwosearch?"
        "ap=CXR.SGN&dt=10-7-2026.15-7-2026&ps=1.0.0&sc=ECONOMY"
    )

    def fake_search_with_storage(request: Any) -> SearchWithStorageResult:
        assert request.origin == "CXR"
        assert request.destination == "SGN"
        assert request.departure_date == "2026-07-10"
        assert request.return_date == "2026-07-15"
        assert request.search_mode == SearchMode.EXPANDED
        response = SearchResponseV1.model_validate(
            {
                "schema_version": "1",
                "status": "success",
                "request_id": (
                    "search:round_trip:CXR:SGN:2026-07-10:2026-07-15:"
                    "expanded:1:0:0:0:5"
                ),
                "offers": [
                    {
                        "offer_id": "traveloka:CXR-SGN:2026-07-10:1",
                        "price_amount": 1_280_000.0,
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
                        "requested_return_date": "2026-07-15",
                        "actual_return_date": "2026-07-15",
                        "return_offset_days": 0,
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
                        "public_search_url": public_search_url,
                    }
                ],
                "warnings": [],
                "errors": [],
                "provider_statuses": [],
                "search_plan": {
                    "search_mode": "expanded",
                    "planned_candidate_count": 1,
                    "executed_candidate_count": 1,
                    "planned_provider_call_count": 1,
                    "executed_provider_call_count": 1,
                    "candidate_count_by_family": {"exact": 1},
                    "provider_call_count_by_family": {"exact": 1},
                    "truncated": False,
                    "truncated_families": [],
                    "candidate_families": ["exact"],
                },
                "mixed_currency": False,
                "currency_groups": [],
                "currency_notes": [],
                "candidates": None,
            }
        )
        return SearchWithStorageResult(
            response=response,
            search_run_id=1,
            storage_enabled=True,
            storage_warning=None,
        )

    monkeypatch.setattr("cheapy.mcp.search_with_storage", fake_search_with_storage)
    tool = _mcp_tool()
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "return_date": "2026-07-15",
        "search_mode": "expanded",
        "passengers": {
            "adults": 1,
            "children": 0,
            "infants_on_lap": 0,
            "infants_in_seat": 0,
        },
        "max_results": 5,
    }

    result = asyncio.run(tool.run(arguments, convert_result=True))

    payload = _structured_content(result)
    response = SearchResponseV1.model_validate(payload)
    assert response.schema_version == "1"
    assert response.status == SearchStatus.SUCCESS
    assert (
        "search:round_trip:CXR:SGN:2026-07-10:2026-07-15:"
        "expanded:1:0:0:0:5"
    ) in response.request_id
    assert payload["offers"][0]["public_search_url"] == public_search_url
    assert response.offers[0].public_search_url == public_search_url
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


def test_mcp_search_tool_rejects_string_max_results() -> None:
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "max_results": "5",
    }

    async def action(session: ClientSession) -> Any:
        return await session.call_tool("search_cheapest_flights", arguments)

    result = asyncio.run(_with_mcp_session(action))

    assert _is_error(result) is True
    text = _text_content(result).lower()
    assert "max_results" in text or "validation" in text


def test_mcp_search_tool_rejects_unknown_top_level_field() -> None:
    arguments = {
        "schema_version": "1",
        "origin": "CXR",
        "destination": "SGN",
        "departure_date": "2026-07-10",
        "foo": "bar",
    }

    async def action(session: ClientSession) -> Any:
        return await session.call_tool("search_cheapest_flights", arguments)

    result = asyncio.run(_with_mcp_session(action))

    assert _is_error(result) is True
    text = _text_content(result).lower()
    assert "foo" in text or "extra" in text or "validation" in text
