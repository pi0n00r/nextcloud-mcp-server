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

import httpx2
import pytest

from nextcloud_mcp_server.app import (
    _check_nextcloud_dav_health,
    _readiness_cache,
)

pytestmark = pytest.mark.unit


class _Settings:
    nextcloud_host = "https://nextcloud.example"
    nextcloud_username = "user/name"
    nextcloud_password = "secret"


@pytest.fixture
def probe(monkeypatch):
    async def _run(statuses):
        seen = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            status = statuses[len(seen) - 1]
            if isinstance(status, Exception):
                raise status
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

    assert [request.method for request in seen] == ["PROPFIND", "PROPFIND"]
    assert seen[0].url.raw_path.endswith(b"/remote.php/dav/files/user%2Fname/")
    assert seen[1].url.raw_path.endswith(b"/remote.php/dav/calendars/user%2Fname/")
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


async def test_transport_failure_is_stable_and_sanitized(probe):
    _seen, statuses = await probe([httpx2.ConnectError("contains-private-path"), 207])

    assert statuses["nextcloud_files"].detail == "error: unreachable"
    assert "contains-private-path" not in statuses["nextcloud_files"].detail
