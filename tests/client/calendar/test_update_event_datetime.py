"""P1.2 — Verify nc_calendar_update_event datetime serialization is RFC 5545-correct
and nc-time-policy-compliant.

Regression context: Productivity Sonnet reported 2026-05-01 that
`nc_calendar_update_event` corrupts dtstart/dtend format before sending to
CalDAV. Forensic root cause (this PR): icalendar's `Component.__setitem__`
does not auto-coerce raw `datetime` to `vDDDTypes`, unlike `Component.add()`.
The merge path used `__setitem__` and produced Python repr leak
(`DTSTART:2026-05-15 10:00:00-04:00` instead of
`DTSTART;TZID=...:20260515T100000`).

Secondary nc-time-policy concern (Documents/Projects/Isla/nc-time-policy.md):
fixed-offset tzinfo from `replace("Z","+00:00") + fromisoformat` does not
preserve TZID=America/Toronto for wall-clock semantics across DST. Fixed by
new helper `_parse_caldav_datetime` that promotes Toronto-offset datetimes
to ZoneInfo("America/Toronto").

These tests exercise the client-layer serialization helpers directly; no
network or live server required.
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

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from nextcloud_mcp_server.client.calendar import CalendarClient


# Existing iCal fixture that subsequent updates merge against.
EXISTING_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Nextcloud MCP Server//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:test-uid-001\r\n"
    "SUMMARY:Original summary\r\n"
    "DESCRIPTION:Original description\r\n"
    "LOCATION:Original location\r\n"
    "DTSTART;TZID=America/Toronto:20260515T100000\r\n"
    "DTEND;TZID=America/Toronto:20260515T110000\r\n"
    "DTSTAMP:20260101T120000Z\r\n"
    "CREATED:20260101T120000Z\r\n"
    "LAST-MODIFIED:20260101T120000Z\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


@pytest.fixture
def client() -> CalendarClient:
    """Minimal CalendarClient with mocked transport — _merge_ical_properties
    and _parse_caldav_datetime don't touch the network."""
    instance = CalendarClient.__new__(CalendarClient)
    instance._client = MagicMock()
    instance._principal = MagicMock()
    instance._calendars_cache = {}
    return instance


# === T1 — summary-only update preserves DTSTART byte-equal ===

def test_summary_only_update_preserves_dtstart_byte_equal(client):
    """An update that touches only `title` MUST NOT mangle the DTSTART line."""
    updated = client._merge_ical_properties(
        EXISTING_ICAL,
        {"title": "Updated summary"},
        "test-uid-001",
    )
    assert "DTSTART;TZID=America/Toronto:20260515T100000" in updated, (
        "DTSTART line lost or mangled — summary-only update should not touch it. "
        f"Output:\n{updated}"
    )
    # Confirm the Python-repr leak that defined P1.2 is gone
    assert "DTSTART:2026-05-15 10:00:00" not in updated, (
        "Python repr leak in DTSTART (the P1.2 mangle is back). "
        f"Output:\n{updated}"
    )
    # Summary correctly updated
    assert "SUMMARY:Updated summary" in updated


# === T2 — dtstart update with TZID=America/Toronto round-trips correctly ===

def test_dtstart_update_with_toronto_offset_preserves_tzid(client):
    """Updating DTSTART with Toronto-offset input MUST emit TZID=America/Toronto,
    not TZID=\"UTC-04:00\" or a Python repr leak."""
    updated = client._merge_ical_properties(
        EXISTING_ICAL,
        {"start_datetime": "2026-06-15T14:30:00-04:00"},
        "test-uid-001",
    )
    assert "DTSTART;TZID=America/Toronto:20260615T143000" in updated, (
        "DTSTART did not carry TZID=America/Toronto after Toronto-offset update. "
        "Per nc-time-policy, wall-clock semantic must be preserved via IANA TZID. "
        f"Output:\n{updated}"
    )
    # No Python repr leak
    assert "DTSTART:2026-06-15 14:30:00-04:00" not in updated
    # No fixed-offset TZID
    assert 'TZID="UTC-04:00"' not in updated


# === T3 — dtstart update across DST boundary preserves wall-clock semantic ===

def test_dtstart_update_across_dst_boundary_uses_toronto_iana_zone(client):
    """A November update (EST, -05:00) and a May update (EDT, -04:00) both must
    promote to TZID=America/Toronto — the IANA zone handles DST automatically;
    fixed-offset tzinfo doesn't."""
    # EDT (May)
    edt_updated = client._merge_ical_properties(
        EXISTING_ICAL,
        {"start_datetime": "2026-05-15T10:00:00-04:00"},
        "test-uid-001",
    )
    assert "DTSTART;TZID=America/Toronto:20260515T100000" in edt_updated

    # EST (November) — Toronto offset is -05:00 in November
    est_updated = client._merge_ical_properties(
        EXISTING_ICAL,
        {"start_datetime": "2026-11-15T10:00:00-05:00"},
        "test-uid-001",
    )
    assert "DTSTART;TZID=America/Toronto:20261115T100000" in est_updated, (
        "November dtstart with EST offset (-05:00) failed to promote to "
        "TZID=America/Toronto. The helper must check offset-equality on the "
        "TARGET date, not the parser's call-time date. "
        f"Output:\n{est_updated}"
    )


# === T4 — utc input produces Z-suffixed audit timestamp ===

def test_utc_dtstart_update_serializes_with_z(client):
    """Audit-semantic input (UTC `Z`) MUST serialize as `Z`-suffix per nc-time-policy."""
    updated = client._merge_ical_properties(
        EXISTING_ICAL,
        {"start_datetime": "2026-05-15T14:00:00Z"},
        "test-uid-001",
    )
    assert "DTSTART:20260515T140000Z" in updated, (
        "UTC dtstart input did not serialize as Z-suffix. "
        f"Output:\n{updated}"
    )


# === T5 — naive input is forbidden per nc-time-policy ===

def test_naive_datetime_input_raises(client):
    """Per nc-time-policy 'never naive', a datetime string without offset/Z must raise."""
    with pytest.raises(ValueError, match="missing timezone"):
        client._parse_caldav_datetime("2026-05-15T10:00:00")


# === T6 — all-day input returns a date object ===

def test_all_day_input_returns_date(client):
    """all_day=True should strip time and return a date (no time component)."""
    result = client._parse_caldav_datetime("2026-05-15", all_day=True)
    assert isinstance(result, dt.date)
    assert not isinstance(result, dt.datetime), (
        f"all_day input should return date, not datetime: {result!r}"
    )
    assert result == dt.date(2026, 5, 15)


# === T7 — todo update_todo `due` preserves TZID (todo path was already vDDDTypes-wrapped;
# this test verifies the TZID promotion via the new helper) ===

EXISTING_TODO_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Nextcloud MCP Server//EN\r\n"
    "BEGIN:VTODO\r\n"
    "UID:test-todo-001\r\n"
    "SUMMARY:Original todo\r\n"
    "STATUS:NEEDS-ACTION\r\n"
    "PRIORITY:5\r\n"
    "DUE;TZID=America/Toronto:20260515T120000\r\n"
    "DTSTAMP:20260101T120000Z\r\n"
    "END:VTODO\r\n"
    "END:VCALENDAR\r\n"
)


def test_todo_due_update_preserves_tzid(client):
    """Update a todo's due datetime; verify TZID=America/Toronto preserved."""
    updated = client._merge_ical_todo_properties(
        EXISTING_TODO_ICAL,
        {"due": "2026-06-15T14:30:00-04:00"},
        "test-todo-001",
    )
    assert "DUE;TZID=America/Toronto:20260615T143000" in updated, (
        f"todo DUE update did not carry TZID=America/Toronto. Output:\n{updated}"
    )
    # No Python repr leak (defensive — todo path was already wrapping with vDDDTypes
    # but the new helper adds TZID promotion)
    assert "DUE:2026-06-15 14:30:00-04:00" not in updated


# === T8 — non-Toronto offset is preserved as fixed offset (don't impose Toronto) ===

def test_non_toronto_offset_preserved_as_fixed_offset(client):
    """A datetime with a non-Toronto offset (e.g., +05:30 IST) should NOT be
    silently relocated to Toronto. icalendar emits TZID="UTC+05:30" in that
    case — RFC 5545 valid, non-IANA, but the user's offset is preserved."""
    updated = client._merge_ical_properties(
        EXISTING_ICAL,
        {"start_datetime": "2026-05-15T19:30:00+05:30"},  # IST, NOT Toronto
        "test-uid-001",
    )
    # Should NOT promote to TZID=America/Toronto (offset doesn't match)
    assert "TZID=America/Toronto" not in updated.split("DTSTART")[1].split("\n")[0], (
        "Non-Toronto offset was wrongly promoted to TZID=America/Toronto. "
        "The helper must only promote when the offset matches Toronto's offset "
        "on the target date."
    )
