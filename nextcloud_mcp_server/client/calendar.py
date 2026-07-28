"""CalDAV client for Nextcloud calendar and task operations using caldav library."""

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

import datetime as dt
import inspect
import logging
import re
import uuid
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
import httpx
import recurring_ical_events
from caldav.aio import AsyncCalendar, AsyncDAVClient, AsyncEvent, AsyncTodo
from caldav.elements import cdav, dav
from caldav.lib import error as caldav_error
from icalendar import Alarm, Calendar, Timezone, vDDDTypes, vRecur
from icalendar import Event as ICalEvent
from icalendar import Todo as ICalTodo
from lxml import etree  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]

from ..config import get_nextcloud_ssl_verify
from .entity_tag import StrongEntityTagError, require_strong_entity_tag

logger = logging.getLogger(__name__)


class CalendarEtagConflictError(Exception):
    """Raised when a CalDAV object changed before a conditional update."""

    status_code = 412
    error = "etag_conflict"

    def __init__(self, message: str, current_etag: str | None = None):
        super().__init__(message)
        self.current_etag = current_etag

    def as_dict(self) -> dict[str, Any]:
        """Return the stable structured error payload exposed to callers."""
        return {
            "error": self.error,
            "status_code": self.status_code,
            "message": str(self),
            "current_etag": self.current_etag,
        }


class CalendarEtagUnavailableError(Exception):
    """Raised when an existing CalDAV object has no usable server ETag."""

    status_code = 409
    error = "etag_unavailable"

    def as_dict(self) -> dict[str, Any]:
        """Return the stable structured error payload exposed to callers."""
        return {
            "error": self.error,
            "status_code": self.status_code,
            "message": str(self),
            "current_etag": None,
        }


async def _maybe_await(result: Any) -> Any:
    """Await a result if it's a coroutine, otherwise return it directly.

    caldav v3 uses dual-mode methods that return coroutines for async clients
    but plain objects when the result is already available (e.g. load() on
    already-loaded objects).
    """
    if inspect.isawaitable(result):
        return await result
    return result


class CalendarClient:
    """Client for Nextcloud CalDAV calendar and task operations."""

    def __init__(
        self,
        base_url: str,
        username: str,
        *,
        auth_username: str | None = None,
        password: str | None = None,
        token: str | None = None,
    ):
        """Initialize CalendarClient with AsyncDAVClient.

        Pass the raw credential plus an explicit ``auth_type`` so caldav can
        build whichever auth object its active HTTP backend needs. caldav v3
        prefers ``niquests`` over ``httpx`` and won't accept an ``httpx.Auth``
        when ``niquests`` is the active backend (issue #731), so we no longer
        accept a pre-built ``httpx.Auth`` here.

        Args:
            base_url: Nextcloud base URL
            username: Nextcloud username (UID) used as the DAV path fallback
            auth_username: Credential identity (loginName) the app password
                authenticates against; defaults to ``username``. Differs from
                the UID for OIDC-provisioned users.
            password: App password / login password — selects ``auth_type="basic"``
            token: OAuth bearer token — selects ``auth_type="bearer"``

        Pass exactly one of ``password`` or ``token``. Passing neither leaves
        the underlying client unauthenticated.
        """
        self.username = username
        self.base_url = base_url
        # The UID (``username``) is the DAV path fallback until principal
        # discovery succeeds; the loginName (``auth_username``) is the
        # credential the app password authenticates against. They differ for
        # OIDC-provisioned users. Defaults to the UID so existing single-user /
        # OAuth callers are unchanged.
        auth_username = auth_username or username

        auth_kwargs: dict[str, Any] = {}
        if password is not None:
            auth_kwargs = {"password": password, "auth_type": "basic"}
        elif token is not None:
            auth_kwargs = {"password": token, "auth_type": "bearer"}

        # AsyncDAVClient needs the full base URL for proper URL construction.
        #
        # The X-NC-CalDAV-Webcal-Caching header makes Nextcloud expose external
        # subscriptions (webcal/ICS feeds) as regular, queryable calendars
        # (CachedSubscription) instead of opaque cs:subscribed collections, so
        # their events become readable through the normal event/search tools —
        # the same mechanism desktop clients (Evolution/KDE) rely on (issue #830).
        # list_calendars() overrides this header to "Off" on its own PROPFIND so
        # it can still detect subscriptions and flag them read-only.
        self._dav_client = AsyncDAVClient(
            url=f"{base_url}/remote.php/dav/",
            username=auth_username,
            ssl_verify_cert=get_nextcloud_ssl_verify(),  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # caldav types say bool|str but passes through to niquests which accepts SSLContext
            headers={"X-NC-CalDAV-Webcal-Caching": "On"},
            **auth_kwargs,
        )
        self._calendar_home_url = f"{base_url}/remote.php/dav/calendars/{username}/"
        self._principal_resolved = False

    def _calendar_home_url_from_home_set(self, home_set: Any) -> str | None:
        """Normalize a caldav CalendarSet or URL into an absolute home URL."""
        if home_set is None:
            return None

        home_url = getattr(home_set, "url", home_set)
        if home_url is None:
            return None

        home_url = str(home_url)
        if not home_url:
            return None
        if home_url.startswith("/"):
            # calendar-home-set returns an absolute path that already includes
            # any subpath under which Nextcloud is served (e.g.
            # ``/nextcloud/remote.php/dav/calendars/David/``). Resolve it
            # against the *origin* (scheme + host) of ``base_url`` rather than
            # the full ``base_url`` — concatenating onto a subpath base URL
            # would double the subpath and produce a bogus, unroutable URL
            # (issue #1007).
            origin = urlsplit(self.base_url)
            home_url = urlunsplit((origin.scheme, origin.netloc, home_url, "", ""))
        if not home_url.endswith("/"):
            home_url = f"{home_url}/"
        return home_url

    async def _calendar_home_url_from_principal(self, principal: Any) -> str | None:
        """Resolve calendar-home-set without using caldav's async-unsafe property."""
        get_property = getattr(principal, "get_property", None)
        if get_property is not None:
            try:
                home_set = await _maybe_await(get_property(cdav.CalendarHomeSet()))
                calendar_home_url = self._calendar_home_url_from_home_set(home_set)
                if calendar_home_url:
                    return calendar_home_url
            except (caldav_error.DAVError, AttributeError, TypeError, ValueError) as e:
                logger.warning(
                    "CalDAV calendar-home-set discovery failed; deriving from "
                    "principal URL: %s",
                    e,
                )

        try:
            home_set = getattr(principal, "calendar_home_set", None)
            home_set = await _maybe_await(home_set)
            return self._calendar_home_url_from_home_set(home_set)
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning(
                "CalDAV calendar-home-set property unavailable; deriving from "
                "principal URL: %s",
                e,
            )
            return None

    async def _ensure_calendar_home(self) -> None:
        """Discover and cache the authenticated user's CalDAV calendar home."""
        if self._principal_resolved:
            return

        try:
            get_principal = getattr(self._dav_client, "get_principal", None)
            if get_principal is None:
                principal = await _maybe_await(self._dav_client.principal())
            else:
                principal = await _maybe_await(get_principal())

            calendar_home_url = await self._calendar_home_url_from_principal(principal)
            if calendar_home_url:
                self._calendar_home_url = calendar_home_url
                self._principal_resolved = True
                return

            principal_url = getattr(principal, "url", None)
            if principal_url is None:
                raise ValueError("CalDAV principal discovery returned no URL")
            principal_id = unquote(str(principal_url).rstrip("/").split("/")[-1])
            if principal_id:
                self._calendar_home_url = (
                    f"{self.base_url}/remote.php/dav/calendars/{principal_id}/"
                )
                self._principal_resolved = True
        except (caldav_error.DAVError, httpx.HTTPError, ValueError) as e:
            logger.warning(
                "CalDAV principal discovery failed; using username path: %s", e
            )

    def _get_calendar_url(self, calendar_name: str) -> str:
        """Get the full URL for a calendar."""
        return f"{self._calendar_home_url}{calendar_name}/"

    def _get_calendar(self, calendar_name: str) -> AsyncCalendar:
        """Get an AsyncCalendar object for the given calendar name."""
        calendar_url = self._get_calendar_url(calendar_name)
        return AsyncCalendar(
            client=self._dav_client,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # AsyncDAVClient is valid for async mode
            url=calendar_url,
            name=calendar_name,
        )

    def _validate_calendar_object_url(self, object_url: str) -> str:
        """Allow conditional writes only to the configured Nextcloud origin."""
        target = urlsplit(object_url)
        base = urlsplit(self.base_url)

        def effective_port(parts: Any) -> int | None:
            if parts.port is not None:
                return parts.port
            return {"http": 80, "https": 443}.get(parts.scheme.lower())

        if (
            target.scheme.lower(),
            target.hostname,
            effective_port(target),
        ) != (
            base.scheme.lower(),
            base.hostname,
            effective_port(base),
        ):
            raise ValueError("CalDAV object URL is not on the configured origin")
        return object_url

    async def _get_object_etag(self, obj: Any, *, fresh: bool = False) -> str | None:
        """Return an exact server ETag, optionally forcing a PROPFIND."""
        if not fresh:
            etag = getattr(obj, "etag", None)
            if etag:
                return str(etag)
            props = getattr(obj, "props", {})
            etag = props.get(dav.GetEtag.tag)
            if etag:
                return str(etag)

        etag = await _maybe_await(obj.get_property(dav.GetEtag()))
        return str(etag) if etag else None

    async def _require_current_etag(
        self, obj: Any, caller_etag: str, *, kind: str, uid: str
    ) -> str:
        """Validate the ETag coupled to the REPORT calendar data."""
        try:
            caller_etag = require_strong_entity_tag(
                caller_etag, operation=f"update_{kind}"
            )
        except StrongEntityTagError as exc:
            raise CalendarEtagUnavailableError(str(exc)) from exc
        current_etag = getattr(obj, "etag", None)
        if not current_etag:
            current_etag = getattr(obj, "props", {}).get(dav.GetEtag.tag)
        current_etag = str(current_etag) if current_etag else None
        if not current_etag:
            raise CalendarEtagUnavailableError(
                f"Cannot update {kind} {uid}: the server supplied no ETag. "
                "Read the object again and retry only after the server provides "
                "a strong ETag."
            )
        try:
            current_etag = require_strong_entity_tag(
                current_etag, operation=f"update_{kind} server ETag"
            )
        except StrongEntityTagError as exc:
            raise CalendarEtagUnavailableError(
                f"Cannot update {kind} {uid}: {exc}. Read the object again and "
                "retry only after the server provides a strong ETag."
            ) from exc
        if caller_etag != current_etag:
            raise CalendarEtagConflictError(
                f"{kind.capitalize()} {uid} changed since it was read",
                current_etag=current_etag,
            )
        return current_etag

    async def _conditional_update(
        self,
        obj: Any,
        updated_ical: str,
        current_etag: str,
        *,
        kind: str,
        uid: str,
    ) -> str | None:
        """PUT an updated calendar object with its REPORT-coupled If-Match."""
        object_url = self._validate_calendar_object_url(str(obj.url))
        response = await self._dav_client.put(
            object_url,
            updated_ical,
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                "If-Match": current_etag,
            },
        )
        if response.status == 412:
            response_etag = response.headers.get("etag")
            if not response_etag:
                try:
                    response_etag = await self._get_object_etag(obj, fresh=True)
                except Exception:
                    response_etag = None
            raise CalendarEtagConflictError(
                f"Conditional update of {kind} {uid} was rejected",
                current_etag=str(response_etag) if response_etag else None,
            )
        if response.status not in (200, 201, 204):
            raise caldav_error.PutError(
                f"CalDAV PUT for {kind} {uid} returned HTTP {response.status}"
            )

        new_etag = response.headers.get("etag")
        if new_etag:
            obj.props[dav.GetEtag.tag] = new_etag
            return str(new_etag)
        return await self._get_object_etag(obj, fresh=True)

    async def _async_object_by_uid(
        self, calendar: AsyncCalendar, uid: str, comp_filter: Any = None
    ) -> Any:
        """Async version of Calendar.get_object_by_uid.

        Upstream caldav v3's get_object_by_uid is not async-aware: it calls
        search() which returns a coroutine for async clients, then tries to
        iterate the coroutine synchronously. This method properly awaits the
        search result.
        """
        # _hacks="insist" mirrors upstream's Calendar.get_object_by_uid pattern:
        # retries with per-component-type searches if the initial search returns
        # nothing, handling CalDAV servers with incomplete search support.
        items_found = await calendar.search(  # type: ignore[misc]  # ty: ignore[invalid-await]  # dual-mode: returns coroutine for async clients
            uid=uid,
            xml=comp_filter,
            post_filter=True,
            _hacks="insist",
            props=[dav.GetEtag()],  # ty: ignore[invalid-argument-type]  # caldav types props too narrowly
        )
        items_found = [o for o in items_found if o.id == uid]
        if not items_found:
            raise caldav_error.NotFoundError(f"{uid} not found on server")
        return items_found[0]

    async def close(self):
        """Close the DAV client connection."""
        await self._dav_client.close()

    async def _wait_for_calendar_propagation(
        self, calendar_name: str, max_attempts: int = 40, initial_delay_ms: int = 100
    ) -> None:
        """Wait for calendar to propagate through Nextcloud's DAV backend.

        After MKCALENDAR succeeds (201), the calendar may not be immediately queryable
        due to Nextcloud's internal caching/indexing. This polls until it appears.

        Args:
            calendar_name: Name of the calendar to wait for
            max_attempts: Maximum polling attempts (default: 40)
            initial_delay_ms: Initial delay between attempts in ms (default: 100ms)
        """
        logger.info("Waiting for calendar '%s' to propagate...", calendar_name)
        delay_ms = initial_delay_ms

        for attempt in range(max_attempts):
            try:
                logger.debug(
                    "Attempt %s/%s to find calendar '%s'...",
                    attempt + 1,
                    max_attempts,
                    calendar_name,
                )
                calendars = await self.list_calendars()
                if any(cal["name"] == calendar_name for cal in calendars):
                    logger.info(
                        "Calendar '%s' became available after %s attempts",
                        calendar_name,
                        attempt + 1,
                    )
                    return
            except Exception as e:
                logger.warning(
                    "Attempt %s/%s to verify calendar '%s' failed: %s",
                    attempt + 1,
                    max_attempts,
                    calendar_name,
                    e,
                )

            if attempt < max_attempts - 1:
                await anyio.sleep(delay_ms / 1000.0)
                # Exponential backoff: double delay up to 2 seconds max
                delay_ms = min(delay_ms * 2, 2000)

        logger.error(
            "Calendar '%s' did not become available after %s attempts.",
            calendar_name,
            max_attempts,
        )

    # ============= Calendar Operations =============

    async def list_calendars(self) -> list[dict[str, Any]]:
        """List all available calendars for the user.

        Returns both regular calendars and external read-only subscriptions
        (webcal/ICS feeds). Subscriptions are reported with ``read_only=True``
        and a ``source`` URL pointing at the upstream feed (issue #830).
        """
        await self._ensure_calendar_home()
        # Use custom PROPFIND with CalendarServer namespace (cs:) for calendar-color.
        # caldav library's nsmap lacks "CS" namespace, and its CalendarColor uses
        # Apple iCal namespace which Nextcloud doesn't recognize.
        #
        # cs:source / ical:calendar-color are requested to surface external
        # subscriptions: Nextcloud exposes those as cs:subscribed collections
        # carrying a cs:source href and an Apple-namespace color.
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:ical="http://apple.com/ns/ical/">
    <d:prop>
        <d:displayname/>
        <d:resourcetype/>
        <cs:getctag/>
        <c:calendar-description/>
        <cs:calendar-color/>
        <ical:calendar-color/>
        <cs:source/>
    </d:prop>
</d:propfind>"""

        # Override the client-wide webcal-caching header to "Off" for this
        # PROPFIND so subscriptions are returned as cs:subscribed collections
        # (with cs:source) and can be detected and flagged read-only. With the
        # header "On" they would masquerade as regular calendars, hiding the
        # source URL. Event reads keep the client-wide "On" so they stay
        # queryable (see __init__).
        # Pass the request XML via ``body``, not ``props``: caldav's ``props``
        # expects a list of property *names* and would build its own body
        # (discarding this custom CalendarServer/Apple-namespace markup).
        response = await self._dav_client.propfind(
            self._calendar_home_url,
            body=propfind_body,
            depth=1,
            headers={"X-NC-CalDAV-Webcal-Caching": "Off"},
        )

        result = []

        # Parse XML response
        tree = etree.fromstring(response.raw.encode("utf-8"))
        ns = {
            "d": "DAV:",
            "cs": "http://calendarserver.org/ns/",
            "c": "urn:ietf:params:xml:ns:caldav",
            "ical": "http://apple.com/ns/ical/",
        }

        for response_elem in tree.findall(".//d:response", ns):
            # A response is a calendar if it is a regular calendar collection
            # (c:calendar) or an external subscription (cs:subscribed).
            resourcetype = response_elem.find(".//d:resourcetype", ns)
            if resourcetype is None:
                continue
            is_calendar = resourcetype.find(".//c:calendar", ns) is not None
            is_subscribed = resourcetype.find(".//cs:subscribed", ns) is not None
            if not (is_calendar or is_subscribed):
                continue

            href = response_elem.find("./d:href", ns)
            if href is None or not href.text:
                continue

            calendar_url = href.text
            # Extract calendar name from URL
            calendar_name = calendar_url.rstrip("/").split("/")[-1]

            # Skip if this is the calendar home itself
            if calendar_url.rstrip("/") == self._calendar_home_url.rstrip("/"):
                continue

            display_name_elem = response_elem.find(".//d:displayname", ns)
            display_name = (
                display_name_elem.text
                if display_name_elem is not None and display_name_elem.text
                else calendar_name
            )

            description_elem = response_elem.find(".//c:calendar-description", ns)
            description = (
                description_elem.text
                if description_elem is not None and description_elem.text
                else ""
            )

            # Regular calendars expose cs:calendar-color; subscriptions store
            # their color under the Apple iCal namespace.
            color_elem = response_elem.find(".//cs:calendar-color", ns)
            if color_elem is None or not color_elem.text:
                color_elem = response_elem.find(".//ical:calendar-color", ns)
            color = (
                color_elem.text
                if color_elem is not None and color_elem.text
                else "#1976D2"
            )

            # External subscriptions carry a cs:source href pointing at the
            # upstream feed and are read-only.
            source = None
            source_elem = response_elem.find(".//cs:source", ns)
            if source_elem is not None:
                source_href = source_elem.find("./d:href", ns)
                if source_href is not None and source_href.text:
                    source = source_href.text
                elif source_elem.text and source_elem.text.strip():
                    source = source_elem.text.strip()

            result.append(
                {
                    "name": calendar_name,
                    "display_name": display_name,
                    "description": description,
                    "color": color,
                    "href": calendar_url,
                    "read_only": is_subscribed,
                    "source": source,
                }
            )

        logger.debug("Found %s calendars", len(result))
        return result

    async def create_calendar(
        self,
        calendar_name: str,
        display_name: str = "",
        description: str = "",
        color: str = "#1976D2",
    ) -> dict[str, Any]:
        """Create a new calendar with retry on 429 errors."""
        await self._ensure_calendar_home()
        # Use custom MKCALENDAR XML instead of caldav library's make_calendar() due to:
        # 1. Missing CalendarServer namespace (cs:) in caldav's nsmap
        # 2. caldav's CalendarColor uses Apple iCal namespace, not cs:calendar-color
        # 3. make_calendar() doesn't support calendar-description or calendar-color params
        calendar_url = self._get_calendar_url(calendar_name)

        mkcalendar_body = f"""<?xml version="1.0" encoding="utf-8"?>
<mkcalendar xmlns="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/">
    <d:set>
        <d:prop>
            <d:displayname>{display_name or calendar_name}</d:displayname>
            <cs:calendar-color>{color}</cs:calendar-color>
            <caldav:calendar-description xmlns:caldav="urn:ietf:params:xml:ns:caldav">{description}</caldav:calendar-description>
            <caldav:supported-calendar-component-set xmlns:caldav="urn:ietf:params:xml:ns:caldav">
                <caldav:comp name="VEVENT"/>
                <caldav:comp name="VTODO"/>
            </caldav:supported-calendar-component-set>
        </d:prop>
    </d:set>
</mkcalendar>"""

        # Create calendar via MKCALENDAR request
        response = await self._dav_client.mkcalendar(calendar_url, mkcalendar_body)

        if response.status != 201:
            raise RuntimeError(
                f"Failed to create calendar '{calendar_name}': HTTP {response.status}"
            )

        logger.debug("Created calendar: %s", calendar_name)

        # Wait for calendar to be queryable (Nextcloud eventual consistency)
        await self._wait_for_calendar_propagation(calendar_name)

        return {
            "name": calendar_name,
            "display_name": display_name or calendar_name,
            "description": description,
            "color": color,
            "status_code": 201,
        }

    async def delete_calendar(self, calendar_name: str) -> dict[str, Any]:
        """Delete a calendar."""
        await self._ensure_calendar_home()
        # Use absolute URL for deletion
        calendar_url = self._get_calendar_url(calendar_name)
        await self._dav_client.delete(calendar_url)

        logger.debug("Deleted calendar: %s", calendar_name)
        return {"status_code": 204}

    # ============= Event Operations =============

    async def get_calendar_events(
        self,
        calendar_name: str,
        start_datetime: dt.datetime | None = None,
        end_datetime: dt.datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List events in a calendar within date range."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        if start_datetime or end_datetime:
            events = await self._search_events_by_date(
                calendar, start_datetime, end_datetime
            )
            # Client-side recurrence expansion preserves DTSTART format
            # (floating / TZID / UTC). RFC 4791 <C:expand> would normalize
            # everything to UTC and erase the original timezone context.
            do_expand = bool(start_datetime and end_datetime)
        else:
            events = await self._search_calendar_objects(calendar, "VEVENT")
            do_expand = False

        result = []
        for event in events:
            await _maybe_await(event.load(only_if_unloaded=True))
            if not event.data:
                continue
            event_etag = await self._get_object_etag(event)

            try:
                cal = Calendar.from_ical(event.data)
            except Exception as e:
                logger.error("Error parsing iCalendar event: %s", e)
                continue

            href = str(event.url)
            event_dicts = self._expand_event_occurrences(
                cal, start_datetime, end_datetime, do_expand
            )
            for event_dict in event_dicts:
                event_dict["href"] = href
                event_dict["etag"] = event_etag
                result.append(event_dict)

                if len(result) >= limit:
                    break

            if len(result) >= limit:
                break

        logger.debug("Found %d events", len(result))
        return result

    def _expand_event_occurrences(
        self,
        cal: Any,
        start_datetime: dt.datetime | None,
        end_datetime: dt.datetime | None,
        do_expand: bool,
    ) -> list[dict[str, Any]]:
        """Return one event dict per occurrence in [start, end), or one dict for the master VEVENT.

        When ``do_expand`` is true and the resource has an RRULE, expand recurrences
        client-side using ``recurring_ical_events`` so that TZID and floating-local
        semantics are preserved on the wire (server-side ``<C:expand>`` would
        UTC-normalize every DTSTART per RFC 4791 §9.6.5).
        """
        if not do_expand:
            for component in cal.walk("VEVENT"):
                return [self._extract_vevent_data(component)]
            return []

        has_rrule = any("rrule" in component for component in cal.walk("VEVENT"))
        if not has_rrule:
            for component in cal.walk("VEVENT"):
                return [self._extract_vevent_data(component)]
            return []

        try:
            assert start_datetime is not None and end_datetime is not None
            occurrences = recurring_ical_events.of(cal).between(
                start_datetime, end_datetime
            )
        except Exception as e:
            logger.warning(
                "Client-side recurrence expansion failed (%s); returning master event",
                e,
            )
            return [
                self._extract_vevent_data(component) for component in cal.walk("VEVENT")
            ]

        return [self._extract_vevent_data(occ) for occ in occurrences]

    async def _search_events_by_date(
        self,
        calendar: AsyncCalendar,
        start_datetime: dt.datetime | None = None,
        end_datetime: dt.datetime | None = None,
    ) -> list:
        """Execute a CalDAV REPORT with time-range filter.

        Returns raw VEVENT resources (no server-side ``<C:expand>``). The caller
        is responsible for expanding recurring events client-side so that
        TZID/floating semantics are preserved.
        """
        # Ensure naive datetimes are treated as UTC for the wire-level filter
        if start_datetime and start_datetime.tzinfo is None:
            start_datetime = start_datetime.replace(tzinfo=dt.UTC)
        if end_datetime and end_datetime.tzinfo is None:
            end_datetime = end_datetime.replace(tzinfo=dt.UTC)

        return await self._search_calendar_objects(
            calendar,
            "VEVENT",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )

    async def _search_calendar_objects(
        self,
        calendar: AsyncCalendar,
        component: str,
        *,
        start_datetime: dt.datetime | None = None,
        end_datetime: dt.datetime | None = None,
    ) -> list:
        """REPORT calendar-data and getetag together for one component type."""
        inner_comp_filter = cdav.CompFilter(name=component)
        if start_datetime or end_datetime:
            inner_comp_filter += cdav.TimeRange(start_datetime, end_datetime)
        outer_comp_filter = cdav.CompFilter(name="VCALENDAR") + inner_comp_filter
        filter_element = cdav.Filter() + outer_comp_filter

        data = cdav.CalendarData()
        query = (
            cdav.CalendarQuery() + [dav.Prop() + [dav.GetEtag(), data]] + filter_element
        )

        body = etree.tostring(
            query.xmlelement(), encoding="utf-8", xml_declaration=True
        )
        assert calendar.client is not None
        response = await calendar.client.report(str(calendar.url), body, depth=1)  # type: ignore[misc]  # ty: ignore[invalid-await]  # dual-mode
        status = getattr(response, "status", 207)
        if status == 404:
            raise caldav_error.NotFoundError(
                url=str(calendar.url),
                reason=getattr(response, "reason", "Calendar not found"),
            )
        if status >= 400:
            raise caldav_error.DAVError(
                url=str(calendar.url),
                reason=getattr(response, "reason", f"HTTP {status}"),
            )

        # Parse response (same pattern as AsyncCalendar.search)
        objects = []
        response_data = response.expand_simple_props(
            [dav.GetEtag(), cdav.CalendarData()]
        )
        for href, props in response_data.items():
            if href == str(calendar.url):
                continue
            cal_data = props.get(cdav.CalendarData.tag)
            if cal_data:
                object_class = AsyncEvent if component == "VEVENT" else AsyncTodo
                obj = object_class(
                    client=calendar.client,
                    url=calendar.url.join(href),  # type: ignore[union-attr]  # ty: ignore[unresolved-attribute]  # url is always set for calendars
                    data=cal_data,
                    parent=calendar,
                    props={dav.GetEtag.tag: props.get(dav.GetEtag.tag)},
                )
                objects.append(obj)

        return objects

    async def create_event(
        self, calendar_name: str, event_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new calendar event."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        event_uid = str(uuid.uuid4())
        ical_content = self._create_ical_event(event_data, event_uid)

        # caldav v3's _async_put raises PutError on HTTP failure
        event = await calendar.save_event(ical=ical_content)  # type: ignore[misc]  # ty: ignore[invalid-await]  # dual-mode

        logger.debug("Created event %s", event_uid)

        return {
            "uid": event_uid,
            "href": str(event.url),
            "etag": "",
            "status_code": 201,
        }

    async def update_event(
        self,
        calendar_name: str,
        event_uid: str,
        event_data: dict[str, Any],
        etag: str,
    ) -> dict[str, Any]:
        """Update an existing calendar event."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        # Find the event by UID using caldav library
        event = await self._async_object_by_uid(
            calendar, event_uid, cdav.CompFilter("VEVENT")
        )
        await _maybe_await(event.load(only_if_unloaded=True))
        current_etag = await self._require_current_etag(
            event, etag, kind="event", uid=event_uid
        )

        # Merge updates into existing iCal data
        updated_ical = self._merge_ical_properties(event.data, event_data)  # type: ignore[arg-type]
        new_etag = await self._conditional_update(
            event, updated_ical, current_etag, kind="event", uid=event_uid
        )

        logger.debug("Updated event %s", event_uid)
        return {
            "uid": event_uid,
            "href": str(event.url),
            "etag": new_etag,
            "status_code": 200,
        }

    @staticmethod
    def _status_from_dav_error(exc: caldav_error.DAVError) -> int | None:
        """Best-effort HTTP status from a caldav DAVError, or ``None``.

        caldav offers nothing structured here. ``_post_delete`` raises
        ``DeleteError(errmsg(r))``, and ``errmsg`` formats
        ``"<status> <reason>\\n\\n<body>"`` — which lands in the exception's
        ``url`` slot, because ``DAVError.__init__``'s first positional parameter
        is ``url``. So the status is the leading integer of ``exc.url``, with
        ``str(exc)`` as a fallback in case a future caldav populates it properly.

        Returns ``None`` when nothing parses; callers must treat that as an
        unknown refusal rather than substituting a plausible-looking code.
        """
        for candidate in (getattr(exc, "url", None), str(exc)):
            if not candidate:
                continue
            match = re.search(r"\b(\d{3})\b", str(candidate))
            if match:
                return int(match.group(1))
        return None

    async def _delete_dav_object(
        self,
        calendar_name: str,
        uid: str,
        comp_filter: Any,
        kind: str,
    ) -> dict[str, Any]:
        """Delete one CalDAV object, mapping refusals to a structured result.

        Only ``NotFoundError`` used to be caught, so a 403 (Nextcloud refuses to
        delete iMIP/scheduled objects) or a 409/412 (a stale entry in the calendar
        trashbin colliding with the delete) escaped as a raw caldav traceback out
        of the MCP tool.

        Only ``DeleteError`` is treated as a refusal: it is precisely what
        ``DAVObject._post_delete`` raises when the server rejects the DELETE.
        caldav's other error classes are flat siblings under ``DAVError``, not
        subclasses of it, so ``AuthorizationError`` (expired credential) and
        ``RateLimitError`` (retryable) keep propagating instead of being flattened
        into a per-object "the server refused this event" message. Catching the
        ``DAVError`` base would have masked exactly those.
        """
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        try:
            obj = await self._async_object_by_uid(calendar, uid, comp_filter)
            await _maybe_await(obj.delete())
            logger.debug("Deleted %s %s", kind, uid)
            return {"success": True, "status_code": 204}
        except caldav_error.NotFoundError as e:
            logger.debug("%s %s not found: %s", kind.capitalize(), uid, e)
            return {"success": True, "status_code": 404}
        except caldav_error.AuthorizationError:
            if kind != "todo":
                raise
            repaired = await self._repair_todo_trash_collision(obj, uid)
            if repaired is not None:
                return repaired
            return self._delete_refusal_result(403, kind, uid)
        except caldav_error.DeleteError as e:
            status = self._status_from_dav_error(e)
            if kind == "todo":
                repaired = await self._repair_todo_trash_collision(obj, uid)
                if repaired is not None:
                    return repaired
            return self._delete_refusal_result(status, kind, uid, reason=str(e))

    def _delete_refusal_result(
        self,
        status: int | None,
        kind: str,
        uid: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Return the stable refusal shape used by calendar delete tools."""
        logger.warning(
            "Server refused to delete %s %s (status %s)",
            kind,
            uid,
            status if status is not None else "unknown",
        )
        result: dict[str, Any] = {
            "success": False,
            "status_code": status if status is not None else 500,
            "message": self._delete_refusal_message(status, kind),
        }
        if reason is not None:
            result["reason"] = reason
        return result

    @staticmethod
    def _is_calendar_trashbin_collision(response: Any) -> bool:
        """Recognize Nextcloud's exact stale calendar-trash refusal."""
        return (
            response.status == 403
            and "therefore this object can't be moved into the trashbin" in response.raw
        )

    async def _purge_todo_trash_entries(self, todo_uid: str) -> int:
        """Permanently remove stale trash entries with this exact VTODO UID."""
        trash_url = urljoin(self._calendar_home_url, "trashbin/objects/")
        ns_dav = "DAV:"
        ns_caldav = "urn:ietf:params:xml:ns:caldav"
        query = etree.Element(
            f"{{{ns_caldav}}}calendar-query",
            nsmap={"d": ns_dav, "c": ns_caldav},
        )
        prop = etree.SubElement(query, f"{{{ns_dav}}}prop")
        etree.SubElement(prop, f"{{{ns_dav}}}getetag")
        etree.SubElement(prop, f"{{{ns_caldav}}}calendar-data")
        root_filter = etree.SubElement(query, f"{{{ns_caldav}}}filter")
        calendar_filter = etree.SubElement(
            root_filter, f"{{{ns_caldav}}}comp-filter", name="VCALENDAR"
        )
        todo_filter = etree.SubElement(
            calendar_filter, f"{{{ns_caldav}}}comp-filter", name="VTODO"
        )
        uid_filter = etree.SubElement(
            todo_filter, f"{{{ns_caldav}}}prop-filter", name="UID"
        )
        text_match = etree.SubElement(
            uid_filter, f"{{{ns_caldav}}}text-match", collation="i;octet"
        )
        text_match.text = todo_uid

        response = await self._dav_client.request(
            trash_url,
            method="REPORT",
            body=etree.tostring(query, encoding="unicode"),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        if response.status != 207:
            raise RuntimeError(
                f"calendar trashbin query returned HTTP {response.status}"
            )

        document = etree.fromstring(response.raw.encode("utf-8"))
        matching_urls: list[str] = []
        for item in document.findall(f".//{{{ns_dav}}}response"):
            href = item.findtext(f"{{{ns_dav}}}href")
            raw_ical = item.findtext(f".//{{{ns_caldav}}}calendar-data")
            if not href or not raw_ical:
                continue
            calendar_data = Calendar.from_ical(raw_ical)
            if any(
                component.name == "VTODO" and str(component.get("uid", "")) == todo_uid
                for component in calendar_data.walk()
            ):
                matching_urls.append(urljoin(trash_url, href))

        purged = 0
        for resource_url in matching_urls:
            delete_response = await self._dav_client.delete(resource_url)
            if delete_response.status == 404:
                continue
            if delete_response.status not in (200, 204):
                raise RuntimeError(
                    f"calendar trashbin purge returned HTTP {delete_response.status}"
                )
            purged += 1
        return purged

    async def _repair_todo_trash_collision(
        self, todo: Any, todo_uid: str
    ) -> dict[str, Any] | None:
        """Purge an exact-UID trash collision and retry one refused todo delete.

        ``caldav`` raises ``AuthorizationError`` before returning a low-level
        403 response, so the response body cannot always be inspected here.
        In that real transport path, the exact-UID trash query is the gate:
        without a matching stale object, no purge or retry is performed.
        """
        try:
            try:
                response = await self._dav_client.delete(
                    str(todo.url), headers={"X-NC-Scheduling": "false"}
                )
            except caldav_error.AuthorizationError:
                response = None

            if response is not None:
                if response.status == 404:
                    return {"status_code": 404}
                if response.status in (200, 204):
                    return {"status_code": 204}
                if not self._is_calendar_trashbin_collision(response):
                    return None

            purged = await self._purge_todo_trash_entries(todo_uid)
            if not purged:
                return None
            try:
                retry = await self._dav_client.delete(
                    str(todo.url), headers={"X-NC-Scheduling": "false"}
                )
            except caldav_error.AuthorizationError:
                return None
            if retry.status == 404:
                return {"status_code": 404}
            if retry.status in (200, 204):
                return {
                    "status_code": 204,
                    "stale_trash_entries_purged": purged,
                }
            raise RuntimeError(
                f"DELETE after calendar trashbin repair returned HTTP {retry.status}"
            )
        except Exception as repair_error:
            logger.warning(
                "Calendar trashbin repair failed for todo %s: %s",
                todo_uid,
                repair_error,
            )
            return None

    @staticmethod
    def _delete_refusal_message(status: int | None, kind: str) -> str:
        """Explain a delete refusal in terms of its likely cause."""
        if status == 403:
            return (
                f"The server refused to delete this {kind}. Nextcloud rejects "
                "deletion of scheduled (iMIP) objects — if you are an attendee, "
                "decline the invitation instead; if you are the organizer, cancel "
                "it so attendees are notified."
            )
        if status in (409, 412, 500):
            return (
                f"The server refused to delete this {kind}, most likely because a "
                "previously-deleted object with the same UID is still in the "
                "calendar trashbin. Empty the trashbin in the Nextcloud Calendar "
                "UI and retry."
            )
        return f"The server refused to delete this {kind}" + (
            f" (HTTP {status})." if status is not None else "."
        )

    async def delete_event(self, calendar_name: str, event_uid: str) -> dict[str, Any]:
        """Delete a calendar event.

        Returns a structured result rather than raising on a server refusal —
        see :meth:`_delete_dav_object`.
        """
        return await self._delete_dav_object(
            calendar_name, event_uid, cdav.CompFilter("VEVENT"), "event"
        )

    async def get_event(
        self, calendar_name: str, event_uid: str
    ) -> tuple[dict[str, Any], str]:
        """Get detailed information about a specific event."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        event = await self._async_object_by_uid(
            calendar, event_uid, cdav.CompFilter("VEVENT")
        )
        await _maybe_await(event.load(only_if_unloaded=True))

        event_data = self._parse_ical_event(event.data) if event.data else None  # type: ignore[arg-type]
        if not event_data:
            raise ValueError(f"Failed to parse event data for {event_uid}")

        event_data["href"] = str(event.url)
        event_etag = await self._get_object_etag(event)
        event_data["etag"] = event_etag

        logger.debug("Retrieved event %s", event_uid)
        return event_data, event_etag or ""

    async def search_events_across_calendars(
        self,
        start_datetime: dt.datetime | None = None,
        end_datetime: dt.datetime | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search events across all calendars with advanced filtering."""
        await self._ensure_calendar_home()
        try:
            calendars = await self.list_calendars()
            all_events = []

            for calendar in calendars:
                try:
                    events = await self.get_calendar_events(
                        calendar["name"], start_datetime, end_datetime
                    )

                    # Apply filters if provided
                    if filters:
                        events = self._apply_event_filters(events, filters)

                    # Add calendar info to each event
                    for event in events:
                        event["calendar_name"] = calendar["name"]
                        event["calendar_display_name"] = calendar.get(
                            "display_name", calendar["name"]
                        )

                    all_events.extend(events)
                except Exception as e:
                    logger.warning(
                        "Error getting events from calendar %s: %s", calendar["name"], e
                    )
                    continue

            return all_events

        except Exception as e:
            logger.error("Error searching events across calendars: %s", e)
            raise

    # ============= Todo/Task Operations (NEW) =============

    async def list_todos(
        self, calendar_name: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """List todos/tasks, optionally excluding completed VTODOs."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        # Get all todos including completed ones (filtering is done client-side)
        todos = await self._search_calendar_objects(calendar, "VTODO")

        result = []
        for todo in todos:
            # Only load if data not already present from REPORT response
            # This avoids 404 errors for virtual calendars (e.g., Deck boards)
            await _maybe_await(todo.load(only_if_unloaded=True))
            if todo.data:
                todo_dict = self._parse_ical_todo(todo.data)  # type: ignore[arg-type]
            else:
                continue
            if todo_dict:
                todo_dict["href"] = str(todo.url)
                todo_dict["etag"] = await self._get_object_etag(todo)

                # Apply filters if provided
                if not filters or self._todo_matches_filters(todo_dict, filters):
                    result.append(todo_dict)

        logger.debug("Found %s todos", len(result))
        return result

    async def get_todo(self, calendar_name: str, todo_uid: str) -> dict[str, Any]:
        """Fetch one VTODO by UID with REPORT-coupled content and exact ETag."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)
        todos = await self._search_calendar_objects(calendar, "VTODO")

        for todo in todos:
            if not todo.data:
                continue
            todo_dict = self._parse_ical_todo(todo.data)  # type: ignore[arg-type]
            if todo_dict is None or todo_dict.get("uid") != todo_uid:
                continue
            etag = getattr(todo, "props", {}).get(dav.GetEtag.tag)
            if not etag:
                raise CalendarEtagUnavailableError(
                    f"Cannot read todo {todo_uid}: the REPORT response supplied "
                    "no ETag. Retry only after the server provides an exact ETag."
                )
            todo_dict["href"] = str(todo.url)
            todo_dict["etag"] = etag
            return todo_dict

        raise caldav_error.NotFoundError(f"{todo_uid} not found on server")

    async def create_todo(
        self, calendar_name: str, todo_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new todo/task."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        todo_uid = str(uuid.uuid4())
        ical_content = self._create_ical_todo(todo_data, todo_uid)

        # caldav v3's _async_put raises PutError on HTTP failure
        todo = await calendar.save_todo(ical=ical_content)  # type: ignore[misc]  # ty: ignore[invalid-await]  # dual-mode

        logger.debug("Created todo %s", todo_uid)

        return {
            "uid": todo_uid,
            "href": str(todo.url),
            "etag": "",
            "status_code": 201,
        }

    async def update_todo(
        self,
        calendar_name: str,
        todo_uid: str,
        todo_data: dict[str, Any],
        etag: str,
    ) -> dict[str, Any]:
        """Update an existing todo/task."""
        await self._ensure_calendar_home()
        calendar = self._get_calendar(calendar_name)

        try:
            # Find the todo by UID
            todo = await self._async_object_by_uid(
                calendar, todo_uid, cdav.CompFilter("VTODO")
            )
            await _maybe_await(todo.load(only_if_unloaded=True))

            logger.debug(
                "Loaded todo %s, current data length: %s", todo_uid, len(todo.data)
            )
            current_etag = await self._require_current_etag(
                todo, etag, kind="todo", uid=todo_uid
            )

            # Merge updates into existing iCal data
            updated_ical = self._merge_ical_todo_properties(
                todo.data,  # type: ignore[arg-type]
                todo_data,
                todo_uid,
            )
            logger.debug("Merged iCal data length: %s", len(updated_ical))
            logger.debug("Updated iCal content:\\n%s", updated_ical)

            new_etag = await self._conditional_update(
                todo, updated_ical, current_etag, kind="todo", uid=todo_uid
            )

            logger.debug("Updated todo %s", todo_uid)
            return {
                "uid": todo_uid,
                "href": str(todo.url),
                "etag": new_etag,
                "status_code": 200,
            }
        except Exception as e:
            logger.error("Error updating todo %s: %s", todo_uid, e)
            raise

    async def delete_todo(self, calendar_name: str, todo_uid: str) -> dict[str, Any]:
        """Delete a todo/task.

        Returns a structured result rather than raising on a server refusal —
        see :meth:`_delete_dav_object`.
        """
        return await self._delete_dav_object(
            calendar_name, todo_uid, cdav.CompFilter("VTODO"), "todo"
        )

    async def search_todos_across_calendars(
        self, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Search todos across all calendars."""
        await self._ensure_calendar_home()
        try:
            calendars = await self.list_calendars()
            all_todos = []

            for calendar in calendars:
                try:
                    todos = await self.list_todos(calendar["name"], filters)

                    # Add calendar info to each todo
                    for todo in todos:
                        todo["calendar_name"] = calendar["name"]
                        todo["calendar_display_name"] = calendar.get(
                            "display_name", calendar["name"]
                        )

                    all_todos.extend(todos)
                except Exception as e:
                    logger.warning(
                        "Error getting todos from calendar %s: %s", calendar["name"], e
                    )
                    continue

            return all_todos

        except Exception as e:
            logger.error("Error searching todos across calendars: %s", e)
            raise

    # ============= Helper Methods - Event iCalendar =============

    @staticmethod
    def _resolve_timezone(tz_name: str) -> ZoneInfo | None:
        """Resolve an IANA timezone name to ZoneInfo, returning None for invalid input."""
        if not tz_name:
            return None
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning(
                "Unknown IANA timezone %r — falling back to floating local time",
                tz_name,
            )
            return None

    @staticmethod
    def _stored_is_all_day(component, prop: str = "DTSTART") -> bool | None:
        """Whether ``prop`` on the stored component is a DATE (all-day) value.

        Returns ``None`` when the property is absent, so callers can distinguish
        "unknown" from "timed" — the update path needs that difference to decide
        whether to inherit the stored value type or fall back to a default.
        """
        value = component.get(prop)
        if value is None:
            return None
        inner = getattr(value, "dt", None)
        if inner is None:
            return None
        return isinstance(inner, dt.date) and not isinstance(inner, dt.datetime)

    @staticmethod
    def _stored_tzid(component, prop: str = "DTSTART") -> str | None:
        """Return the TZID parameter on ``prop``, if the stored value carries one.

        ``None`` for all-day, floating and UTC values — none of which should have
        a zone inherited onto them.
        """
        value = component.get(prop)
        if value is None:
            return None
        tzid = getattr(value, "params", {}).get("TZID")
        return str(tzid) if tzid else None

    @classmethod
    def _parse_event_datetime(
        cls,
        dt_str: str,
        tz_name: str | None = None,
        *,
        inherited_tz: str | None = None,
    ) -> tuple[dt.datetime, ZoneInfo | None]:
        """Parse an ISO datetime string with optional TZID application.

        Returns ``(parsed_dt, applied_zoneinfo)`` where ``applied_zoneinfo``
        is non-None only when a zone was applied to a naive input — the
        caller uses this to know whether to emit a VTIMEZONE component.

        ``tz_name`` is the caller's explicit request; ``inherited_tz`` is the TZID
        already on the stored property. They are separate parameters on purpose:
        passing the stored zone as ``tz_name`` would fire the "explicit offset;
        ignoring timezone" warning spuriously on every offset-bearing update, and
        would override a caller who deliberately wants floating time. Explicit
        always wins over inherited.
        """
        parsed = dt.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        zi = cls._resolve_timezone(tz_name) if tz_name else None

        if parsed.tzinfo is not None:
            # Only complain about a zone the caller actually asked for.
            if tz_name:
                logger.warning(
                    "Datetime %r has an explicit offset; ignoring timezone=%r",
                    dt_str,
                    tz_name,
                )
            offset = parsed.utcoffset()
            if isinstance(parsed.tzinfo, dt.timezone) and offset != dt.timedelta(0):
                toronto_value = parsed.replace(tzinfo=cls._TORONTO_TZ)
                if toronto_value.utcoffset() == offset:
                    return toronto_value, cls._TORONTO_TZ
            return parsed, None

        if zi is not None:
            return parsed.replace(tzinfo=zi), zi

        if inherited_tz:
            # Resolve quietly. A stored TZID is not guaranteed to be an IANA name:
            # icalendar renders a fixed-offset tzinfo as TZID="UTC-04:00" with no
            # VTIMEZONE, so inheriting one is expected to fail. Routing that
            # through _resolve_timezone would log "Unknown IANA timezone", which
            # reads as an error when the floating-time fallback below is the
            # correct, harmless outcome. An explicit `timezone=` from the caller
            # still warns — that one really is a mistake worth surfacing.
            try:
                inherited_zi = ZoneInfo(inherited_tz)
            except (ZoneInfoNotFoundError, ValueError):
                logger.debug(
                    "Stored TZID %r is not an IANA name; not inheriting it",
                    inherited_tz,
                )
                inherited_zi = None
            if inherited_zi is not None:
                # Inheriting is the expected behaviour, not a problem — debug, not
                # warning. Without this, updating the time of a TZID-bound event
                # without re-passing `timezone` silently produced floating time.
                logger.debug(
                    "Datetime %r is naive; inheriting stored TZID=%r",
                    dt_str,
                    inherited_tz,
                )
                return parsed.replace(tzinfo=inherited_zi), inherited_zi

        logger.warning(
            "Datetime %r is naive and no timezone was supplied — storing as RFC 5545 floating local time",
            dt_str,
        )
        return parsed, None

    _TORONTO_TZ = ZoneInfo("America/Toronto")

    @classmethod
    def _parse_caldav_datetime(
        cls, value: str, *, all_day: bool = False
    ) -> dt.datetime | dt.date:
        """Parse a CalDAV value without losing wall-clock timezone semantics.

        All-day values become dates. Timed values must include an offset or
        ``Z``. Fixed offsets matching Toronto on the target date are promoted
        to ``America/Toronto`` so recurring and subsequently edited values keep
        their wall-clock meaning across DST. Other offsets remain unchanged.
        """
        if all_day:
            return dt.date.fromisoformat(value.split("T", maxsplit=1)[0])

        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"Datetime is not valid ISO 8601: {value!r}") from exc

        if parsed.tzinfo is None:
            raise ValueError(
                f"Datetime missing timezone offset: {value!r}. "
                "Provide Z or an explicit +/-HH:MM offset."
            )

        offset = parsed.utcoffset()
        if isinstance(parsed.tzinfo, dt.timezone) and offset != dt.timedelta(0):
            toronto_value = parsed.replace(tzinfo=cls._TORONTO_TZ)
            if toronto_value.utcoffset() == offset:
                parsed = toronto_value

        return parsed

    @staticmethod
    def _decode_ical_value(value: Any) -> str:
        if hasattr(value, "to_ical"):
            raw = value.to_ical()
            return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return str(value)

    @classmethod
    def _extract_ical_values(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        return [cls._decode_ical_value(item) for item in values]

    @staticmethod
    def _parse_alarm_datetime(value: str) -> dt.datetime:
        cleaned = value.strip().replace("Z", "+00:00")
        try:
            return dt.datetime.fromisoformat(cleaned)
        except ValueError:
            utc = value.strip().endswith("Z")
            parsed = dt.datetime.strptime(
                value.strip().removesuffix("Z"), "%Y%m%dT%H%M%S"
            )
            return parsed.replace(tzinfo=dt.UTC) if utc else parsed

    def _extract_valarms(self, component: Any) -> list[dict[str, Any]]:
        reminders: list[dict[str, Any]] = []
        for index, alarm in enumerate(
            sub for sub in component.subcomponents if sub.name == "VALARM"
        ):
            trigger = alarm.get("trigger")
            trigger_dt = getattr(trigger, "dt", None)
            reminder: dict[str, Any] = {
                "index": index,
                "action": str(alarm.get("action", "DISPLAY")),
            }
            for source, target in (
                ("description", "description"),
                ("summary", "summary"),
            ):
                value = alarm.get(source)
                if value is not None:
                    reminder[target] = str(value)
            repeat = alarm.get("repeat")
            if repeat is not None:
                reminder["repeat"] = int(repeat)
            duration = alarm.get("duration")
            if duration is not None:
                reminder["duration"] = self._decode_ical_value(duration)
                duration_dt = getattr(duration, "dt", None)
                if isinstance(duration_dt, dt.timedelta):
                    reminder["duration_seconds"] = int(duration_dt.total_seconds())
            attendees = self._extract_ical_values(alarm.get("attendee"))
            if attendees:
                reminder["attendees"] = [
                    value.removeprefix("mailto:") for value in attendees
                ]
            attachments = self._extract_ical_values(alarm.get("attach"))
            if attachments:
                reminder["attachments"] = attachments
            if trigger is not None:
                reminder["trigger"] = self._decode_ical_value(trigger)
                params = getattr(trigger, "params", {}) or {}
                if params.get("RELATED"):
                    reminder["related"] = str(params["RELATED"])
                if isinstance(trigger_dt, dt.datetime):
                    reminder["trigger_at"] = trigger_dt.isoformat()
                elif isinstance(trigger_dt, dt.timedelta):
                    seconds = int(trigger_dt.total_seconds())
                    reminder["offset_seconds"] = seconds
                    if seconds < 0 and seconds % 60 == 0:
                        reminder["minutes_before"] = abs(seconds) // 60
            reminders.append(reminder)
        return reminders

    def _build_valarm(self, reminder: dict[str, Any]) -> Alarm:
        alarm = Alarm()
        alarm.add("action", reminder.get("action", "DISPLAY"))
        alarm.add("description", reminder.get("description", "Event reminder"))
        if reminder.get("summary"):
            alarm.add("summary", str(reminder["summary"]))
        if "repeat" in reminder:
            alarm.add("repeat", int(reminder["repeat"]))
        if "duration_seconds" in reminder:
            alarm.add(
                "duration", dt.timedelta(seconds=int(reminder["duration_seconds"]))
            )
        elif reminder.get("duration"):
            alarm.add("duration", vDDDTypes.from_ical(str(reminder["duration"])))
        attendees = reminder.get("attendees") or []
        for attendee in [attendees] if isinstance(attendees, str) else attendees:
            value = str(attendee)
            alarm.add(
                "attendee",
                value if value.lower().startswith("mailto:") else f"mailto:{value}",
            )
        attachments = reminder.get("attachments") or []
        for attachment in (
            [attachments] if isinstance(attachments, str) else attachments
        ):
            alarm.add("attach", str(attachment))
        params = (
            {"RELATED": str(reminder["related"])} if reminder.get("related") else {}
        )
        if reminder.get("trigger_at"):
            params["VALUE"] = "DATE-TIME"
            trigger = self._parse_alarm_datetime(str(reminder["trigger_at"]))
        elif reminder.get("trigger"):
            raw_trigger = str(reminder["trigger"])
            trigger = (
                vDDDTypes.from_ical(raw_trigger)
                if raw_trigger.startswith(("P", "-P", "+P"))
                else self._parse_alarm_datetime(raw_trigger)
            )
        elif "minutes_before" in reminder:
            trigger = dt.timedelta(minutes=-int(reminder["minutes_before"]))
        elif "offset_seconds" in reminder:
            trigger = dt.timedelta(seconds=int(reminder["offset_seconds"]))
        else:
            trigger = dt.timedelta(minutes=-15)
        alarm.add("trigger", trigger, parameters=params)
        return alarm

    def _sync_valarms_by_index(
        self, component: Any, reminders: list[dict[str, Any]]
    ) -> None:
        component.subcomponents = [
            sub for sub in component.subcomponents if sub.name != "VALARM"
        ]
        for reminder in reminders:
            component.add_component(self._build_valarm(reminder))

    def _add_reminders_or_legacy_alarm(
        self, component: Any, data: dict[str, Any], default_description: str
    ) -> None:
        if "reminders" in data:
            self._sync_valarms_by_index(component, data.get("reminders") or [])
        elif data.get("reminder_minutes", 0) > 0:
            component.add_component(
                self._build_valarm(
                    {
                        "description": default_description,
                        "minutes_before": data["reminder_minutes"],
                    }
                )
            )

    def _create_ical_event(self, event_data: dict[str, Any], event_uid: str) -> str:
        """Create iCalendar content from event data."""
        cal = Calendar()
        cal.add("prodid", "-//Nextcloud MCP Server//EN")
        cal.add("version", "2.0")

        event = ICalEvent()
        event.add("uid", event_uid)
        event.add("summary", event_data.get("title", ""))
        event.add("description", event_data.get("description", ""))
        event.add("location", event_data.get("location", ""))

        # Handle dates/times
        start_str = event_data.get("start_datetime", "")
        end_str = event_data.get("end_datetime", "")
        all_day = event_data.get("all_day", False)
        tz_name = event_data.get("timezone", "")
        used_timezones: set[ZoneInfo] = set()

        if start_str:
            if all_day:
                start_date = dt.datetime.fromisoformat(start_str.split("T")[0]).date()
                event.add("dtstart", start_date)
                if end_str:
                    end_date = dt.datetime.fromisoformat(end_str.split("T")[0]).date()
                    event.add("dtend", end_date)
            else:
                start_dt, zi = self._parse_event_datetime(start_str, tz_name)
                if zi is not None:
                    used_timezones.add(zi)
                event.add("dtstart", start_dt)
                if end_str:
                    end_dt, zi = self._parse_event_datetime(end_str, tz_name)
                    if zi is not None:
                        used_timezones.add(zi)
                    event.add("dtend", end_dt)

        # Add categories
        categories = event_data.get("categories", "")
        if categories:
            event.add("categories", [c.strip() for c in categories.split(",")])

        # Add priority and status
        priority = event_data.get("priority", 5)
        event.add("priority", priority)

        status = event_data.get("status", "CONFIRMED")
        event.add("status", status)

        # Add privacy classification
        privacy = event_data.get("privacy", "PUBLIC")
        event.add("class", privacy)

        # Add URL
        url = event_data.get("url", "")
        if url:
            event.add("url", url)

        # Handle recurrence
        recurring = event_data.get("recurring", False)
        if recurring:
            recurrence_rule = event_data.get("recurrence_rule", "")
            if recurrence_rule:
                event.add("rrule", vRecur.from_ical(recurrence_rule))

        self._add_reminders_or_legacy_alarm(event, event_data, "Event reminder")

        # Add attendees
        attendees = event_data.get("attendees", "")
        if attendees:
            for email in attendees.split(","):
                if email.strip():
                    event.add("attendee", f"mailto:{email.strip()}")

        # Add timestamps
        now = dt.datetime.now(dt.UTC)
        event.add("created", now)
        event.add("dtstamp", now)
        event.add("last-modified", now)

        # VTIMEZONE must appear before the referencing VEVENT.
        for zi in used_timezones:
            cal.add_component(Timezone.from_tzinfo(zi))
        cal.add_component(event)
        return cal.to_ical().decode("utf-8")

    def _extract_vevent_data(self, component) -> dict[str, Any]:
        """Extract event data from a single VEVENT component."""
        event_data: dict[str, Any] = {
            "uid": str(component.get("uid", "")),
            "title": str(component.get("summary", "")),
            "description": str(component.get("description", "")),
            "location": str(component.get("location", "")),
            "status": str(component.get("status", "CONFIRMED")),
            "priority": int(component.get("priority", 5)),
            "privacy": str(component.get("class", "PUBLIC")),
            "url": str(component.get("url", "")),
        }

        # Handle dates. The ``.isoformat()`` representation already encodes the
        # storage semantics: no suffix for floating local, ``+00:00`` for UTC,
        # and the offset (e.g. ``-04:00``) for TZID-bound datetimes. The IANA
        # TZID name is surfaced separately as ``start_tz``/``end_tz`` so callers
        # can distinguish "10am NY time" (recurs in local time across DST) from
        # "14:00 UTC" (same UTC instant), which the offset alone cannot express.
        dtstart = component.get("dtstart")
        if dtstart:
            event_data["start_datetime"] = dtstart.dt.isoformat()
            event_data["all_day"] = isinstance(dtstart.dt, dt.date) and not isinstance(
                dtstart.dt, dt.datetime
            )
            tzid = dtstart.params.get("TZID") if dtstart.params else None
            if tzid:
                event_data["start_tz"] = str(tzid)

        dtend = component.get("dtend")
        if dtend:
            event_data["end_datetime"] = dtend.dt.isoformat()
            tzid = dtend.params.get("TZID") if dtend.params else None
            if tzid:
                event_data["end_tz"] = str(tzid)

        # Handle categories
        categories = component.get("categories")
        if categories:
            event_data["categories"] = self._extract_categories(categories)

        # Handle recurrence
        rrule = component.get("rrule")
        if rrule:
            event_data["recurring"] = True
            event_data["recurrence_rule"] = str(rrule)

        # Handle attendees
        attendees = []
        for attendee in component.get("attendee", []):
            if isinstance(attendee, list):
                attendees.extend(str(a).replace("mailto:", "") for a in attendee)
            else:
                attendees.append(str(attendee).replace("mailto:", ""))
        if attendees:
            event_data["attendees"] = ",".join(attendees)

        reminders = self._extract_valarms(component)
        if reminders:
            event_data["reminders"] = reminders

        return event_data

    def _parse_ical_event(self, ical_text: str) -> dict[str, Any] | None:
        """Parse iCalendar text and extract the first event."""
        try:
            cal = Calendar.from_ical(ical_text)
            for component in cal.walk():
                if component.name == "VEVENT":
                    return self._extract_vevent_data(component)
            return None
        except Exception as e:
            logger.error("Error parsing iCalendar event: %s", e)
            return None

    @staticmethod
    def _validate_all_day_flip(
        target_all_day: bool, has_start: bool, has_end: bool
    ) -> None:
        """Reject flips between all-day and timed that can't produce valid iCal.

        Only called when the update actually changes the value type.
        """
        if (has_start or has_end) and not (has_start and has_end):
            raise ValueError(
                "changing an event between all-day and timed requires both "
                "start_datetime and end_datetime, so DTSTART and DTEND cannot "
                "end up with mismatched value types"
            )
        if not has_start and not has_end and not target_all_day:
            raise ValueError(
                "converting an all-day event to a timed one requires "
                "start_datetime and end_datetime — there is no defensible "
                "time-of-day to invent"
            )

    def _apply_date_updates(
        self, component, event_data: dict[str, Any]
    ) -> set[ZoneInfo]:
        """Write DTSTART/DTEND onto ``component``, preserving its stored shape.

        Returns the set of zones applied, so the caller can emit the matching
        VTIMEZONE components.

        Two properties of the stored event are inherited when the caller does not
        override them, because reading them from ``event_data`` alone is what made
        updates lossy:

        * **Value type.** ``all_day`` was previously read as
          ``event_data.get("all_day", False)`` independently in each branch, so
          updating an all-day event's start without re-passing ``all_day=True``
          rewrote DTSTART as a naive DATE-TIME — RFC 5545 floating time — and could
          leave DTSTART and DTEND with mismatched value types, which is invalid
          iCalendar.
        * **TZID.** Inherited *per property*: DTSTART and DTEND may legally carry
          different zones, so sharing DTSTART's would silently relocate the end.
        """
        tz_name = event_data.get("timezone", "")
        used_timezones: set[ZoneInfo] = set()

        has_start = "start_datetime" in event_data
        has_end = "end_datetime" in event_data
        if not has_start and not has_end and "all_day" not in event_data:
            return used_timezones

        stored_all_day = self._stored_is_all_day(component, "DTSTART")
        if stored_all_day is None:
            stored_all_day = self._stored_is_all_day(component, "DTEND")

        # Computed once. Falling back to the *stored* type is the fix: absent an
        # explicit `all_day`, the event keeps the shape it already had.
        if "all_day" in event_data:
            target_all_day = bool(event_data["all_day"])
        else:
            target_all_day = bool(stored_all_day)

        # ``stored_all_day is None`` means neither DTSTART nor DTEND gave a
        # definitive type — a VEVENT with no dates at all. There is nothing to
        # flip *from*, so flip validation is deliberately skipped rather than
        # guessing; ``bool(None)`` then treats the target as timed, matching the
        # pre-existing default.
        flipping = stored_all_day is not None and target_all_day != stored_all_day

        if flipping:
            self._validate_all_day_flip(target_all_day, has_start, has_end)
            if not has_start and not has_end:
                # Timed -> all-day with no new datetimes is well defined: take the
                # dates off the stored values. (The converse already raised.)
                self._convert_component_to_all_day(component)
                return used_timezones

        if has_start:
            self._write_date_property(
                component,
                "DTSTART",
                event_data["start_datetime"],
                target_all_day,
                tz_name,
                used_timezones,
            )
        if has_end:
            self._write_date_property(
                component,
                "DTEND",
                event_data["end_datetime"],
                target_all_day,
                tz_name,
                used_timezones,
            )

        if target_all_day:
            # Apply the same zero-length guard the implicit conversion path uses.
            # An explicit ``all_day=True`` with start and end resolving to the same
            # calendar date would otherwise write ``DTEND == DTSTART`` — the very
            # zero-length DATE range this method rejects elsewhere.
            self._clamp_all_day_end(component)

        return used_timezones

    def _write_date_property(
        self,
        component,
        prop: str,
        value: str,
        all_day: bool,
        tz_name: str,
        used_timezones: set[ZoneInfo],
    ) -> None:
        """Write one DATE or DATE-TIME property, inheriting that property's TZID."""
        if all_day:
            component[prop] = vDDDTypes(
                self._parse_caldav_datetime(value, all_day=True)
            )
            return

        inherited_tz = self._stored_tzid(component, prop)
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        try:
            probe = dt.datetime.fromisoformat(normalized)
        except ValueError:
            probe = None

        if probe is not None and probe.tzinfo is not None:
            parsed = self._parse_caldav_datetime(value)
            if not isinstance(parsed, dt.datetime):
                raise TypeError("timed CalDAV value unexpectedly parsed as a date")
            zi = parsed.tzinfo if isinstance(parsed.tzinfo, ZoneInfo) else None
        elif tz_name or inherited_tz:
            parsed, zi = self._parse_event_datetime(
                value, tz_name, inherited_tz=inherited_tz
            )
        else:
            # Enforce the fleet time policy when no explicit or stored TZID can
            # give an otherwise naive value a defensible timezone.
            parsed = self._parse_caldav_datetime(value)
            if not isinstance(parsed, dt.datetime):
                raise TypeError("timed CalDAV value unexpectedly parsed as a date")
            zi = parsed.tzinfo if isinstance(parsed.tzinfo, ZoneInfo) else None

        if zi is not None:
            used_timezones.add(zi)
        component[prop] = vDDDTypes(parsed)

    @staticmethod
    def _clamp_all_day_end(component) -> None:
        """Ensure an all-day ``DTEND`` is at least the day after ``DTSTART``.

        ``DTEND == DTSTART`` is a zero-length DATE range and invalid per RFC 5545.
        Shared by both routes that can produce one: the implicit timed -> all-day
        conversion (``.date()`` on a 09:00-10:00 event collapses both ends onto the
        same day) and an explicit ``all_day=True`` whose supplied start and end
        resolve to the same calendar date. A no-op unless both are DATE values.
        """
        start_value = component.get("DTSTART")
        end_value = component.get("DTEND")
        if start_value is None or end_value is None:
            return
        start_date, end_date = start_value.dt, end_value.dt
        if isinstance(start_date, dt.datetime) or isinstance(end_date, dt.datetime):
            return  # not an all-day pair; nothing to clamp
        if end_date <= start_date:
            component["DTEND"] = vDDDTypes(start_date + dt.timedelta(days=1))

    @classmethod
    def _convert_component_to_all_day(cls, component) -> None:
        """Re-write stored DTSTART/DTEND as DATE values."""
        start_value = component.get("DTSTART")
        if start_value is None:
            return
        start_date = start_value.dt
        start_date = (
            start_date.date() if isinstance(start_date, dt.datetime) else start_date
        )
        component["DTSTART"] = vDDDTypes(start_date)

        end_value = component.get("DTEND")
        if end_value is not None:
            end_date = end_value.dt
            end_date = (
                end_date.date() if isinstance(end_date, dt.datetime) else end_date
            )
            component["DTEND"] = vDDDTypes(end_date)

        cls._clamp_all_day_end(component)

    def _merge_ical_properties(
        self,
        raw_ical: str,
        event_data: dict[str, Any],
        event_uid: str | None = None,
    ) -> str:
        """Merge new event data into existing raw iCal while preserving all properties.

        The event's own ``UID`` is carried through from ``raw_ical`` like any other
        preserved property, so no ``event_uid`` argument is needed. (One used to be
        required solely by the removed rebuild fallback.)

        Raises on any merge failure rather than substituting a synthesised event.
        This previously caught every exception and fell back to
        ``_create_ical_event(event_data, ...)``, which rebuilds the event from the
        *partial update dict* — destroying summary, location, attendees, alarms,
        RRULE and every custom property the caller did not happen to pass, while
        reporting success.
        """
        cal = Calendar.from_ical(raw_ical)

        for component in cal.walk():
            if component.name == "VEVENT":
                # Update only provided properties
                if "title" in event_data:
                    component["SUMMARY"] = event_data["title"]
                if "description" in event_data:
                    component["DESCRIPTION"] = event_data["description"]
                if "location" in event_data:
                    component["LOCATION"] = event_data["location"]
                if "status" in event_data:
                    component["STATUS"] = event_data["status"].upper()
                if "priority" in event_data:
                    component["PRIORITY"] = event_data["priority"]
                if "privacy" in event_data:
                    component["CLASS"] = event_data["privacy"].upper()
                if "url" in event_data:
                    component["URL"] = event_data["url"]

                # Handle categories
                if "categories" in event_data:
                    categories_str = event_data["categories"]
                    if categories_str:
                        component["CATEGORIES"] = [
                            c.strip() for c in categories_str.split(",")
                        ]
                    elif "CATEGORIES" in component:
                        del component["CATEGORIES"]

                # Handle recurrence rule
                if "recurrence_rule" in event_data:
                    rrule_str = event_data["recurrence_rule"]
                    if rrule_str:
                        component["RRULE"] = vRecur.from_ical(rrule_str)
                    elif "RRULE" in component:
                        del component["RRULE"]

                # Handle attendees
                if "attendees" in event_data:
                    attendees_str = event_data["attendees"]
                    # Remove all existing attendees first
                    while "ATTENDEE" in component:
                        del component["ATTENDEE"]
                    if attendees_str:
                        for email in attendees_str.split(","):
                            if email.strip():
                                component.add("attendee", f"mailto:{email.strip()}")

                if "reminders" in event_data:
                    self._sync_valarms_by_index(
                        component, event_data.get("reminders") or []
                    )
                elif "reminder_minutes" in event_data:
                    self._sync_valarms_by_index(component, [])
                    if event_data["reminder_minutes"] > 0:
                        component.add_component(
                            self._build_valarm(
                                {
                                    "description": "Event reminder",
                                    "minutes_before": event_data["reminder_minutes"],
                                }
                            )
                        )

                # Handle dates
                used_timezones = self._apply_date_updates(component, event_data)

                # Update timestamps
                now = dt.datetime.now(dt.UTC)
                component["LAST-MODIFIED"] = vDDDTypes(now)
                component["DTSTAMP"] = vDDDTypes(now)

                # Ensure VTIMEZONE definitions exist for any TZID we just attached.
                existing_tzids = {
                    str(sub.get("TZID", ""))
                    for sub in cal.subcomponents
                    if sub.name == "VTIMEZONE"
                }
                for zi in used_timezones:
                    if str(zi) not in existing_tzids:
                        cal.add_component(Timezone.from_tzinfo(zi))

                break

        return cal.to_ical().decode("utf-8")

    # ============= Helper Methods - Todo iCalendar =============

    def _create_ical_todo(self, todo_data: dict[str, Any], todo_uid: str) -> str:
        """Create iCalendar VTODO content from todo data."""
        cal = Calendar()
        cal.add("prodid", "-//Nextcloud MCP Server//EN")
        cal.add("version", "2.0")

        todo = ICalTodo()
        todo.add("uid", todo_uid)
        todo.add("summary", todo_data.get("summary", ""))
        todo.add("description", todo_data.get("description", ""))
        used_timezones: set[ZoneInfo] = set()

        # Status
        status = todo_data.get("status", "NEEDS-ACTION").upper()
        todo.add("status", status)

        # Priority (0-9, 0=undefined)
        priority = todo_data.get("priority", 0)
        todo.add("priority", priority)

        # Percent complete
        percent = todo_data.get("percent_complete", 0)
        todo.add("percent-complete", percent)

        # Due date
        due = todo_data.get("due", "")
        if due:
            due_dt = self._parse_caldav_datetime(due)
            if isinstance(due_dt, dt.datetime) and isinstance(due_dt.tzinfo, ZoneInfo):
                used_timezones.add(due_dt.tzinfo)
            todo.add("due", vDDDTypes(due_dt))

        # Start date
        dtstart = todo_data.get("dtstart", "")
        if dtstart:
            start_dt = self._parse_caldav_datetime(dtstart)
            if isinstance(start_dt, dt.datetime) and isinstance(
                start_dt.tzinfo, ZoneInfo
            ):
                used_timezones.add(start_dt.tzinfo)
            todo.add("dtstart", vDDDTypes(start_dt))

        # Completed timestamp
        completed = todo_data.get("completed", "")
        if completed:
            completed_dt = self._parse_caldav_datetime(completed)
            if isinstance(completed_dt, dt.datetime) and isinstance(
                completed_dt.tzinfo, ZoneInfo
            ):
                used_timezones.add(completed_dt.tzinfo)
            todo.add("completed", vDDDTypes(completed_dt))

        # Categories
        categories = todo_data.get("categories", "")
        if categories:
            todo.add("categories", categories.split(","))

        self._add_reminders_or_legacy_alarm(todo, todo_data, "Todo reminder")

        # Add timestamps
        now = dt.datetime.now(dt.UTC)
        todo.add("created", now)
        todo.add("dtstamp", now)
        todo.add("last-modified", now)

        for zi in used_timezones:
            cal.add_component(Timezone.from_tzinfo(zi))
        cal.add_component(todo)
        return cal.to_ical().decode("utf-8")

    def _parse_ical_todo(self, ical_text: str) -> dict[str, Any] | None:
        """Parse iCalendar text and extract todo data."""
        try:
            cal = Calendar.from_ical(ical_text)
            for component in cal.walk():
                if component.name == "VTODO":
                    todo_data = {
                        "uid": str(component.get("uid", "")),
                        "summary": str(component.get("summary", "")),
                        "description": str(component.get("description", "")),
                        "status": str(component.get("status", "NEEDS-ACTION")),
                        "priority": int(component.get("priority", 0)),
                        "percent_complete": int(component.get("percent-complete", 0)),
                    }

                    # Handle due date
                    due = component.get("due")
                    if due:
                        todo_data["due"] = due.dt.isoformat()

                    # Handle start date
                    dtstart = component.get("dtstart")
                    if dtstart:
                        todo_data["dtstart"] = dtstart.dt.isoformat()

                    # Handle completed date
                    completed = component.get("completed")
                    if completed:
                        todo_data["completed"] = completed.dt.isoformat()

                    # Handle categories
                    categories = component.get("categories")
                    if categories:
                        todo_data["categories"] = self._extract_categories(categories)

                    reminders = self._extract_valarms(component)
                    if reminders:
                        todo_data["reminders"] = reminders

                    return todo_data

            return None

        except Exception as e:
            logger.error("Error parsing iCalendar todo: %s", e)
            return None

    def _merge_ical_todo_properties(
        self, raw_ical: str, todo_data: dict[str, Any], todo_uid: str
    ) -> str:
        """Merge new todo data into existing raw iCal while preserving all properties."""
        try:
            logger.debug(
                "Merging todo properties for %s: %s", todo_uid, list(todo_data.keys())
            )
            cal = Calendar.from_ical(raw_ical)

            for component in cal.walk():
                if component.name == "VTODO":
                    used_timezones: set[ZoneInfo] = set()
                    # Update only provided properties
                    if "summary" in todo_data:
                        component["SUMMARY"] = todo_data["summary"]
                    if "description" in todo_data:
                        component["DESCRIPTION"] = todo_data["description"]
                    if "status" in todo_data:
                        status_value = todo_data["status"].upper()
                        component["STATUS"] = status_value
                        logger.debug("Set STATUS to %s", status_value)
                    if "priority" in todo_data:
                        component["PRIORITY"] = todo_data["priority"]
                    if "percent_complete" in todo_data:
                        percent_value = todo_data["percent_complete"]
                        component["PERCENT-COMPLETE"] = percent_value
                        logger.debug("Set PERCENT-COMPLETE to %s", percent_value)

                    # Handle due date
                    if "due" in todo_data:
                        due_str = todo_data["due"]
                        if due_str:
                            due_dt = self._parse_caldav_datetime(due_str)
                            if isinstance(due_dt, dt.datetime) and isinstance(
                                due_dt.tzinfo, ZoneInfo
                            ):
                                used_timezones.add(due_dt.tzinfo)
                            component["DUE"] = vDDDTypes(due_dt)
                            logger.debug("Set DUE to %s", due_dt)

                    # Handle start date
                    if "dtstart" in todo_data:
                        dtstart_str = todo_data["dtstart"]
                        if dtstart_str:
                            dtstart_dt = self._parse_caldav_datetime(dtstart_str)
                            if isinstance(dtstart_dt, dt.datetime) and isinstance(
                                dtstart_dt.tzinfo, ZoneInfo
                            ):
                                used_timezones.add(dtstart_dt.tzinfo)
                            component["DTSTART"] = vDDDTypes(dtstart_dt)
                            logger.debug("Set DTSTART to %s", dtstart_dt)

                    # Handle completed date
                    if "completed" in todo_data:
                        completed_str = todo_data["completed"]
                        if completed_str:
                            completed_dt = self._parse_caldav_datetime(completed_str)
                            if isinstance(completed_dt, dt.datetime) and isinstance(
                                completed_dt.tzinfo, ZoneInfo
                            ):
                                used_timezones.add(completed_dt.tzinfo)
                            component["COMPLETED"] = vDDDTypes(completed_dt)
                            logger.debug("Set COMPLETED to %s", completed_dt)

                    # Handle categories
                    if "categories" in todo_data:
                        categories_str = todo_data["categories"]
                        if categories_str:
                            component["CATEGORIES"] = [
                                c.strip() for c in categories_str.split(",")
                            ]
                            logger.debug("Set CATEGORIES to %s", categories_str)

                    if "reminders" in todo_data:
                        self._sync_valarms_by_index(
                            component, todo_data.get("reminders") or []
                        )
                    elif "reminder_minutes" in todo_data:
                        self._sync_valarms_by_index(component, [])
                        if todo_data["reminder_minutes"] > 0:
                            component.add_component(
                                self._build_valarm(
                                    {
                                        "description": "Todo reminder",
                                        "minutes_before": todo_data["reminder_minutes"],
                                    }
                                )
                            )

                    # Update timestamps
                    now = dt.datetime.now(dt.UTC)
                    component["LAST-MODIFIED"] = vDDDTypes(now)
                    component["DTSTAMP"] = vDDDTypes(now)

                    existing_tzids = {
                        str(sub.get("TZID", ""))
                        for sub in cal.subcomponents
                        if sub.name == "VTIMEZONE"
                    }
                    for zi in used_timezones:
                        if str(zi) not in existing_tzids:
                            cal.add_component(Timezone.from_tzinfo(zi))

                    break

            return cal.to_ical().decode("utf-8")

        except Exception as e:
            logger.error("Error merging iCal todo properties: %s", e)
            return self._create_ical_todo(todo_data, todo_uid)

    # ============= Helper Methods - Filtering =============

    def _extract_categories(self, categories_obj) -> str:
        """Extract categories from icalendar object to string."""
        if not categories_obj:
            return ""

        try:
            if hasattr(categories_obj, "cats"):
                # Handle Categories object with cats attribute
                return ", ".join(str(cat) for cat in categories_obj.cats)
            elif hasattr(categories_obj, "__iter__") and not isinstance(
                categories_obj, str
            ):
                # Handle list of vCategory objects or strings
                result = []
                for cat in categories_obj:
                    # Try to extract value from vCategory objects using to_ical()
                    if hasattr(cat, "to_ical"):
                        result.append(cat.to_ical().decode("utf-8"))
                    else:
                        result.append(str(cat))
                return ", ".join(result)
            else:
                # Handle single category string or object
                if hasattr(categories_obj, "to_ical"):
                    return categories_obj.to_ical().decode("utf-8")
                return str(categories_obj)
        except Exception as e:
            logger.warning("Error extracting categories: %s", e)
            return str(categories_obj)

    def _apply_event_filters(
        self, events: list[dict[str, Any]], filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Apply advanced filters to event list."""
        return [
            event for event in events if self._event_matches_filters(event, filters)
        ]

    def _event_matches_filters(
        self, event: dict[str, Any], filters: dict[str, Any]
    ) -> bool:
        """Check if an event matches the provided filters."""
        try:
            # Filter by minimum attendees
            if "min_attendees" in filters:
                attendees = event.get("attendees", "")
                attendee_count = len(attendees.split(",")) if attendees else 0
                if attendee_count < filters["min_attendees"]:
                    return False

            # Filter by categories
            if "categories" in filters:
                event_categories = event.get("categories", "").lower()
                required_categories = [cat.lower() for cat in filters["categories"]]
                if not any(cat in event_categories for cat in required_categories):
                    return False

            # Filter by status
            if "status" in filters:
                if event.get("status", "").upper() != filters["status"].upper():
                    return False

            # Filter by title contains
            if "title_contains" in filters:
                title = event.get("title", "").lower()
                search_term = filters["title_contains"].lower()
                if search_term not in title:
                    return False

            # Filter by location contains
            if "location_contains" in filters:
                location = event.get("location", "").lower()
                search_term = filters["location_contains"].lower()
                if search_term not in location:
                    return False

            return True

        except Exception:
            return True

    def _todo_matches_filters(
        self, todo: dict[str, Any], filters: dict[str, Any]
    ) -> bool:
        """Check if a todo matches the provided filters."""
        try:
            if filters.get("include_completed") is False and (
                str(todo.get("status", "")).upper() == "COMPLETED"
                or bool(todo.get("completed"))
            ):
                return False

            # Filter by status
            if "status" in filters:
                if todo.get("status", "").upper() != filters["status"].upper():
                    return False

            # Filter by minimum priority
            if "min_priority" in filters:
                priority = todo.get("priority", 0)
                if priority == 0 or priority > filters["min_priority"]:
                    return False

            # Filter by categories
            if "categories" in filters:
                todo_categories = todo.get("categories", "").lower()
                required_categories = [cat.lower() for cat in filters["categories"]]
                if not any(cat in todo_categories for cat in required_categories):
                    return False

            # Filter by summary contains
            if "summary_contains" in filters:
                summary = todo.get("summary", "").lower()
                search_term = filters["summary_contains"].lower()
                if search_term not in summary:
                    return False

            return True

        except Exception:
            return True

    # ============= Legacy Methods (for backward compatibility) =============

    async def bulk_update_events(
        self, filter_criteria: dict[str, Any], update_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Bulk update events matching filter criteria."""
        await self._ensure_calendar_home()
        try:
            start_datetime = None
            end_datetime = None
            if "start_date" in filter_criteria and filter_criteria["start_date"]:
                start_datetime = dt.datetime.fromisoformat(
                    filter_criteria["start_date"]
                )
            if "end_date" in filter_criteria and filter_criteria["end_date"]:
                end_datetime = dt.datetime.fromisoformat(filter_criteria["end_date"])

            events = await self.search_events_across_calendars(
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                filters=filter_criteria,
            )

            updated_count = 0
            failed_count = 0
            results = []

            for event in events:
                try:
                    try:
                        event_etag = require_strong_entity_tag(
                            event.get("etag"),
                            operation=f"bulk update event {event['uid']}",
                        )
                    except StrongEntityTagError as exc:
                        raise CalendarEtagUnavailableError(str(exc)) from exc
                    await self.update_event(
                        event["calendar_name"],
                        event["uid"],
                        update_data,
                        event_etag,
                    )
                    updated_count += 1
                    results.append(
                        {
                            "uid": event["uid"],
                            "status": "updated",
                            "title": event.get("title", ""),
                        }
                    )
                except Exception as e:
                    failed_count += 1
                    results.append(
                        {
                            "uid": event["uid"],
                            "status": "failed",
                            "error": str(e),
                            "title": event.get("title", ""),
                        }
                    )

            return {
                "total_found": len(events),
                "updated_count": updated_count,
                "failed_count": failed_count,
                "results": results,
            }

        except Exception as e:
            logger.error("Error in bulk update: %s", e)
            raise

    async def find_availability(
        self,
        duration_minutes: int,
        attendees: list[str] | None = None,
        start_datetime: dt.datetime | None = None,
        end_datetime: dt.datetime | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Find available time slots for scheduling.

        Note: This is a simplified stub that returns empty list.
        Full implementation would require complex free/busy analysis.
        """
        logger.warning("find_availability is not fully implemented with AsyncDavClient")
        return []
