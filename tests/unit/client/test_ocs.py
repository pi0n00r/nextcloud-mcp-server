"""Unit tests for the OCS envelope helpers.

Two traps are pinned here: the v1 envelope (HTTP 200 regardless of outcome,
real status in ``meta.statuscode``, success ``100``), and status ``997``, which
means "unauthenticated *or* missing OCS-APIRequest header" rather than a
server-side fault.
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

from nextcloud_mcp_server.client.ocs import (
    OCS_API_REQUEST_HEADER,
    OCSAuthenticationError,
    OCSError,
    ocs_data,
    raise_for_ocs_status,
)

pytestmark = pytest.mark.unit


def _envelope(statuscode, data=None, message="OK"):
    return {
        "ocs": {"meta": {"statuscode": statuscode, "message": message}, "data": data}
    }


class TestRaiseForOcsStatus:
    @pytest.mark.parametrize("statuscode", [100, 200])
    def test_both_success_codes_pass(self, statuscode):
        """100 is v1's success code, 200 is v2's. A helper that only knew one
        would either reject every v1 success or accept every v1 failure."""
        raise_for_ocs_status(_envelope(statuscode, data={"id": 1}))

    def test_failure_raises_with_server_message(self):
        with pytest.raises(OCSError, match="Wrong path") as excinfo:
            raise_for_ocs_status(
                _envelope(404, message="Wrong path, file/folder doesn't exist")
            )
        assert excinfo.value.status_code == 404
        assert excinfo.value.ocs_message == "Wrong path, file/folder doesn't exist"

    def test_997_names_both_causes(self):
        with pytest.raises(OCSAuthenticationError) as excinfo:
            raise_for_ocs_status(
                _envelope(997, message="Current user is not logged in")
            )
        text = str(excinfo.value)
        assert "OCS-APIRequest" in text
        assert "unauthenticated" in text
        assert excinfo.value.status_code == 997
        # Callers written against the pre-existing RuntimeError still catch it.
        assert isinstance(excinfo.value, OCSError)
        assert isinstance(excinfo.value, RuntimeError)

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"ocs": None},
            {"ocs": {"meta": None, "data": []}},
            {"ocs": {"data": []}},
            {"ocs": {"meta": {"status": "ok"}, "data": []}},
            "not a dict",
        ],
    )
    def test_missing_statuscode_fails_closed(self, payload):
        with pytest.raises(OCSError, match="malformed OCS envelope"):
            raise_for_ocs_status(payload)

    @pytest.mark.parametrize("statuscode", ["200", "ok", None, 200.0, True])
    def test_non_integer_statuscode_fails_closed(self, statuscode):
        with pytest.raises(OCSError, match="must be an integer"):
            raise_for_ocs_status(
                {"ocs": {"meta": {"statuscode": statuscode}, "data": []}}
            )


class TestOcsData:
    def test_returns_data_on_success(self):
        assert ocs_data(_envelope(200, data={"id": 7})) == {"id": 7}

    def test_returns_falsy_data_unchanged(self):
        """An empty list is a legitimate result (no shares); it must not be
        confused with a missing payload."""
        assert ocs_data(_envelope(200, data=[])) == []

    def test_raises_before_returning_on_failure(self):
        with pytest.raises(OCSError, match="code 403"):
            ocs_data(_envelope(403, message="Forbidden"))

    def test_missing_data_key_raises(self):
        with pytest.raises(OCSError, match="no ocs.data payload"):
            ocs_data({"ocs": {"meta": {"statuscode": 200}}})

    def test_context_appears_in_message(self):
        with pytest.raises(OCSError, match="OCS create_share error"):
            ocs_data(_envelope(400, message="bad"), context="OCS create_share")


def test_header_constant_is_the_value_nextcloud_requires():
    assert OCS_API_REQUEST_HEADER == {"OCS-APIRequest": "true"}
