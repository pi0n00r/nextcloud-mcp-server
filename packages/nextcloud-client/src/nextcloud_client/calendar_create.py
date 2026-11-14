"""One-shot, create-only VTODO writes reconciled by authoritative readback."""

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

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from caldav.aio import AsyncCalendar
from caldav.lib import error as caldav_error

TodoReader = Callable[[str, str], Awaitable[dict[str, Any]]]


def normalize_creation_uid(value: str) -> str:
    """Validate before any DAV access; never replace an invalid supplied UID."""
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("creation_uid must be a UUID") from exc


def _is_create_collision(exc: Exception) -> bool:
    """caldav may refuse in its UID search or at the conditional HTTP PUT."""
    if isinstance(exc, (caldav_error.ConsistencyError, caldav_error.UidConflictError)):
        return True
    response = getattr(exc, "response", None)
    return any(
        getattr(source, attribute, None) == 412
        for source in (exc, response)
        for attribute in ("status", "status_code")
    )


def _unresolved(todo_uid: str, reason: str) -> dict[str, Any]:
    """Keep the identity available for reconciliation without another create."""
    return {
        "ok": False,
        "uid": todo_uid,
        "href": "",
        "etag": "",
        "status_code": 503,
        "outcome": "unresolved",
        "repairable": True,
        "proposed_content_applied": False,
        "todo": None,
        "error": reason,
    }


async def save_todo_create_only(
    calendar: AsyncCalendar,
    read_todo: TodoReader,
    calendar_name: str,
    todo_uid: str,
    ical_content: str,
    proposed_todo: dict[str, Any],
) -> dict[str, Any]:
    """Attempt one guarded write, then report the saved object, never the proposal.

    Nextcloud's caldav no_overwrite path sends If-None-Match: *. A collision
    must not update content. A lost write acknowledgement is reconciled only
    by the same UID's REPORT-coupled content/ETag, without another write.
    Cancellation propagates; the caller still owns the supplied identity.
    """
    outcome = "created"
    try:
        await calendar.save_todo(ical=ical_content, no_overwrite=True)
    except Exception as exc:
        outcome = "reused" if _is_create_collision(exc) else "recovered"

    try:
        actual = await read_todo(calendar_name, todo_uid)
    except Exception:
        return _unresolved(todo_uid, "todo_readback_unavailable")

    if not isinstance(actual, dict) or actual.get("uid") != todo_uid:
        return _unresolved(todo_uid, "todo_readback_identity_mismatch")
    if not isinstance(actual.get("etag"), str) or not actual["etag"]:
        return _unresolved(todo_uid, "todo_readback_etag_unavailable")
    if not isinstance(actual.get("href"), str) or not actual["href"]:
        return _unresolved(todo_uid, "todo_readback_href_unavailable")

    matches = all(actual.get(key) == value for key, value in proposed_todo.items())
    if outcome == "created" and not matches:
        outcome = "recovered"
    return {
        "ok": True,
        "uid": todo_uid,
        "href": actual["href"],
        "etag": actual["etag"],
        "status_code": 201 if outcome == "created" else 200,
        "outcome": outcome,
        "proposed_content_applied": outcome == "created",
        "todo": actual,
    }
