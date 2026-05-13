"""Cheapy MCP server."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ConfigDict, Field

from cheapy.models import PassengersV1, SearchMode, SearchRequestV1, SearchResponseV1
from cheapy.search import search_exact


_TOOL_ANNOTATIONS: dict[str, object] = {
    "title": "Search Cheapest Flights",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def create_mcp_server() -> FastMCP:
    """Create the Cheapy MCP server."""
    server = FastMCP("cheapy_mcp")

    @server.tool(
        name="search_cheapest_flights",
        annotations=_TOOL_ANNOTATIONS,
    )
    async def search_cheapest_flights(
        schema_version: Any,
        origin: Any,
        destination: Any,
        departure_date: Any,
        return_date: Any = None,
        search_mode: Any = SearchMode.EXACT,
        passengers: Any = Field(default_factory=PassengersV1),
        max_results: Any = 5,
    ) -> SearchResponseV1:
        """Search one-way or round-trip flights and return Contract V1 results."""
        request = SearchRequestV1.model_validate(
            _request_payload(
                schema_version=schema_version,
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                return_date=return_date,
                search_mode=search_mode,
                passengers=passengers,
                max_results=max_results,
            )
        )
        return await asyncio.to_thread(search_exact, request)

    # FastMCP otherwise coerces scalar inputs and drops extra args before the
    # strict SearchRequestV1 boundary, so the SDK minor is capped and this
    # adapter patches the generated arg model/schema to preserve Contract V1.
    tool = server._tool_manager.get_tool("search_cheapest_flights")
    if tool is None:  # pragma: no cover
        raise RuntimeError("search_cheapest_flights tool was not registered")
    tool.fn_metadata.arg_model.model_config = ConfigDict(
        **tool.fn_metadata.arg_model.model_config,
        extra="forbid",
    )
    tool.fn_metadata.arg_model.model_rebuild(force=True)
    tool.parameters = SearchRequestV1.model_json_schema()

    return server


def run_stdio_server() -> None:
    """Run the Cheapy MCP server over stdio."""
    create_mcp_server().run()


def _request_payload(
    *,
    schema_version: object,
    origin: object,
    destination: object,
    departure_date: object,
    return_date: object,
    search_mode: object,
    passengers: object,
    max_results: object,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date,
        "search_mode": (
            search_mode.value if isinstance(search_mode, SearchMode) else search_mode
        ),
        "max_results": max_results,
    }
    if isinstance(passengers, PassengersV1):
        payload["passengers"] = passengers.model_dump(mode="json")
    else:
        payload["passengers"] = passengers
    return payload
