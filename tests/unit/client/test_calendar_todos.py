"""Unit tests for VTODO completion semantics and the completed-task filter.

Two properties are pinned here:

* a task is *finished* when RFC 5545 says so — ``STATUS:COMPLETED`` or a
  ``COMPLETED`` timestamp — not when a client happens to set one of the two;
* ``include_completed=False`` drops finished tasks, and its absence changes
  nothing, so the historical listing behaviour is preserved.
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

from typing import Any

import pytest

from nextcloud_mcp_server.client.calendar import CalendarClient

pytestmark = pytest.mark.unit


@pytest.fixture
def client() -> Any:
    """A CalendarClient with no DAV session — these methods are pure."""
    return CalendarClient.__new__(CalendarClient)


class TestTodoIsCompleted:
    @pytest.mark.parametrize(
        "todo",
        [
            {"status": "COMPLETED"},
            {"status": "completed"},
            # Clients that write only the timestamp still mean "done".
            {"status": "NEEDS-ACTION", "completed": "2026-01-02T03:04:05+00:00"},
        ],
    )
    def test_finished_tasks_are_recognised(self, client, todo):
        assert client._todo_is_completed(todo) is True

    @pytest.mark.parametrize(
        "todo",
        [
            {},
            {"status": "NEEDS-ACTION"},
            {"status": "IN-PROCESS", "percent_complete": 90},
            # CANCELLED is abandoned, not completed — a distinct state.
            {"status": "CANCELLED"},
            {"completed": ""},
        ],
    )
    def test_unfinished_tasks_are_not(self, client, todo):
        assert client._todo_is_completed(todo) is False


class TestIncludeCompletedFilter:
    def test_completed_task_is_dropped_when_excluded(self, client):
        todo = {"summary": "ship it", "status": "COMPLETED"}
        assert client._todo_matches_filters(todo, {"include_completed": False}) is False

    def test_open_task_survives_when_completed_excluded(self, client):
        todo = {"summary": "ship it", "status": "NEEDS-ACTION"}
        assert client._todo_matches_filters(todo, {"include_completed": False}) is True

    def test_absent_flag_preserves_historical_behaviour(self, client):
        """No flag means no change: completed tasks still list."""
        todo = {"summary": "ship it", "status": "COMPLETED"}
        assert client._todo_matches_filters(todo, {}) is True

    def test_explicit_true_keeps_completed_tasks(self, client):
        todo = {"summary": "ship it", "status": "COMPLETED"}
        assert client._todo_matches_filters(todo, {"include_completed": True}) is True

    def test_explicit_status_filter_still_wins(self, client):
        """Asking for COMPLETED explicitly must not be silently overruled."""
        todo = {"summary": "ship it", "status": "COMPLETED"}
        assert client._todo_matches_filters(todo, {"status": "COMPLETED"}) is True

    def test_combines_with_other_filters(self, client):
        todo = {"summary": "write the report", "status": "NEEDS-ACTION"}
        assert (
            client._todo_matches_filters(
                todo, {"include_completed": False, "summary_contains": "report"}
            )
            is True
        )
        assert (
            client._todo_matches_filters(
                todo, {"include_completed": False, "summary_contains": "invoice"}
            )
            is False
        )
