"""Expose optional creation identity without changing ordinary todo arguments."""

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

import pytest
from mcp.server.mcpserver import MCPServer

from nextcloud_mcp_server.models.calendar import Reminder
from nextcloud_mcp_server.server.calendar import configure_calendar_tools

pytestmark = pytest.mark.unit


@pytest.fixture
def todo_tool(mocker):
    server = MCPServer("test")
    configure_calendar_tools(server)
    client = mocker.MagicMock()
    client.calendar.create_todo = mocker.AsyncMock(return_value={"outcome": "reused"})
    mocker.patch(
        "nextcloud_mcp_server.server.calendar.get_client",
        mocker.AsyncMock(return_value=client),
    )
    mocker.patch(
        "nextcloud_mcp_server.auth.scope_authorization.get_settings",
        return_value=mocker.MagicMock(enable_login_flow=False),
    )
    return server._tool_manager.get_tool("nc_calendar_create_todo"), client


async def test_creation_uid_is_optional_and_forwarded_separately(todo_tool, mocker):
    tool, client = todo_tool
    uid = "ce78a5b3-394b-42a1-93e6-96850d223f7a"
    result = await tool.fn(
        calendar_name="persona",
        summary="Task",
        ctx=mocker.MagicMock(),
        creation_uid=uid,
        reminders=[Reminder(minutes_before=15)],
    )

    assert result == {"outcome": "reused"}
    assert client.calendar.create_todo.call_args.kwargs == {"creation_uid": uid}
    data = client.calendar.create_todo.call_args.args[1]
    assert "creation_uid" not in data
    assert data["reminders"] == [
        {
            "minutes_before": 15,
            "action": "DISPLAY",
            "attendees": [],
            "attachments": [],
        }
    ]
    assert "creation_uid" in tool.parameters["properties"]
    assert "creation_uid" not in tool.parameters["required"]


async def test_ordinary_call_keeps_omitted_reminders_and_two_arguments(
    todo_tool, mocker
):
    tool, client = todo_tool
    await tool.fn(calendar_name="persona", summary="Task", ctx=mocker.MagicMock())

    call = client.calendar.create_todo.call_args
    assert call.kwargs == {}
    assert call.args == (
        "persona",
        {
            "summary": "Task",
            "description": "",
            "status": "NEEDS-ACTION",
            "priority": 0,
            "due": "",
            "dtstart": "",
            "categories": "",
        },
    )
