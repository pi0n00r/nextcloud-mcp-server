"""Unit tests for WebDAV write-conflict classification.

``_write_conflict_result`` turns a failing status into an actionable result.
These tests pin the 507 (quota) row added alongside 412/423, and the rule that
the server's own ``s:exception``/``s:message`` explanation is carried through
rather than discarded in favour of our generic wording.
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
from httpx import HTTPStatusError, Request, Response

from nextcloud_mcp_server.client.dav_errors import dav_error_from_status_error
from nextcloud_mcp_server.client.webdav import (
    _dav_detail_text,
    _write_conflict_result,
)

pytestmark = pytest.mark.unit


def _dav_error(status_code: int, exception: str, message: str):
    body = (
        '<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
        f"<s:exception>{exception}</s:exception>"
        f"<s:message>{message}</s:message>"
        "</d:error>"
    ).encode()
    request = Request("PUT", "https://cloud.example.org/remote.php/dav/files/a/x.txt")
    response = Response(status_code, content=body, request=request)
    return dav_error_from_status_error(
        HTTPStatusError("boom", request=request, response=response)
    )


class TestInsufficientStorage:
    def test_507_is_classified_as_quota_exhaustion(self):
        result = _write_conflict_result(None, 507, "/x.txt")
        assert result is not None
        assert result["error_kind"] == "insufficient_storage"
        assert result["status_code"] == 507

    def test_507_message_says_retrying_will_not_help(self):
        """Quota is not transient; a caller that retries just fails slower."""
        result = _write_conflict_result(None, 507, "/x.txt")
        assert result is not None
        assert "retrying will not help" in result["message"]


class TestServerDetailPassthrough:
    @pytest.mark.parametrize("status_code", [412, 423, 507])
    def test_detail_is_appended_to_the_message(self, status_code):
        result = _write_conflict_result(
            "etag-1",
            status_code,
            "/x.txt",
            server_detail="Sabre\\DAV\\Exception\\Locked: write locked",
        )
        assert result is not None
        assert result["server_detail"] == "Sabre\\DAV\\Exception\\Locked: write locked"
        assert "[server said: " in result["message"]

    def test_absent_detail_leaves_the_message_untouched(self):
        result = _write_conflict_result("etag-1", 412, "/x.txt")
        assert result is not None
        assert "server said" not in result["message"]
        assert "server_detail" not in result

    def test_detail_is_extracted_from_a_typed_dav_error(self):
        error = _dav_error(
            423, "Sabre\\DAV\\Exception\\Locked", "File is currently write locked"
        )
        assert (
            _dav_detail_text(error)
            == "Sabre\\DAV\\Exception\\Locked: File is currently write locked"
        )

    def test_plain_http_error_yields_no_detail(self):
        request = Request("PUT", "https://cloud.example.org/remote.php/dav/f/x.txt")
        error = HTTPStatusError(
            "boom", request=request, response=Response(500, request=request)
        )
        assert _dav_detail_text(error) is None


class TestUnrelatedStatusesAreNotClaimed:
    @pytest.mark.parametrize("status_code", [200, 404, 500])
    def test_returns_none_so_the_caller_reraises(self, status_code):
        assert _write_conflict_result(None, status_code, "/x.txt") is None
