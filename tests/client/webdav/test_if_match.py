"""P1.1 — webdav If-Match passthrough + etag surfacing.

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

Covers:
  T1 — read_file returns (content, content_type, etag) tuple; etag stripped of quotes
  T2 — write_file with if_match adds If-Match header; succeeds when server accepts
  T3 — write_file with if_match raises EtagConflictError on 412 with server_etag
  T3b — a transport-added -gzip ETag retries once with the authoritative ETag
  T4 — write_file WITHOUT if_match preserves current behavior (no If-Match header)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import HTTPStatusError, Request, Response

from nextcloud_mcp_server.client.webdav import EtagConflictError, WebDAVClient


def _make_client():
    """Construct a WebDAVClient with mocked httpx + minimal config."""
    c = WebDAVClient.__new__(WebDAVClient)
    c._client = MagicMock()
    c.username = "test-user"
    c._principal_discovered = True
    c._principal_id = "test-user"
    c.CHUNK_THRESHOLD = 1024 * 1024
    c.CHUNK_SIZE = 5 * 1024 * 1024
    c._get_webdav_base_path = lambda: "/remote.php/dav/files/test-user"
    return c


def _mock_response(
    status_code: int = 200, content: bytes = b"", headers: dict | None = None
):
    req = Request("GET", "http://test/")
    resp = Response(status_code, content=content, headers=headers or {}, request=req)
    return resp


async def test_T1_read_file_returns_etag_tuple():
    """read_file returns (content, content_type, etag); etag has quotes stripped."""
    c = _make_client()
    c._make_request = AsyncMock(
        return_value=_mock_response(
            200, b"hello", {"content-type": "text/plain", "etag": '"abc123"'}
        )
    )
    content, content_type, etag = await c.read_file("/file.txt")
    assert content == b"hello"
    assert content_type == "text/plain"
    assert etag == "abc123"  # quotes stripped


async def test_T1b_read_file_etag_absent():
    """read_file returns etag=None when server omits ETag header."""
    c = _make_client()
    c._make_request = AsyncMock(
        return_value=_mock_response(200, b"x", {"content-type": "text/plain"})
    )
    _, _, etag = await c.read_file("/file.txt")
    assert etag is None


async def test_T2_write_file_with_if_match_adds_header():
    """write_file with if_match passes If-Match header on PUT."""
    c = _make_client()
    captured = {}

    async def fake_request(method, url, content=None, headers=None):
        captured["headers"] = headers
        return _mock_response(204, headers={"etag": '"new-etag-xyz"'})

    c._make_request = fake_request
    result = await c.write_file("/file.txt", b"data", if_match="abc123")
    assert "If-Match" in captured["headers"]
    assert captured["headers"]["If-Match"] == '"abc123"'  # quoted per RFC 7232
    assert result["status_code"] == 204
    assert result.get("etag") == "new-etag-xyz"


async def test_T3_write_file_412_raises_etag_conflict():
    """write_file with stale if_match → 412 → EtagConflictError carrying current server etag."""
    c = _make_client()

    async def fake_request(method, url, content=None, headers=None):
        req = Request(method, url)
        resp = Response(412, headers={"etag": '"server-current-etag"'}, request=req)
        raise HTTPStatusError("412", request=req, response=resp)

    c._make_request = fake_request
    with pytest.raises(EtagConflictError) as exc_info:
        await c.write_file("/file.txt", b"data", if_match="stale-etag")
    assert exc_info.value.current_etag == "server-current-etag"


async def test_T3b_gzip_etag_variant_retries_once_with_authoritative_etag():
    """An exact -gzip transport variant retries without weakening If-Match."""
    c = _make_client()
    seen_if_match = []

    async def fake_request(method, url, content=None, headers=None):
        seen_if_match.append(headers["If-Match"])
        if len(seen_if_match) == 1:
            req = Request(method, url)
            resp = Response(412, headers={"etag": '"abc123"'}, request=req)
            raise HTTPStatusError("412", request=req, response=resp)
        return _mock_response(204, headers={"etag": '"new-etag"'})

    c._make_request = fake_request
    result = await c.write_file("/file.txt", b"data", if_match="abc123-gzip")

    assert seen_if_match == ['"abc123-gzip"', '"abc123"']
    assert result == {
        "status_code": 204,
        "bytes_written": 4,
        "etag": "new-etag",
    }


async def test_T3c_gzip_retry_preserves_second_conflict():
    """A changed authoritative ETag after the retry still fails closed."""
    c = _make_client()
    server_etags = iter(("abc123", "changed-after-read"))

    async def fake_request(method, url, content=None, headers=None):
        req = Request(method, url)
        resp = Response(412, headers={"etag": f'"{next(server_etags)}"'}, request=req)
        raise HTTPStatusError("412", request=req, response=resp)

    c._make_request = fake_request
    with pytest.raises(EtagConflictError) as exc_info:
        await c.write_file("/file.txt", b"data", if_match="abc123-gzip")

    assert exc_info.value.current_etag == "changed-after-read"


async def test_T4_write_file_without_if_match_omits_header():
    """write_file without if_match (default) does not add If-Match header — preserves prior behavior."""
    c = _make_client()
    captured = {}

    async def fake_request(method, url, content=None, headers=None):
        captured["headers"] = headers
        return _mock_response(204)

    c._make_request = fake_request
    await c.write_file("/file.txt", b"data")
    assert "If-Match" not in captured["headers"]
