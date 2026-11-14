"""Create-or-reuse tests using caldav's real guarded PUT path, without a server."""

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

import anyio
import pytest
from caldav.aio import AsyncCalendar
from caldav.elements import dav
from caldav.lib import error as caldav_error
from nextcloud_client.calendar import CalendarClient as PackageCalendarClient

from nextcloud_mcp_server.client.calendar import (
    CalendarClient as RetainedCalendarClient,
)

pytestmark = pytest.mark.unit

UID = "ce78a5b3-394b-42a1-93e6-96850d223f7a"
DATA = {
    "summary": "Requested work",
    "description": "Observed source and recommendation.",
    "status": "IN-PROCESS",
    "priority": 3,
    "due": "2026-11-15",
    "dtstart": "2026-11-14",
    "categories": "work,follow-up",
    "reminders": [{"trigger": "-PT6H", "description": "Reminder"}],
}


class GuardedStore:
    """Only the network is simulated; caldav builds its actual PUT headers."""

    def __init__(self, client):
        self.objects = {}
        self.puts = []
        self.lose_ack = False
        self.read_error = None
        self.before_read = None
        self.lock = anyio.Lock()
        calendar = AsyncCalendar(
            client=client._dav_client,
            url="https://cloud.example.org/remote.php/dav/calendars/alice/main/",
        )
        # A stale UID index is the difficult concurrent case. The PUT itself
        # must reject another writer; a preceding successful search is not enough.
        calendar.get_todo_by_uid = AsyncMock(
            side_effect=caldav_error.NotFoundError("No indexed Task")
        )
        client._dav_client.put = self.put
        client._get_calendar = lambda _name: calendar
        client._ensure_calendar_home = AsyncMock()
        client._search_calendar_objects = AsyncMock(side_effect=self.report)

    async def put(self, url, data, headers):
        self.puts.append((url, data, dict(headers)))
        await anyio.sleep(0)
        async with self.lock:
            if headers.get("if-none-match") == "*" and url in self.objects:
                return SimpleNamespace(
                    status=412,
                    reason="Precondition Failed",
                    raw="",
                    _raw=b"",
                    headers={},
                )
            self.objects[url] = SimpleNamespace(
                url=url,
                data=data,
                props={dav.GetEtag.tag: '"stored"'},
            )
        if self.lose_ack:
            raise TimeoutError("Write acknowledgement lost")
        return SimpleNamespace(status=201, headers={"Etag": '"stored"'})

    async def report(self, _calendar, component_type):
        assert component_type == "VTODO"
        if self.read_error is not None:
            raise self.read_error
        if self.before_read is not None:
            self.before_read()
        return list(self.objects.values())


@pytest.fixture(params=[PackageCalendarClient, RetainedCalendarClient])
def setup_client(request):
    client = request.param("https://cloud.example.org", "alice")
    store = GuardedStore(client)
    return client, store


async def test_created_readback_preserves_content_dates_and_reminders(setup_client):
    client, store = setup_client
    result = await client.create_todo("main", DATA, creation_uid=UID)

    assert result["outcome"] == "created"
    assert result["proposed_content_applied"] is True
    assert result["uid"] == result["todo"]["uid"] == UID
    assert result["etag"] == result["todo"]["etag"] == '"stored"'
    for key in ("summary", "description", "status", "priority", "due", "dtstart"):
        assert result["todo"][key] == DATA[key]
    assert result["todo"]["categories"] == "work, follow-up"
    assert result["todo"]["reminders"] == [
        {"action": "DISPLAY", "description": "Reminder", "minutes_before": 360}
    ]
    assert len(store.puts) == 1
    assert store.puts[0][2]["if-none-match"] == "*"


async def test_same_uid_changed_proposal_never_overwrites_existing(setup_client):
    client, store = setup_client
    first = await client.create_todo("main", DATA, creation_uid=UID)
    original_bytes = next(iter(store.objects.values())).data
    result = await client.create_todo(
        "main",
        dict(DATA, summary="Changed", description="Do not replace"),
        creation_uid=UID,
    )

    assert result["outcome"] == "reused"
    assert result["proposed_content_applied"] is False
    assert result["todo"] == first["todo"]
    assert next(iter(store.objects.values())).data == original_bytes
    assert len(store.objects) == 1
    assert all(headers["if-none-match"] == "*" for _, _, headers in store.puts)


async def test_parallel_creates_keep_one_task_and_return_actual_winner(setup_client):
    client, store = setup_client
    results = []

    async def create(summary):
        results.append(
            await client.create_todo(
                "main",
                dict(DATA, summary=summary),
                creation_uid=UID,
            )
        )

    async with anyio.create_task_group() as group:
        group.start_soon(create, "First proposal")
        group.start_soon(create, "Second proposal")

    assert len(store.objects) == 1
    assert sorted(result["outcome"] for result in results) == ["created", "reused"]
    assert results[0]["todo"] == results[1]["todo"]
    assert len(store.puts) == 2


async def test_lost_ack_reads_same_task_without_write_retry(setup_client):
    client, store = setup_client
    store.lose_ack = True
    result = await client.create_todo("main", DATA, creation_uid=UID)

    assert result["outcome"] == "recovered"
    assert result["proposed_content_applied"] is False
    assert result["todo"]["summary"] == DATA["summary"]
    assert result["todo"]["uid"] == UID
    assert len(store.puts) == len(store.objects) == 1


@pytest.mark.parametrize("lose_ack", [False, True])
async def test_unavailable_readback_preserves_repairable_identity(
    setup_client, lose_ack
):
    client, store = setup_client
    store.lose_ack = lose_ack
    store.read_error = TimeoutError("Read unavailable")
    result = await client.create_todo("main", DATA, creation_uid=UID)

    assert result["ok"] is False
    assert result["outcome"] == "unresolved"
    assert result["uid"] == UID
    assert result["todo"] is None
    assert result["repairable"] is True
    assert result["proposed_content_applied"] is False
    assert len(store.puts) == len(store.objects) == 1


async def test_missing_etag_does_not_claim_verified_creation(setup_client):
    client, store = setup_client
    store.before_read = lambda: next(iter(store.objects.values())).props.clear()
    result = await client.create_todo("main", DATA, creation_uid=UID)

    assert result["ok"] is False
    assert result["outcome"] == "unresolved"
    assert result["uid"] == UID
    assert result["todo"] is None
    assert len(store.puts) == 1


async def test_changed_saved_fields_are_returned_without_proposal_success(setup_client):
    client, store = setup_client

    def other_writer():
        item = next(iter(store.objects.values()))
        item.data = item.data.replace(
            "SUMMARY:Requested work", "SUMMARY:Updated elsewhere"
        )

    store.before_read = other_writer
    result = await client.create_todo("main", DATA, creation_uid=UID)

    assert result["outcome"] == "recovered"
    assert result["proposed_content_applied"] is False
    assert result["todo"]["summary"] == "Updated elsewhere"
    assert len(store.puts) == 1


async def test_invalid_supplied_uid_fails_before_dav_access(setup_client):
    client, store = setup_client
    with pytest.raises(ValueError, match="creation_uid must be a UUID"):
        await client.create_todo("main", DATA, creation_uid="not-a-uuid")

    client._ensure_calendar_home.assert_not_awaited()
    assert not store.puts


async def test_omitted_uid_retains_ordinary_create_result_and_no_readback(setup_client):
    client, store = setup_client
    todo = SimpleNamespace(url="/dav/generated.ics")
    calendar = SimpleNamespace(save_todo=AsyncMock(return_value=todo))
    client._get_calendar = lambda _name: calendar
    client._etag_after_create = AsyncMock(return_value='"ordinary"')
    client.get_todo = AsyncMock()

    result = await client.create_todo("main", {"summary": "Ordinary"})

    assert set(result) == {"uid", "href", "etag", "status_code"}
    assert result["status_code"] == 201
    assert result["etag"] == '"ordinary"'
    assert set(calendar.save_todo.call_args.kwargs) == {"ical"}
    client.get_todo.assert_not_awaited()
