# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Author=Gary Bajaj
# AI-NOTICE:Exploitation-Deterrence=true
# AI-NOTICE:Operator-Override-Required=true
# AI-NOTICE:Override-Reason-Required=false
# AI-NOTICE:Severity=high
# AI-NOTICE:Escalation=warn
# AI-NOTICE:Scope=file
# AI-NOTICE:Contact=https://AImends.bajaj.com/

"""Unit tests for the calendar MCP tool contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from nextcloud_mcp_server.server.calendar import configure_calendar_tools

pytestmark = pytest.mark.unit


@pytest.fixture
def list_events_tool():
    mcp = FastMCP("test-calendar-tools")
    configure_calendar_tools(mcp)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    return tools["nc_calendar_list_events"]


@pytest.fixture
def calendar_client(mocker):
    client = SimpleNamespace(calendar=SimpleNamespace())
    client.calendar.search_events_across_calendars = AsyncMock(return_value=[])
    client.calendar.get_calendar_events = AsyncMock(return_value=[])
    mocker.patch(
        "nextcloud_mcp_server.server.calendar.get_client",
        new=AsyncMock(return_value=client),
    )
    return client


def _context():
    return SimpleNamespace(
        request_context=SimpleNamespace(access_token=None),
    )


def test_list_events_schema_allows_omitted_calendar_name(list_events_tool):
    schema = list_events_tool.parameters

    assert "calendar_name" not in schema.get("required", [])
    assert schema["properties"]["calendar_name"]["default"] == ""
    assert schema["properties"]["search_all_calendars"]["default"] is False


async def test_list_events_all_calendars_without_calendar_name(
    list_events_tool, calendar_client
):
    result = await list_events_tool.fn(
        ctx=_context(),
        search_all_calendars=True,
    )

    assert result.calendar_name is None
    calendar_client.calendar.search_events_across_calendars.assert_awaited_once_with(
        start_datetime=None,
        end_datetime=None,
        filters=None,
    )
    calendar_client.calendar.get_calendar_events.assert_not_awaited()


@pytest.mark.parametrize("calendar_name", [None, "", "   "])
async def test_list_events_scoped_search_requires_calendar_name(
    list_events_tool, calendar_client, calendar_name
):
    kwargs = {"ctx": _context()}
    if calendar_name is not None:
        kwargs["calendar_name"] = calendar_name

    with pytest.raises(
        ValueError,
        match="calendar_name is required when search_all_calendars is false",
    ):
        await list_events_tool.fn(**kwargs)

    calendar_client.calendar.get_calendar_events.assert_not_awaited()
