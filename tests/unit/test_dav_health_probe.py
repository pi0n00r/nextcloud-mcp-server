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

"""Tests for the non-gating Files and CalDAV readiness probes."""

import anyio
import httpx2
import pytest

from nextcloud_mcp_server.app import (
    _check_nextcloud_dav_health,
    _dav_multistatus_result,
    _dav_request,
    _readiness_cache,
)

pytestmark = pytest.mark.unit


class _Settings:
    nextcloud_host = "https://nextcloud.example"
    nextcloud_username = "user/name"
    nextcloud_password = "secret"


def _property_multistatus(property_xml: str) -> bytes:
    """Build one successful property response for discovery fixtures."""
    return (
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:response><d:propstat><d:prop>"
        f"{property_xml}"
        "</d:prop><d:status>HTTP/1.1 200 OK</d:status>"
        "</d:propstat></d:response></d:multistatus>"
    ).encode()


def _resource_multistatus(status: int = 200) -> bytes:
    """Build one resourcetype propstat with the requested status."""
    return (
        '<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop>'
        "<d:resourcetype><d:collection/></d:resourcetype>"
        f"</d:prop><d:status>HTTP/1.1 {status} Result</d:status>"
        "</d:propstat></d:response></d:multistatus>"
    ).encode()


PRINCIPAL_RESPONSE = _property_multistatus(
    "<d:current-user-principal>"
    "<d:href>/remote.php/dav/principals/users/canonical%2Fuser/</d:href>"
    "</d:current-user-principal>"
)
CALENDAR_HOME_RESPONSE = _property_multistatus(
    "<c:calendar-home-set>"
    "<d:href>/remote.php/dav/calendars/canonical-user/</d:href>"
    "</c:calendar-home-set>"
)


@pytest.fixture
def probe(monkeypatch):
    async def _run(statuses, *, calendar_discovery=207):
        seen = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            if len(seen) == 1:
                return httpx2.Response(207, content=PRINCIPAL_RESPONSE)
            if len(seen) == 2:
                if calendar_discovery != 207:
                    return httpx2.Response(calendar_discovery)
                return httpx2.Response(207, content=CALENDAR_HOME_RESPONSE)

            status = statuses[len(seen) - 3]
            if isinstance(status, Exception):
                raise status
            if isinstance(status, tuple):
                outer_status, prop_status = status
                content = (
                    b"" if prop_status is None else _resource_multistatus(prop_status)
                )
                return httpx2.Response(outer_status, content=content)
            if status == 207:
                return httpx2.Response(207, content=_resource_multistatus())
            return httpx2.Response(status)

        real_client = httpx2.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(handler)
            return real_client(*args, **kwargs)

        _readiness_cache.statuses.clear()
        monkeypatch.setattr(
            "nextcloud_mcp_server.app.get_settings", lambda: _Settings()
        )
        monkeypatch.setattr("nextcloud_mcp_server.app.nextcloud_httpx_client", client)
        await _check_nextcloud_dav_health()
        return seen, _readiness_cache.snapshot()

    return _run


async def test_probes_files_and_calendar_roots_without_listing_data(probe):
    seen, statuses = await probe([207, 200])

    assert [request.method for request in seen] == ["PROPFIND"] * 4
    assert seen[2].url.raw_path.endswith(b"/remote.php/dav/files/canonical%2Fuser/")
    assert seen[3].url.raw_path.endswith(b"/remote.php/dav/calendars/canonical-user/")
    assert all(request.headers["depth"] == "0" for request in seen)
    assert statuses["nextcloud_files"].detail == "ok"
    assert statuses["nextcloud_caldav"].detail == "ok"


async def test_reports_storage_and_dependency_failures_without_urls(probe):
    _seen, statuses = await probe([507, 503])

    assert statuses["nextcloud_files"].detail == "error: insufficient_storage"
    assert statuses["nextcloud_caldav"].detail == "error: dependency_unavailable"
    rendered = " ".join(status.detail for status in statuses.values())
    assert "nextcloud.example" not in rendered
    assert "secret" not in rendered


async def test_calendar_discovery_failure_does_not_mask_files(probe):
    seen, statuses = await probe([207], calendar_discovery=503)

    assert [request.method for request in seen] == ["PROPFIND"] * 3
    assert seen[2].url.raw_path.endswith(b"/remote.php/dav/files/canonical%2Fuser/")
    assert statuses["nextcloud_files"].detail == "ok"
    assert statuses["nextcloud_caldav"].detail == "error: dependency_unavailable"


async def test_transport_failure_is_stable_and_sanitized(probe):
    _seen, statuses = await probe([httpx2.ConnectError("contains-private-path"), 207])

    assert statuses["nextcloud_files"].detail == "error: unreachable"
    assert "contains-private-path" not in statuses["nextcloud_files"].detail


def test_multistatus_requires_successful_resourcetype():
    healthy, detail = _dav_multistatus_result(_resource_multistatus(403))

    assert healthy is False
    assert detail == "error: authorization"

    healthy, detail = _dav_multistatus_result(b"")

    assert healthy is False
    assert detail == "error: invalid_multistatus"


def test_multistatus_ignores_unrelated_success_when_resourcetype_fails():
    content = b"""<d:multistatus xmlns:d="DAV:"><d:response>
      <d:propstat><d:prop><d:displayname/></d:prop>
        <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
      <d:propstat><d:prop><d:resourcetype/></d:prop>
        <d:status>HTTP/1.1 403 Forbidden</d:status></d:propstat>
    </d:response></d:multistatus>"""

    healthy, detail = _dav_multistatus_result(content)

    assert healthy is False
    assert detail == "error: authorization"


async def test_request_has_overall_deadline(monkeypatch):
    class SlowClient:
        async def request(self, *_args, **_kwargs):
            await anyio.sleep(1)

    monkeypatch.setattr("nextcloud_mcp_server.app._DAV_PROBE_DEADLINE_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        await _dav_request(
            SlowClient(),
            "PROPFIND",
            "https://nextcloud.example/remote.php/dav/",
            content="<d:propfind/>",
        )
