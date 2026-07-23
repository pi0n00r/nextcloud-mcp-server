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
  T3 — write_file with if_match returns a structured conflict on 412
  T3b — a transport-added -gzip ETag retries once with the authoritative ETag
  T4 — write_file WITHOUT if_match is create-only
  T5 — chunked MOVE uses destination-aware Overwrite semantics
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import HTTPStatusError, Request, Response

from nextcloud_mcp_server.client.webdav import WebDAVClient


def _make_client():
    """Construct a WebDAVClient with mocked httpx + minimal config."""
    c = WebDAVClient.__new__(WebDAVClient)
    c._client = MagicMock()
    c._client.base_url = "http://test"
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


async def test_T3_write_file_412_returns_etag_conflict():
    """A stale exact ETag returns a structured conflict with the server ETag."""
    c = _make_client()

    async def fake_request(method, url, content=None, headers=None):
        req = Request(method, url)
        resp = Response(412, headers={"etag": '"server-current-etag"'}, request=req)
        raise HTTPStatusError("412", request=req, response=resp)

    c._make_request = fake_request
    result = await c.write_file("/file.txt", b"data", if_match="stale-etag")
    assert result["status_code"] == 412
    assert result["error_kind"] == "precondition_failed"
    assert result["server_etag"] == "server-current-etag"


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
    result = await c.write_file("/file.txt", b"data", if_match="abc123-gzip")
    assert result["status_code"] == 412
    assert result["error_kind"] == "precondition_failed"
    assert result["server_etag"] == "changed-after-read"


async def test_T4_write_file_without_if_match_is_create_only():
    """The default sends If-None-Match: * and never silently overwrites."""
    c = _make_client()
    captured = {}

    async def fake_request(method, url, content=None, headers=None):
        captured["headers"] = headers
        return _mock_response(204)

    c._make_request = fake_request
    await c.write_file("/file.txt", b"data")
    assert captured["headers"]["If-None-Match"] == "*"
    assert "If-Match" not in captured["headers"]


async def test_T4b_write_file_star_is_explicit_force():
    """The explicit force token is passed as an unquoted If-Match wildcard."""
    c = _make_client()
    captured = {}

    async def fake_request(method, url, content=None, headers=None):
        captured["headers"] = headers
        return _mock_response(204)

    c._make_request = fake_request
    await c.write_file("/file.txt", b"data", if_match="*")
    assert captured["headers"]["If-Match"] == "*"
    assert "If-None-Match" not in captured["headers"]


@pytest.mark.parametrize(
    ("if_match", "overwrite", "condition"),
    [
        (None, "F", None),
        ("*", "T", None),
        (
            "destination-etag",
            "T",
            '<http://test/remote.php/dav/files/test-user/large.bin> '
            '(["destination-etag"])',
        ),
    ],
)
async def test_T5_chunked_move_uses_destination_conditions(
    if_match, overwrite, condition
):
    """All three intents are enforced atomically on the final Destination."""
    c = _make_client()
    c.CHUNK_THRESHOLD = 1
    requests = []

    async def fake_request(method, url, content=None, headers=None):
        requests.append((method, url, headers or {}))
        return _mock_response(201 if method == "MKCOL" else 204)

    c._make_request = fake_request
    result = await c.write_file("/large.bin", b"large", if_match=if_match)

    move = next(request for request in requests if request[0] == "MOVE")
    assert move[2]["Overwrite"] == overwrite
    assert "If-Match" not in move[2]
    assert "If-None-Match" not in move[2]
    if condition is None:
        assert "If" not in move[2]
    else:
        assert move[2]["If"] == condition
    assert result["status_code"] == 204


@pytest.mark.parametrize(
    ("etag", "entity_tag"),
    [
        ("destination-etag", '"destination-etag"'),
        ('"destination-etag"', '"destination-etag"'),
        ('W/"destination-etag"', 'W/"destination-etag"'),
    ],
)
async def test_T5b_chunked_exact_etag_encodes_destination_and_quotes_once(
    etag, entity_tag
):
    """Tagged If names the encoded absolute Destination and quotes safely."""
    c = _make_client()
    c.CHUNK_THRESHOLD = 1
    requests = []

    async def fake_request(method, url, content=None, headers=None):
        requests.append((method, url, headers or {}))
        return _mock_response(201 if method == "MKCOL" else 204)

    c._make_request = fake_request
    result = await c.write_file(
        "/Folder #1/report %.bin", b"large", if_match=etag
    )

    move = next(request for request in requests if request[0] == "MOVE")
    destination = (
        "http://test/remote.php/dav/files/test-user/"
        "Folder%20%231/report%20%25.bin"
    )
    assert move[2]["Destination"] == destination
    assert move[2]["Overwrite"] == "T"
    assert move[2]["If"] == f"<{destination}> ([{entity_tag}])"
    assert result["status_code"] == 204


@pytest.mark.parametrize(
    ("if_match", "status", "error_kind"),
    [
        (None, 412, "already_exists"),
        ("*", 412, "missing_destination"),
        ("destination-etag", 412, "precondition_failed"),
        (None, 423, "locked"),
        ("destination-etag", 423, "locked"),
    ],
)
async def test_T5c_chunked_move_returns_structured_conflict(
    if_match, status, error_kind
):
    """Known destination conflicts remain actionable on the final MOVE."""
    c = _make_client()
    c.CHUNK_THRESHOLD = 1

    async def fake_request(method, url, content=None, headers=None):
        if method == "MOVE":
            request = Request(method, url)
            response = Response(status, request=request)
            raise HTTPStatusError(str(status), request=request, response=response)
        return _mock_response(201 if method == "MKCOL" else 204)

    c._make_request = fake_request
    result = await c.write_file("/large.bin", b"large", if_match=if_match)

    assert result["status_code"] == status
    assert result["error_kind"] == error_kind


@pytest.mark.parametrize(
    ("status", "error_kind", "server_etag"),
    [
        (412, "precondition_failed", "server-current-etag"),
        (423, "locked", None),
    ],
)
async def test_T5d_exact_etag_conflict_does_not_retry_or_fallback(
    status, error_kind, server_etag
):
    """A failed atomic MOVE is returned once; no unconditional MOVE follows."""
    c = _make_client()
    c.CHUNK_THRESHOLD = 1
    requests = []

    async def fake_request(method, url, content=None, headers=None):
        requests.append((method, url, headers or {}))
        if method == "MOVE":
            request = Request(method, url)
            response_headers = (
                {"etag": '"server-current-etag"'} if server_etag else {}
            )
            response = Response(
                status,
                headers=response_headers,
                request=request,
            )
            raise HTTPStatusError(str(status), request=request, response=response)
        return _mock_response(201 if method == "MKCOL" else 204)

    c._make_request = fake_request
    result = await c.write_file(
        "/large.bin", b"large", if_match="destination-etag"
    )

    assert [request[0] for request in requests] == ["MKCOL", "PUT", "MOVE"]
    move_headers = requests[-1][2]
    assert move_headers["Overwrite"] == "T"
    assert move_headers["If"].endswith('(["destination-etag"])')
    assert result["status_code"] == status
    assert result["error_kind"] == error_kind
    if server_etag:
        assert result["server_etag"] == server_etag
