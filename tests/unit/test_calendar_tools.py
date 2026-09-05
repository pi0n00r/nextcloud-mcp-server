"""Unit tests for the calendar MCP tool contract."""

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

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from nextcloud_mcp_server.client.calendar import (
    CalendarEtagConflictError,
    CalendarEtagUnavailableError,
)
from nextcloud_mcp_server.server.calendar import configure_calendar_tools

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def basicauth_mode():
    """Pin direct tool calls to the single-user BasicAuth scope path."""
    with patch(
        "nextcloud_mcp_server.auth.scope_authorization.get_settings",
        return_value=SimpleNamespace(enable_login_flow=False),
    ):
        yield


@pytest.fixture
def list_events_tool():
    mcp = MCPServer("test-calendar-tools")
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


def test_calendar_tool_schemas_keep_valarm_and_completion_aliases():
    mcp = MCPServer("test-calendar-compatibility")
    configure_calendar_tools(mcp)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    for name in (
        "nc_calendar_create_event",
        "nc_calendar_update_event",
        "nc_calendar_create_todo",
        "nc_calendar_update_todo",
    ):
        assert "reminders" in tools[name].parameters["properties"]

    assert "etag" in tools["nc_calendar_update_event"].parameters["properties"]
    assert "etag" in tools["nc_calendar_update_todo"].parameters["properties"]

    complete_properties = tools["nc_calendar_complete_todo"].parameters["properties"]
    assert "completed" in complete_properties
    assert "completed_at" in complete_properties
    assert "etag" in complete_properties
    assert "etag" in tools["nc_calendar_complete_todo"].parameters["required"]
    assert "nc_calendar_get_todo" in tools
    assert getattr(tools["nc_calendar_get_todo"].fn, "_required_scopes") == [
        "todo.read",
        "calendar.read",
    ]
    assert (
        tools["nc_calendar_list_todos"].parameters["properties"]["include_completed"][
            "default"
        ]
        is True
    )
    assert (
        tools["nc_calendar_search_todos"].parameters["properties"]["include_completed"][
            "default"
        ]
        is True
    )


async def test_complete_todo_accepts_completed_at_alias():
    mcp = MCPServer("test-calendar-completion-alias")
    configure_calendar_tools(mcp)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    update_todo = AsyncMock(return_value={"href": "/calendars/tasks/todo.ics"})
    client = SimpleNamespace(calendar=SimpleNamespace(update_todo=update_todo))

    with patch(
        "nextcloud_mcp_server.server.calendar.get_client",
        new=AsyncMock(return_value=client),
    ):
        result = await tools["nc_calendar_complete_todo"].fn(
            calendar_name="tasks",
            todo_uid="todo-1",
            ctx=_context(),
            etag='"todo-v1"',
            completed_at="2026-07-26T00:00:00+00:00",
        )

    assert result.completed == "2026-07-26T00:00:00+00:00"
    update_todo.assert_awaited_once_with(
        "tasks",
        "todo-1",
        {
            "status": "COMPLETED",
            "percent_complete": 100,
            "completed": "2026-07-26T00:00:00+00:00",
        },
        '"todo-v1"',
    )


@pytest.mark.parametrize(
    ("error", "message_pattern"),
    [
        (
            CalendarEtagConflictError("changed", current_etag='"todo-v2"'),
            'modified since it was read.*Current ETag: "todo-v2"',
        ),
        (
            CalendarEtagUnavailableError("malformed ETag"),
            "malformed ETag.*update was not sent",
        ),
    ],
)
async def test_complete_todo_maps_etag_errors(error, message_pattern):
    mcp = MCPServer("test-calendar-completion-errors")
    configure_calendar_tools(mcp)
    tool = {item.name: item for item in mcp._tool_manager.list_tools()}[
        "nc_calendar_complete_todo"
    ]
    update_todo = AsyncMock(side_effect=error)
    client = SimpleNamespace(calendar=SimpleNamespace(update_todo=update_todo))

    with (
        patch(
            "nextcloud_mcp_server.server.calendar.get_client",
            new=AsyncMock(return_value=client),
        ),
        pytest.raises(ToolError, match=message_pattern),
    ):
        await tool.fn(
            calendar_name="tasks",
            todo_uid="todo-1",
            ctx=_context(),
            etag='"todo-v1"',
        )

    update_todo.assert_awaited_once()


async def test_completion_verifies_after_forwarding_exact_etag():
    mcp = MCPServer("test-calendar-completion-verification")
    configure_calendar_tools(mcp)
    tool = {item.name: item for item in mcp._tool_manager.list_tools()}[
        "nc_calendar_complete_todo"
    ]
    update_todo = AsyncMock(return_value={"href": "/todo.ics", "etag": '"todo-v2"'})
    get_todo = AsyncMock(
        return_value={"status": "COMPLETED", "completed": "2026-07-26T00:00:00Z"}
    )
    client = SimpleNamespace(
        calendar=SimpleNamespace(update_todo=update_todo, get_todo=get_todo)
    )

    with patch(
        "nextcloud_mcp_server.server.calendar.get_client",
        new=AsyncMock(return_value=client),
    ):
        result = await tool.fn(
            calendar_name="tasks",
            todo_uid="todo-1",
            ctx=_context(),
            etag='"todo-v1"',
        )

    assert result.verified is True
    assert result.etag == '"todo-v2"'
    assert update_todo.await_args.args[3] == '"todo-v1"'
    get_todo.assert_awaited_once_with("tasks", "todo-1")


async def test_readback_failure_does_not_weaken_or_mask_successful_write():
    mcp = MCPServer("test-calendar-completion-readback-failure")
    configure_calendar_tools(mcp)
    tool = {item.name: item for item in mcp._tool_manager.list_tools()}[
        "nc_calendar_complete_todo"
    ]
    update_todo = AsyncMock(return_value={"etag": '"todo-v2"'})
    get_todo = AsyncMock(side_effect=RuntimeError("REPORT unavailable"))
    client = SimpleNamespace(
        calendar=SimpleNamespace(update_todo=update_todo, get_todo=get_todo)
    )

    with patch(
        "nextcloud_mcp_server.server.calendar.get_client",
        new=AsyncMock(return_value=client),
    ):
        result = await tool.fn(
            calendar_name="tasks",
            todo_uid="todo-1",
            ctx=_context(),
            etag='"todo-v1"',
        )

    assert result.verified is False
    assert "REPORT unavailable" in result.verification_error
    assert update_todo.await_args.args[3] == '"todo-v1"'


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


def test_event_summary_preserves_exact_etag():
    from nextcloud_mcp_server.server.calendar import _event_dict_to_summary

    summary = _event_dict_to_summary(
        {
            "uid": "event-1",
            "title": "Event",
            "start_datetime": "2026-07-26",
            "etag": 'W/"opaque"',
        }
    )

    assert summary.etag == 'W/"opaque"'


@pytest.mark.parametrize(
    ("tool_name", "method_name", "uid_name", "uid"),
    [
        ("nc_calendar_update_event", "update_event", "event_uid", "event-1"),
        ("nc_calendar_update_todo", "update_todo", "todo_uid", "todo-1"),
    ],
)
@pytest.mark.parametrize(
    ("error", "message_pattern"),
    [
        (
            CalendarEtagConflictError("changed", current_etag='"current-version"'),
            'modified since it was read.*Current ETag: "current-version".*Read',
        ),
        (
            CalendarEtagUnavailableError("server supplied no usable strong ETag"),
            "no usable strong ETag.*update was not sent.*read",
        ),
    ],
)
async def test_update_tools_translate_etag_errors_to_actionable_toolerror(
    tool_name, method_name, uid_name, uid, error, message_pattern
):
    mcp = MCPServer("test-calendar-etag-errors")
    configure_calendar_tools(mcp)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    update = AsyncMock(side_effect=error)
    client = SimpleNamespace(calendar=SimpleNamespace(**{method_name: update}))
    kwargs = {
        "calendar_name": "main",
        uid_name: uid,
        "ctx": _context(),
        "etag": '"stale"',
    }

    with (
        patch(
            "nextcloud_mcp_server.server.calendar.get_client",
            new=AsyncMock(return_value=client),
        ),
        pytest.raises(ToolError, match=message_pattern),
    ):
        await tools[tool_name].fn(**kwargs)

    update.assert_awaited_once()


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
