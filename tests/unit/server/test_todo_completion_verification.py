"""Unit tests for todo completion: STATUS edit, UID addressing, read-back.

Completing a task must be an edit (STATUS=COMPLETED + PERCENT-COMPLETE +
COMPLETED timestamp), never a delete, and must be addressed by UID. The write
is then read back, because a 2xx on a CalDAV PUT says the request was accepted,
not that the stored resource says what was sent.
"""

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
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from nextcloud_mcp_server.server.calendar import (
    _verify_todo_completed,
    configure_calendar_tools,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def complete_todo_tool():
    mcp = FastMCP("test-todo-completion")
    configure_calendar_tools(mcp)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    return tools["nc_calendar_complete_todo"]


@pytest.fixture
def calendar_client(mocker):
    client = SimpleNamespace(calendar=SimpleNamespace())
    client.calendar.update_todo = AsyncMock(
        return_value={"uid": "task-1", "status_code": 200}
    )
    client.calendar.get_todo = AsyncMock(return_value={"status": "COMPLETED"})
    client.calendar.delete_todo = AsyncMock()
    mocker.patch(
        "nextcloud_mcp_server.server.calendar.get_client",
        new=AsyncMock(return_value=client),
    )
    return client


def _context():
    return SimpleNamespace(request_context=SimpleNamespace(access_token=None))


class TestCompletionIsAnEdit:
    async def test_sets_status_percent_and_timestamp(
        self, complete_todo_tool, calendar_client
    ):
        await complete_todo_tool.fn(
            calendar_name="Tasks", todo_uid="task-1", ctx=_context()
        )

        calendar_client.calendar.update_todo.assert_awaited_once()
        args = calendar_client.calendar.update_todo.await_args.args
        assert args[0] == "Tasks"
        # Addressed by UID, never by summary.
        assert args[1] == "task-1"
        payload = args[2]
        assert payload["status"] == "COMPLETED"
        assert payload["percent_complete"] == 100
        assert payload["completed"]

    async def test_never_deletes_the_task(self, complete_todo_tool, calendar_client):
        """A completed task stays on the server; deleting it would destroy the
        record instead of closing it."""
        await complete_todo_tool.fn(
            calendar_name="Tasks", todo_uid="task-1", ctx=_context()
        )
        calendar_client.calendar.delete_todo.assert_not_awaited()

    async def test_supplied_timestamp_is_passed_through(
        self, complete_todo_tool, calendar_client
    ):
        await complete_todo_tool.fn(
            calendar_name="Tasks",
            todo_uid="task-1",
            ctx=_context(),
            completed_at="2026-02-03T04:05:06+00:00",
        )
        payload = calendar_client.calendar.update_todo.await_args.args[2]
        assert payload["completed"] == "2026-02-03T04:05:06+00:00"


class TestReadBackVerification:
    async def test_confirmed_completion_reports_verified(
        self, complete_todo_tool, calendar_client
    ):
        result = await complete_todo_tool.fn(
            calendar_name="Tasks", todo_uid="task-1", ctx=_context()
        )

        calendar_client.calendar.get_todo.assert_awaited_once_with("Tasks", "task-1")
        assert result["verified"] is True
        assert "verification_error" not in result

    async def test_server_that_did_not_store_completion_is_reported(self):
        client = SimpleNamespace(calendar=SimpleNamespace())
        client.calendar.get_todo = AsyncMock(return_value={"status": "NEEDS-ACTION"})

        result = await _verify_todo_completed(
            client, "Tasks", "task-1", {"uid": "task-1"}
        )

        assert result["verified"] is False
        assert "NEEDS-ACTION" in result["verification_error"]

    async def test_missing_task_on_read_back_is_reported(self):
        client = SimpleNamespace(calendar=SimpleNamespace())
        client.calendar.get_todo = AsyncMock(return_value=None)

        result = await _verify_todo_completed(
            client, "Tasks", "task-1", {"uid": "task-1"}
        )

        assert result["verified"] is False
        assert "expected 'COMPLETED'" in result["verification_error"]

    async def test_read_back_failure_does_not_mask_the_write(self):
        """The write happened. A verification that cannot run is annotated, not
        raised — reporting a failed write would be the worse lie."""
        client = SimpleNamespace(calendar=SimpleNamespace())
        client.calendar.get_todo = AsyncMock(side_effect=RuntimeError("network down"))

        result = await _verify_todo_completed(
            client, "Tasks", "task-1", {"uid": "task-1", "status_code": 200}
        )

        assert result["status_code"] == 200
        assert result["verified"] is False
        assert "network down" in result["verification_error"]
