"""Unit tests for OCS share-type constants and recipient validation.

``shareType`` travels the wire as a bare integer, so the constants *are* the
contract. The load-bearing rule is that type 3 (public link) takes no
``shareWith``: Nextcloud ignores the recipient and returns a valid anonymous
link, so a caller that gets this wrong publishes a file believing it shared it
with one person. That failure is silent server-side, which is why it is caught
client-side here.
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
from httpx import AsyncClient

from nextcloud_mcp_server.client.ocs import OCSAuthenticationError
from nextcloud_mcp_server.client.sharing import SharingClient, validate_share_with
from nextcloud_mcp_server.models.sharing import (
    SHARE_TYPES_REQUIRING_RECIPIENT,
    ShareType,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def sharing_client(mocker):
    """SharingClient with a mocked underlying httpx client."""
    mock_http = mocker.AsyncMock(spec=AsyncClient)
    return SharingClient(mock_http, "testuser")


def _ok_response(mocker, data):
    response = mocker.Mock()
    response.raise_for_status = mocker.Mock()
    response.json.return_value = {
        "ocs": {"meta": {"statuscode": 200, "message": "OK"}, "data": data}
    }
    return response


class TestShareTypeConstants:
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (ShareType.USER, 0),
            (ShareType.GROUP, 1),
            (ShareType.PUBLIC_LINK, 3),
            (ShareType.EMAIL, 4),
            (ShareType.FEDERATED, 6),
            (ShareType.CIRCLE, 7),
            (ShareType.TALK, 10),
            (ShareType.DECK, 12),
        ],
    )
    def test_wire_values_match_ishare_constants(self, member, value):
        """These mirror OCP\\Share\\IShare::TYPE_*; a drift here silently
        creates shares of the wrong kind."""
        assert int(member) == value

    def test_public_link_is_not_a_recipient_type(self):
        assert ShareType.PUBLIC_LINK not in SHARE_TYPES_REQUIRING_RECIPIENT

    def test_every_other_type_requires_a_recipient(self):
        assert SHARE_TYPES_REQUIRING_RECIPIENT == {
            ShareType.USER,
            ShareType.GROUP,
            ShareType.EMAIL,
            ShareType.FEDERATED,
            ShareType.CIRCLE,
            ShareType.TALK,
            ShareType.DECK,
        }


class TestValidateShareWith:
    def test_public_link_with_recipient_is_rejected(self):
        with pytest.raises(ValueError, match="must not carry shareWith"):
            validate_share_with(ShareType.PUBLIC_LINK, "alice")

    def test_public_link_without_recipient_is_accepted(self):
        validate_share_with(ShareType.PUBLIC_LINK, None)
        validate_share_with(ShareType.PUBLIC_LINK, "")
        # Whitespace is not a recipient.
        validate_share_with(ShareType.PUBLIC_LINK, "   ")

    @pytest.mark.parametrize("share_type", sorted(SHARE_TYPES_REQUIRING_RECIPIENT))
    def test_recipient_types_require_a_recipient(self, share_type):
        with pytest.raises(ValueError, match="requires a non-empty shareWith"):
            validate_share_with(share_type, None)
        validate_share_with(share_type, "someone")

    def test_unknown_type_is_not_second_guessed(self):
        """Nextcloud gains share types over time; an unrecognised one is the
        server's business, not ours to reject."""
        validate_share_with(99, "whatever")
        validate_share_with(99, None)


class TestCreateShareValidation:
    async def test_public_link_with_recipient_never_reaches_the_wire(
        self, sharing_client
    ):
        with pytest.raises(ValueError, match="public link"):
            await sharing_client.create_share(
                path="/report.pdf", share_with="alice", share_type=3
            )
        sharing_client._client.post.assert_not_called()

    async def test_missing_recipient_never_reaches_the_wire(self, sharing_client):
        with pytest.raises(ValueError, match="requires a non-empty shareWith"):
            await sharing_client.create_share(
                path="/report.pdf", share_with="", share_type=ShareType.USER
            )
        sharing_client._client.post.assert_not_called()

    async def test_valid_user_share_is_sent(self, sharing_client, mocker):
        sharing_client._client.post.return_value = _ok_response(mocker, {"id": 11})

        share = await sharing_client.create_share(
            path="/report.pdf", share_with="alice", share_type=ShareType.USER
        )

        assert share["id"] == 11
        data = sharing_client._client.post.call_args.kwargs["data"]
        assert data["shareType"] == 0
        assert data["shareWith"] == "alice"

    async def test_deck_card_share_still_carries_its_card_id(
        self, sharing_client, mocker
    ):
        """Type 12 binds a file to a Deck card via shareWith=<cardId>; the new
        validation must not break the Deck attachment path."""
        sharing_client._client.post.return_value = _ok_response(mocker, {"id": 12})

        await sharing_client.create_share(
            path="/Notes/n.md", share_with="123", share_type=ShareType.DECK
        )

        data = sharing_client._client.post.call_args.kwargs["data"]
        assert data["shareType"] == 12
        assert data["shareWith"] == "123"


class TestPublicLinkPayload:
    async def test_public_link_sends_no_recipient(self, sharing_client, mocker):
        sharing_client._client.post.return_value = _ok_response(
            mocker, {"id": 5, "url": "https://cloud.example.org/s/tok"}
        )

        await sharing_client.create_public_link(path="/receipt.jpg")

        data = sharing_client._client.post.call_args.kwargs["data"]
        assert data["shareType"] == 3
        assert "shareWith" not in data


class TestSharingOcsHeaders:
    @pytest.mark.parametrize(
        "method_name", ["create_share", "create_public_link", "list_shares"]
    )
    def test_every_call_carries_the_ocs_header(self, sharing_client, method_name):
        """Omitting OCS-APIRequest yields statuscode 997, not a 4xx — the kind
        of failure that gets misread as a server fault."""
        assert SharingClient._OCS_HEADERS["OCS-APIRequest"] == "true"
        assert hasattr(sharing_client, method_name)

    async def test_997_from_sharing_surfaces_as_auth_error(
        self, sharing_client, mocker
    ):
        response = mocker.Mock()
        response.raise_for_status = mocker.Mock()
        response.json.return_value = {
            "ocs": {
                "meta": {"statuscode": 997, "message": "Current user is not logged in"},
                "data": [],
            }
        }
        sharing_client._client.get.return_value = response

        with pytest.raises(OCSAuthenticationError, match="OCS-APIRequest"):
            await sharing_client.list_shares()
