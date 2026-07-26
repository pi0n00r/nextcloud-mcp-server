"""Unit tests for DAV error surfacing.

Sabre/DAV explains a failure in the response body (``s:exception`` /
``s:message``); the HTTP status alone under-specifies it. These tests pin the
parser, the status→type mapping (412/423/507), and the ``_make_request`` hook
that promotes a plain ``HTTPStatusError`` into the typed error — including the
guarantee that the replacement is still catchable as ``HTTPStatusError``.
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

import pytest
from httpx import AsyncClient, HTTPStatusError, Request, Response

from nextcloud_mcp_server.client.base import BaseNextcloudClient
from nextcloud_mcp_server.client.dav_errors import (
    MAX_ERROR_BODY_BYTES,
    DavError,
    DavInsufficientStorage,
    DavLocked,
    DavPreconditionFailed,
    dav_error_from_status_error,
    parse_dav_error,
)

pytestmark = pytest.mark.unit


def _dav_body(exception: str, message: str) -> bytes:
    """Build a Sabre/DAV error document exactly as Nextcloud emits one."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">\n'
        f"  <s:exception>{exception}</s:exception>\n"
        f"  <s:message>{message}</s:message>\n"
        "</d:error>\n"
    ).encode()


def _status_error(status_code: int, body: bytes = b"") -> HTTPStatusError:
    """Build the HTTPStatusError httpx would raise for a failed DAV request."""
    request = Request("PUT", "https://cloud.example.org/remote.php/dav/files/a/x.txt")
    response = Response(status_code, content=body, request=request)
    return HTTPStatusError(f"{status_code} error", request=request, response=response)


class TestParseDavError:
    def test_extracts_exception_and_message(self):
        detail = parse_dav_error(
            _dav_body("Sabre\\DAV\\Exception\\Locked", "File is currently write locked")
        )
        assert detail is not None
        assert detail.exception == "Sabre\\DAV\\Exception\\Locked"
        assert detail.message == "File is currently write locked"
        assert detail.describe() == (
            "Sabre\\DAV\\Exception\\Locked: File is currently write locked"
        )

    def test_message_only_document(self):
        body = (
            '<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
            "<s:message>Quota exceeded</s:message></d:error>"
        )
        detail = parse_dav_error(body)
        assert detail is not None
        assert detail.exception is None
        assert detail.describe() == "Quota exceeded"

    def test_accepts_str_as_well_as_bytes(self):
        detail = parse_dav_error(_dav_body("Sabre\\DAV\\Exception", "boom").decode())
        assert detail is not None
        assert detail.message == "boom"

    @pytest.mark.parametrize(
        "body",
        [
            None,
            b"",
            b"not xml at all",
            b'{"ocs": {"meta": {"statuscode": 404}}}',
            # A multistatus is a valid DAV document but not an error document;
            # interpreting it here would invent failures out of PROPFIND replies.
            b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"></d:multistatus>',
            # Well-formed error envelope carrying neither element.
            b'<d:error xmlns:d="DAV:"></d:error>',
        ],
    )
    def test_non_dav_error_bodies_yield_none(self, body):
        assert parse_dav_error(body) is None

    def test_oversized_body_is_not_parsed(self):
        """A body too large to be an error document is refused, not parsed —
        a failed request must not become unbounded work."""
        padding = b"<!--" + b"x" * (MAX_ERROR_BODY_BYTES + 1) + b"-->"
        body = _dav_body("Sabre\\DAV\\Exception\\Locked", "locked") + padding
        assert parse_dav_error(body) is None

    def test_non_bytes_non_str_input_yields_none(self):
        """Mocked responses hand back sentinels, not bodies; don't parse them."""
        assert parse_dav_error(object()) is None  # type: ignore[arg-type]


class TestStatusMapping:
    @pytest.mark.parametrize(
        ("status_code", "expected_type", "hint_fragment"),
        [
            (412, DavPreconditionFailed, "If-Match ETag no longer matches"),
            (423, DavLocked, "locked by another client"),
            (507, DavInsufficientStorage, "quota is exhausted"),
        ],
    )
    def test_mapped_dav_statuses_get_their_own_type(
        self, status_code, expected_type, hint_fragment
    ):
        error = dav_error_from_status_error(
            _status_error(status_code, _dav_body("Sabre\\DAV\\Exception", "failure"))
        )
        assert isinstance(error, expected_type)
        assert hint_fragment in str(error)
        # Every DAV error stays catchable by pre-existing handlers.
        assert isinstance(error, HTTPStatusError)

    @pytest.mark.parametrize("status_code", [412, 423, 507])
    def test_non_dav_json_status_is_left_as_http_status_error(self, status_code):
        original = _status_error(status_code, b'{"error":"REST failure"}')
        assert dav_error_from_status_error(original) is None
        assert type(original) is HTTPStatusError

    def test_message_carries_method_path_and_server_detail(self):
        error = dav_error_from_status_error(
            _status_error(
                507,
                _dav_body(
                    "Sabre\\DAV\\Exception\\InsufficientStorage", "Quota exceeded"
                ),
            )
        )
        assert error is not None
        text = str(error)
        assert "PUT https://cloud.example.org/remote.php/dav/files/a/x.txt" in text
        assert "Sabre\\DAV\\Exception\\InsufficientStorage: Quota exceeded" in text
        assert error.detail is not None
        assert error.detail.message == "Quota exceeded"

    def test_unmapped_status_with_dav_body_is_a_generic_dav_error(self):
        error = dav_error_from_status_error(
            _status_error(
                403, _dav_body("Sabre\\DAV\\Exception\\Forbidden", "Not permitted")
            )
        )
        assert type(error) is DavError
        assert "Not permitted" in str(error)

    def test_unmapped_status_without_dav_body_is_left_alone(self):
        """OCS returns JSON and has its own envelope — don't claim its errors."""
        assert (
            dav_error_from_status_error(
                _status_error(404, b'{"ocs": {"meta": {"statuscode": 404}}}')
            )
            is None
        )


class _ProbeClient(BaseNextcloudClient):
    """Minimal concrete client so ``_make_request`` can be exercised."""

    app_name = "probe"


class TestMakeRequestPromotion:
    async def test_make_request_raises_typed_dav_error(self, mocker):
        request = Request("PUT", "https://cloud.example.org/remote.php/dav/f/x.txt")
        response = Response(
            423,
            content=_dav_body("Sabre\\DAV\\Exception\\Locked", "write locked"),
            request=request,
        )
        http_client = mocker.AsyncMock(spec=AsyncClient)
        http_client.request = mocker.AsyncMock(return_value=response)

        client = _ProbeClient(http_client, "alice")

        with pytest.raises(DavLocked) as excinfo:
            await client._make_request("PUT", "/remote.php/dav/f/x.txt")

        assert "write locked" in str(excinfo.value)
        assert excinfo.value.response.status_code == 423

    async def test_non_dav_failure_raises_plain_http_status_error(self, mocker):
        request = Request("GET", "https://cloud.example.org/ocs/v2.php/cloud/user")
        response = Response(500, content=b"upstream exploded", request=request)
        http_client = mocker.AsyncMock(spec=AsyncClient)
        http_client.request = mocker.AsyncMock(return_value=response)

        client = _ProbeClient(http_client, "alice")

        with pytest.raises(HTTPStatusError) as excinfo:
            await client._make_request("GET", "/ocs/v2.php/cloud/user")

        assert not isinstance(excinfo.value, DavError)
