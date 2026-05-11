"""Cheapy MCP server."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from cheapy.models import PassengersV1, SearchMode, SearchRequestV1, SearchResponseV1
from cheapy.search import search_exact


_TOOL_ANNOTATIONS: dict[str, object] = {
    "title": "Search Cheapest Flights",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def create_mcp_server() -> FastMCP:
    """Create the Cheapy MCP server."""
    server = FastMCP("cheapy_mcp")

    @server.tool(
        name="search_cheapest_flights",
        annotations=_TOOL_ANNOTATIONS,
    )
    async def search_cheapest_flights(
        schema_version: Literal["1"],
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        search_mode: SearchMode = SearchMode.EXACT,
        passengers: PassengersV1 = PassengersV1(),
        max_results: int = 5,
    ) -> SearchResponseV1:
        """Search exact one-way flights and return Contract V1 results."""
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

    return server


def run_stdio_server() -> None:
    """Run the Cheapy MCP server over stdio."""
    create_mcp_server().run()


def _request_payload(
    *,
    schema_version: Literal["1"],
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
    search_mode: SearchMode | str,
    passengers: object,
    max_results: int,
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
