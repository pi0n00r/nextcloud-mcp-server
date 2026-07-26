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

"""Focused CalDAV ETag and optimistic-concurrency regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from caldav.elements import cdav, dav

from nextcloud_mcp_server.client.calendar import (
    CalendarClient,
    CalendarEtagConflictError,
    CalendarEtagUnavailableError,
)

pytestmark = pytest.mark.unit

EVENT_ICAL = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-1
SUMMARY:Original
DTSTART;VALUE=DATE:20260726
DTEND;VALUE=DATE:20260727
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:Reminder
END:VALARM
END:VEVENT
END:VCALENDAR
"""

TODO_ICAL = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:todo-1
SUMMARY:Original
DUE:20260726T170000Z
END:VTODO
END:VCALENDAR
"""


class DavObject:
    def __init__(self, uid: str, data: str, etag: str | None):
        self.id = uid
        self.data = data
        self.url = (
            f"https://cloud.example.org/remote.php/dav/calendars/alice/main/{uid}.ics"
        )
        self.props = {dav.GetEtag.tag: etag} if etag else {}
        self.get_property = AsyncMock(return_value=etag)
        self.load = AsyncMock(return_value=self)

    @property
    def etag(self):
        return self.props.get(dav.GetEtag.tag)


class CalendarUrl:
    def __str__(self):
        return "https://cloud.example.org/remote.php/dav/calendars/alice/main/"

    def join(self, href):
        return f"https://cloud.example.org{href}"


@pytest.fixture
def client(mocker):
    mocker.patch("nextcloud_mcp_server.client.calendar.AsyncDAVClient")
    result = CalendarClient("https://cloud.example.org", "alice")
    result._ensure_calendar_home = AsyncMock()
    result._dav_client = SimpleNamespace(put=AsyncMock())
    return result


def response(status=204, etag='"new"'):
    return SimpleNamespace(status=status, headers={"etag": etag} if etag else {})


async def test_uid_lookup_requests_getetag_in_search_report(client):
    event = DavObject("event-1", EVENT_ICAL, '"report-v1"')
    calendar = SimpleNamespace(search=AsyncMock(return_value=[event]))

    found = await client._async_object_by_uid(
        calendar, "event-1", cdav.CompFilter("VEVENT")
    )

    assert found is event
    assert calendar.search.await_args.kwargs["props"][0].tag == dav.GetEtag.tag


async def test_event_read_and_list_surface_exact_weak_quoted_etag(client):
    event = DavObject("event-1", EVENT_ICAL, 'W/"event-v1"')
    calendar = SimpleNamespace(events=AsyncMock(return_value=[event]))
    client._get_calendar = lambda _name: calendar
    client._async_object_by_uid = AsyncMock(return_value=event)

    listed = await client.get_calendar_events("main")
    fetched, etag = await client.get_event("main", "event-1")

    assert listed[0]["etag"] == 'W/"event-v1"'
    assert fetched["etag"] == 'W/"event-v1"'
    assert etag == 'W/"event-v1"'


async def test_todo_list_surfaces_exact_quoted_etag(client):
    todo = DavObject("todo-1", TODO_ICAL, '"todo-v1"')
    client._get_calendar = lambda _name: SimpleNamespace(
        todos=AsyncMock(return_value=[todo])
    )

    listed = await client.list_todos("main")

    assert listed[0]["etag"] == '"todo-v1"'


@pytest.mark.parametrize(
    ("kind", "uid", "ical", "merge_name"),
    [
        ("event", "event-1", EVENT_ICAL, "_merge_ical_properties"),
        ("todo", "todo-1", TODO_ICAL, "_merge_ical_todo_properties"),
    ],
)
async def test_update_emits_exact_if_match_and_preserves_calendar_data(
    client, mocker, kind, uid, ical, merge_name
):
    obj = DavObject(uid, ical, '"opaque-v1"')
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=obj)
    mocker.patch.object(client, merge_name, return_value=ical)
    client._dav_client.put.return_value = response(etag='"opaque-v2"')

    update = getattr(client, f"update_{kind}")
    result = await update("main", uid, {"summary": "Changed"})

    headers = client._dav_client.put.await_args.kwargs["headers"]
    assert headers["If-Match"] == '"opaque-v1"'
    assert client._dav_client.put.await_args.args[1] == ical
    assert "VALUE=DATE" in ical if kind == "event" else "DUE:" in ical
    assert result["etag"] == '"opaque-v2"'
    obj.get_property.assert_not_awaited()


async def test_concurrent_change_is_rejected_using_report_coupled_etag(client, mocker):
    event = DavObject("event-1", EVENT_ICAL, '"report-v1"')
    event.get_property = AsyncMock(return_value='"concurrent-v2"')
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=event)
    mocker.patch.object(client, "_merge_ical_properties", return_value=EVENT_ICAL)
    client._dav_client.put.return_value = response(412, '"concurrent-v2"')

    with pytest.raises(CalendarEtagConflictError) as caught:
        await client.update_event("main", "event-1", {}, '"report-v1"')

    assert client._dav_client.put.await_args.kwargs["headers"]["If-Match"] == (
        '"report-v1"'
    )
    event.get_property.assert_not_awaited()
    assert caught.value.current_etag == '"concurrent-v2"'


@pytest.mark.parametrize(
    ("kind", "uid", "ical", "merge_name"),
    [
        ("event", "event-1", EVENT_ICAL, "_merge_ical_properties"),
        ("todo", "todo-1", TODO_ICAL, "_merge_ical_todo_properties"),
    ],
)
async def test_weak_etag_fails_closed_before_merge_or_put(
    client, mocker, kind, uid, ical, merge_name
):
    obj = DavObject(uid, ical, 'W/"opaque-v1"')
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=obj)
    merge = mocker.patch.object(client, merge_name)

    update = getattr(client, f"update_{kind}")
    with pytest.raises(CalendarEtagUnavailableError, match="weak ETag"):
        await update("main", uid, {}, 'W/"opaque-v1"')

    merge.assert_not_called()
    client._dav_client.put.assert_not_awaited()


async def test_stale_caller_etag_fails_before_mutation(client, mocker):
    event = DavObject("event-1", EVENT_ICAL, '"current"')
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=event)
    merge = mocker.patch.object(client, "_merge_ical_properties")

    with pytest.raises(CalendarEtagConflictError) as caught:
        await client.update_event("main", "event-1", {}, '"stale"')

    assert caught.value.current_etag == '"current"'
    assert caught.value.as_dict()["error"] == "etag_conflict"
    merge.assert_not_called()
    client._dav_client.put.assert_not_awaited()


async def test_server_412_maps_to_conflict_with_current_etag(client, mocker):
    todo = DavObject("todo-1", TODO_ICAL, '"todo-v1"')
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=todo)
    mocker.patch.object(client, "_merge_ical_todo_properties", return_value=TODO_ICAL)
    client._dav_client.put.return_value = response(412, '"todo-v2"')

    with pytest.raises(CalendarEtagConflictError) as caught:
        await client.update_todo("main", "todo-1", {}, '"todo-v1"')

    assert caught.value.as_dict() == {
        "error": "etag_conflict",
        "status_code": 412,
        "message": "Conditional update of todo todo-1 was rejected",
        "current_etag": '"todo-v2"',
    }
    assert client._dav_client.put.await_count == 1


async def test_missing_server_etag_fails_closed(client, mocker):
    event = DavObject("event-1", EVENT_ICAL, None)
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=event)
    mocker.patch.object(client, "_merge_ical_properties", return_value=EVENT_ICAL)

    with pytest.raises(CalendarEtagUnavailableError) as caught:
        await client.update_event("main", "event-1", {})

    assert caught.value.as_dict()["error"] == "etag_unavailable"
    client._dav_client.put.assert_not_awaited()


async def test_date_range_report_requests_and_retains_exact_etag(client, mocker):
    report_response = SimpleNamespace(
        expand_simple_props=lambda requested: {
            "/remote.php/dav/calendars/alice/main/event-1.ics": {
                dav.GetEtag.tag: 'W/"report-v1"',
                cdav.CalendarData.tag: EVENT_ICAL,
            }
        }
    )
    report = AsyncMock(return_value=report_response)
    calendar = SimpleNamespace(
        client=SimpleNamespace(report=report),
        url=CalendarUrl(),
    )
    event_class = mocker.patch("nextcloud_mcp_server.client.calendar.AsyncEvent")

    objects = await client._search_events_by_date(calendar)

    report_body = report.await_args.args[1]
    assert b"getetag" in report_body
    event_class.assert_called_once()
    assert event_class.call_args.kwargs["props"] == {dav.GetEtag.tag: 'W/"report-v1"'}
    assert objects == [event_class.return_value]


async def test_successful_put_without_etag_header_refetches_new_etag(client, mocker):
    event = DavObject("event-1", EVENT_ICAL, '"event-v1"')
    event.get_property = AsyncMock(return_value='"event-v2"')
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=event)
    mocker.patch.object(client, "_merge_ical_properties", return_value=EVENT_ICAL)
    client._dav_client.put.return_value = response(etag=None)

    result = await client.update_event("main", "event-1", {}, '"event-v1"')

    assert result["etag"] == '"event-v2"'
    event.get_property.assert_awaited_once()


async def test_cross_origin_object_url_is_rejected_before_put(client, mocker):
    event = DavObject("event-1", EVENT_ICAL, '"event-v1"')
    event.url = "https://untrusted.example.net/event-1.ics"
    client._get_calendar = lambda _name: SimpleNamespace()
    client._async_object_by_uid = AsyncMock(return_value=event)
    mocker.patch.object(client, "_merge_ical_properties", return_value=EVENT_ICAL)

    with pytest.raises(ValueError, match="configured origin"):
        await client.update_event("main", "event-1", {})

    client._dav_client.put.assert_not_awaited()
