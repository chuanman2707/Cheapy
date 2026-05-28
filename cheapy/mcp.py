"""Cheapy MCP server."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp import types
from mcp.server.fastmcp import FastMCP
from pydantic import ConfigDict, Field

from cheapy.markdown_report import render_search_report
from cheapy.models import PassengersV1, SearchMode, SearchRequestV1, SearchResponseV1
from cheapy.search_service import search_with_storage


_RENDERER_FALLBACK_TEXT = (
    "## Cheapy flight search results\n\n"
    "Structured results are available in the MCP response.\n"
)

_TOOL_ANNOTATIONS: dict[str, object] = {
    "title": "Search Cheapest Flights",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
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
        result = await asyncio.to_thread(search_with_storage, request)
        markdown = _render_search_report_for_mcp(request, result.response)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=markdown)],
            structuredContent=result.response.model_dump(mode="json"),
            isError=False,
        )

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


def _render_search_report_for_mcp(
    request: SearchRequestV1, response: SearchResponseV1
) -> str:
    try:
        return render_search_report(request, response)
    except Exception:
        return _RENDERER_FALLBACK_TEXT


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
