"""Regression coverage for compact, interoperable VTODO time serialization."""

from __future__ import annotations

import datetime as dt
import time
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from icalendar import Alarm, Calendar, Event, Timezone, Todo

from nextcloud_mcp_server.client.calendar import CalendarClient


@pytest.fixture
def client() -> CalendarClient:
    instance = CalendarClient.__new__(CalendarClient)
    instance._client = MagicMock()
    instance._principal = MagicMock()
    instance._calendars_cache = {}
    return instance


def _task_resource(*, recurring: bool = False, duplicate_zone: bool = False) -> str:
    zone = ZoneInfo("America/Toronto")
    calendar = Calendar()
    calendar.add("prodid", "-//Bridgette timezone regression//EN")
    calendar.add("version", "2.0")
    calendar.add_component(Timezone.from_tzinfo(zone))
    if duplicate_zone:
        calendar.add_component(Timezone.from_tzinfo(zone))

    todo = Todo()
    todo.add("uid", "timezone-regression")
    todo.add("summary", "Timezone regression")
    todo.add("status", "COMPLETED")
    todo.add("dtstart", dt.datetime(2026, 10, 29, 9, tzinfo=zone))
    todo.add("due", dt.datetime(2026, 10, 29, 10, tzinfo=zone))
    todo.add("completed", dt.datetime(2026, 10, 29, 10, 5, tzinfo=zone))
    if recurring:
        todo.add("rrule", {"freq": "DAILY"})

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", "Reminder")
    alarm.add("trigger", dt.datetime(2026, 10, 29, 8, 45, tzinfo=zone))
    todo.add_component(alarm)
    calendar.add_component(todo)
    return calendar.to_ical().decode()


def test_nonrecurring_merge_normalizes_all_instants_and_prunes_zone(client):
    original = _task_resource()
    updated = client._merge_ical_todo_properties(original, {"summary": "Updated"})

    assert "DTSTART:20261029T130000Z" in updated
    assert "DUE:20261029T140000Z" in updated
    assert "COMPLETED:20261029T140500Z" in updated
    assert "TRIGGER:20261029T124500Z" in updated
    assert "VTIMEZONE" not in updated
    assert len(updated) < len(original) / 2


def test_recurring_merge_preserves_wall_clock_zone_and_deduplicates_it(client):
    updated = client._merge_ical_todo_properties(
        _task_resource(recurring=True, duplicate_zone=True),
        {"summary": "Updated series"},
    )

    assert "DTSTART;TZID=America/Toronto:20261029T090000" in updated
    assert "DUE;TZID=America/Toronto:20261029T100000" in updated
    assert "COMPLETED:20261029T140500Z" in updated
    assert "TRIGGER:20261029T124500Z" in updated
    assert updated.count("BEGIN:VTIMEZONE") == 1


def test_zone_referenced_by_another_component_is_not_pruned(client):
    calendar = Calendar.from_ical(_task_resource())
    zone = ZoneInfo("America/Toronto")
    event = Event()
    event.add("uid", "separate-event")
    event.add("summary", "Keep my timezone")
    event.add("dtstart", dt.datetime(2026, 10, 30, 9, tzinfo=zone))
    calendar.add_component(event)

    updated = client._merge_ical_todo_properties(
        calendar.to_ical().decode(), {"summary": "Updated task"}
    )

    assert "DTSTART:20261029T130000Z" in updated
    assert "DTSTART;TZID=America/Toronto:20261030T090000" in updated
    assert updated.count("BEGIN:VTIMEZONE") == 1


@pytest.mark.parametrize(
    ("update", "expected_status"),
    [
        ({"completed": ""}, "COMPLETED"),
        ({"status": "NEEDS-ACTION"}, "NEEDS-ACTION"),
    ],
)
def test_reopen_or_explicit_clear_removes_completed(client, update, expected_status):
    updated = client._merge_ical_todo_properties(_task_resource(), update)

    assert "COMPLETED:" not in updated
    assert f"STATUS:{expected_status}" in updated


def test_utc_rewrite_keeps_date_only_values_unchanged(client):
    raw = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\n"
        "UID:date-only\r\nSUMMARY:Whole day\r\nSTATUS:NEEDS-ACTION\r\n"
        "DTSTART;VALUE=DATE:20261029\r\nDUE;VALUE=DATE:20261030\r\n"
        "END:VTODO\r\nEND:VCALENDAR\r\n"
    )

    updated = client._merge_ical_todo_properties(raw, {"summary": "Still whole day"})

    assert "DTSTART;VALUE=DATE:20261029" in updated
    assert "DUE;VALUE=DATE:20261030" in updated


def test_bulk_cleanup_is_bounded(client):
    raw = _task_resource()
    started = time.perf_counter()
    outputs = [
        client._merge_ical_todo_properties(raw, {"summary": f"Task {index}"})
        for index in range(105)
    ]
    elapsed = time.perf_counter() - started

    assert elapsed < 5
    assert all("VTIMEZONE" not in output for output in outputs)
