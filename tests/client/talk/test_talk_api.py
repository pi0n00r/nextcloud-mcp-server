"""Unit tests for the Nextcloud Talk (spreed) HTTP client."""

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

import logging

import httpx
import pytest

from nextcloud_mcp_server.client.talk import (
    TalkClient,
    _validate_message_id,
    _validate_token,
)
from nextcloud_mcp_server.models.talk import (
    TalkConversation,
    TalkMessage,
    TalkParticipant,
)
from tests.client.conftest import (
    create_mock_error_response,
    create_mock_response,
    create_mock_talk_message_response,
    create_mock_talk_room_response,
)

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.unit


# Token validation


@pytest.mark.parametrize(
    "bad_token",
    [
        "",
        "../foo",
        "a/b",
        "a b",
        "a.b",
        "a-b",
        "token!",
    ],
)
def test_validate_token_rejects_invalid(bad_token):
    """_validate_token rejects anything outside the alphanumeric whitelist."""
    with pytest.raises(ValueError, match="Invalid Talk conversation token"):
        _validate_token(bad_token)


@pytest.mark.parametrize("good_token", ["a1b2c3d4", "ABC123", "abcdef", "1"])
def test_validate_token_accepts_valid(good_token):
    """Real spreed tokens — short alphanumeric strings — pass through."""
    _validate_token(good_token)


async def test_talk_get_conversation_rejects_path_traversal(mocker):
    """Pathological tokens never reach the HTTP layer."""
    mock_make_request = mocker.patch.object(TalkClient, "_make_request")

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    with pytest.raises(ValueError, match="Invalid Talk conversation token"):
        await client.get_conversation("../etc/passwd")

    mock_make_request.assert_not_called()


# Conversation tests


async def test_talk_list_conversations(mocker):
    """list_conversations parses the OCS-wrapped room list."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={
            "ocs": {
                "meta": {"status": "ok"},
                "data": [
                    {
                        "id": 1,
                        "token": "abc",
                        "type": 2,
                        "name": "Group 1",
                        "displayName": "Group 1",
                        "lastMessage": [],
                    },
                    {
                        "id": 2,
                        "token": "def",
                        "type": 3,
                        "name": "Public Room",
                        "displayName": "Public Room",
                        "lastMessage": [],
                    },
                ],
            }
        },
    )

    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    rooms = await client.list_conversations()

    assert len(rooms) == 2
    assert all(isinstance(r, TalkConversation) for r in rooms)
    assert rooms[0].token == "abc"
    assert rooms[1].room_type == 3

    mock_make_request.assert_called_once()
    call_args = mock_make_request.call_args
    assert call_args[0][0] == "GET"
    assert call_args[0][1] == "/ocs/v2.php/apps/spreed/api/v4/room"
    # noStatusUpdate defaults to True
    assert call_args[1]["params"]["noStatusUpdate"] == 1


async def test_talk_list_conversations_with_modified_since(mocker):
    """modifiedSince and includeStatus are forwarded as query params."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    await client.list_conversations(modified_since=1700000000, include_status=True)

    params = mock_make_request.call_args[1]["params"]
    assert params["modifiedSince"] == 1700000000
    assert params["includeStatus"] == 1


async def test_talk_get_conversation(mocker):
    """get_conversation parses the OCS-wrapped room object."""
    mock_response = create_mock_talk_room_response(
        conversation_id=42, token="xyz", name="My Room"
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    room = await client.get_conversation("xyz")

    assert isinstance(room, TalkConversation)
    assert room.id == 42
    assert room.token == "xyz"
    assert room.name == "My Room"

    assert mock_make_request.call_args[0][0] == "GET"
    assert "/api/v4/room/xyz" in mock_make_request.call_args[0][1]


async def test_talk_get_conversation_not_found(mocker):
    """A 404 from spreed propagates as HTTPStatusError."""
    error_response = create_mock_error_response(404, "Conversation not found")
    mock_make_request = mocker.patch.object(TalkClient, "_make_request")
    mock_make_request.side_effect = httpx.HTTPStatusError(
        "404 Not Found",
        request=httpx.Request("GET", "http://test.local"),
        response=error_response,
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await client.get_conversation("nope")
    assert excinfo.value.response.status_code == 404


async def test_talk_create_conversation(mocker):
    """create_conversation builds the right POST body."""
    mock_response = create_mock_talk_room_response(
        conversation_id=10, token="new", name="New Room"
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    room = await client.create_conversation(room_name="New Room")

    assert room.token == "new"
    call_args = mock_make_request.call_args
    assert call_args[0][0] == "POST"
    assert call_args[1]["json"] == {"roomType": 2, "roomName": "New Room"}


@pytest.mark.parametrize("room_type", [0, 4, 99])
async def test_talk_create_conversation_rejects_unsupported_room_type(
    mocker, room_type
):
    mock_make_request = mocker.patch.object(TalkClient, "_make_request")
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    with pytest.raises(ValueError, match="room_type must be 1, 2, or 3"):
        await client.create_conversation(room_type=room_type, room_name="Room")

    mock_make_request.assert_not_called()


async def test_talk_create_direct_conversation_requires_and_strips_invite(mocker):
    mock_response = create_mock_talk_room_response(
        conversation_id=11, token="direct", name="Alice"
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    await client.create_conversation(room_type=1, room_name="Alice", invite="  alice  ")

    assert mock_make_request.call_args.kwargs["json"] == {
        "roomType": 1,
        "invite": "alice",
    }


async def test_talk_create_direct_conversation_rejects_missing_invite(mocker):
    mock_make_request = mocker.patch.object(TalkClient, "_make_request")
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    with pytest.raises(ValueError, match="invite is required"):
        await client.create_conversation(room_type=1, room_name="")

    mock_make_request.assert_not_called()


async def test_talk_create_conversation_rejects_long_room_name(mocker):
    mock_make_request = mocker.patch.object(TalkClient, "_make_request")
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    with pytest.raises(ValueError, match="must not exceed 255 characters"):
        await client.create_conversation(room_name="x" * 256)

    mock_make_request.assert_not_called()


async def test_talk_delete_conversation(mocker):
    """delete_conversation issues DELETE on the room URL."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    await client.delete_conversation("doomed")

    call_args = mock_make_request.call_args
    assert call_args[0][0] == "DELETE"
    assert "/api/v4/room/doomed" in call_args[0][1]


# Chat tests


async def test_talk_get_messages(mocker):
    """get_messages parses the OCS-wrapped message list."""
    mock_response = create_mock_response(
        status_code=200,
        headers={"X-Chat-Last-Given": "100"},
        json_data={
            "ocs": {
                "meta": {"status": "ok"},
                "data": [
                    {
                        "id": 101,
                        "token": "abc",
                        "actorType": "users",
                        "actorId": "alice",
                        "actorDisplayName": "Alice",
                        "timestamp": 1700000000,
                        "messageType": "comment",
                        "message": "Hi",
                    },
                    {
                        "id": 102,
                        "token": "abc",
                        "actorType": "users",
                        "actorId": "bob",
                        "actorDisplayName": "Bob",
                        "timestamp": 1700000010,
                        "messageType": "comment",
                        "message": "Hello",
                    },
                ],
            }
        },
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    messages, last_given = await client.get_messages("abc", limit=20)

    assert len(messages) == 2
    assert all(isinstance(m, TalkMessage) for m in messages)
    assert messages[0].id == 101
    assert messages[1].actorId == "bob"
    assert last_given == 100

    call_args = mock_make_request.call_args
    assert call_args[0][0] == "GET"
    assert "/chat/abc" in call_args[0][1]
    params = call_args[1]["params"]
    assert params["limit"] == 20
    # Defaults: not look_into_future, not set_read_marker
    assert params["lookIntoFuture"] == 0
    assert params["setReadMarker"] == 0


async def test_talk_get_messages_pagination_cursor(mocker):
    """last_known_message_id is forwarded as the spreed cursor param."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    messages, last_given = await client.get_messages(
        "abc", last_known_message_id=500, include_last_known=True
    )

    assert messages == []
    assert last_given is None  # No header on this response

    params = mock_make_request.call_args[1]["params"]
    assert params["lastKnownMessageId"] == 500
    assert params["includeLastKnown"] == 1


async def test_talk_get_messages_invalid_last_given_header(mocker, caplog):
    """A non-numeric X-Chat-Last-Given falls back to None and logs a warning."""
    mock_response = create_mock_response(
        status_code=200,
        headers={"X-Chat-Last-Given": "not-a-number"},
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mocker.patch.object(TalkClient, "_make_request", return_value=mock_response)

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    with caplog.at_level(logging.WARNING, logger="nextcloud_mcp_server.client.talk"):
        messages, last_given = await client.get_messages("abc")

    assert messages == []
    assert last_given is None
    assert any(
        "Invalid X-Chat-Last-Given" in record.message for record in caplog.records
    ), "Expected a warning log for the malformed header"


async def test_talk_send_message(mocker):
    """send_message posts the message text and parses the response."""
    mock_response = create_mock_talk_message_response(
        message_id=999, token="abc", text="Posted from MCP"
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    posted = await client.send_message("abc", "Posted from MCP")

    assert isinstance(posted, TalkMessage)
    assert posted.id == 999
    assert posted.message == "Posted from MCP"

    call_args = mock_make_request.call_args
    assert call_args[0][0] == "POST"
    assert "/chat/abc" in call_args[0][1]
    body = call_args[1]["json"]
    assert body["message"] == "Posted from MCP"
    # Optional fields are not sent unless explicitly set
    assert "replyTo" not in body
    assert "referenceId" not in body
    assert "silent" not in body


async def test_talk_send_message_with_reference_id_and_reply(mocker):
    """send_message forwards reply_to, reference_id, silent."""
    mock_response = create_mock_talk_message_response(
        message_id=1000, token="abc", text="A reply"
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    await client.send_message(
        "abc",
        "A reply",
        reply_to=999,
        reference_id="dedupe-token",
        silent=True,
    )

    body = mock_make_request.call_args[1]["json"]
    assert body == {
        "message": "A reply",
        "replyTo": 999,
        "referenceId": "dedupe-token",
        "silent": True,
    }


async def test_talk_mark_as_read_no_message(mocker):
    """mark_as_read with no message ID sends no body (json=None)."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    await client.mark_as_read("abc")

    call_args = mock_make_request.call_args
    assert call_args[0][0] == "POST"
    assert "/chat/abc/read" in call_args[0][1]
    # Empty body is sent as ``json=None`` so httpx skips both the body and
    # the ``Content-Type: application/json`` header for this bodyless POST.
    assert call_args[1]["json"] is None


async def test_talk_mark_as_read_with_message(mocker):
    """mark_as_read sends lastReadMessage when provided."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    await client.mark_as_read("abc", last_read_message=500)

    assert mock_make_request.call_args[1]["json"] == {"lastReadMessage": 500}


# Participant tests


async def test_talk_list_participants(mocker):
    """list_participants parses the OCS-wrapped participant list."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={
            "ocs": {
                "meta": {"status": "ok"},
                "data": [
                    {
                        "attendeeId": 1,
                        "actorType": "users",
                        "actorId": "alice",
                        "displayName": "Alice",
                        "participantType": 1,
                        "inCall": 0,
                        "lastPing": 1700000000,
                    },
                    {
                        "attendeeId": 2,
                        "actorType": "users",
                        "actorId": "bob",
                        "displayName": "Bob",
                        "participantType": 3,
                        "inCall": 0,
                        "lastPing": 1700000000,
                    },
                ],
            }
        },
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    participants = await client.list_participants("abc")

    assert len(participants) == 2
    assert all(isinstance(p, TalkParticipant) for p in participants)
    assert participants[0].actorId == "alice"
    assert participants[1].participantType == 3

    call_args = mock_make_request.call_args
    assert call_args[0][0] == "GET"
    assert "/api/v4/room/abc/participants" in call_args[0][1]
    # Default: include_status off → no includeStatus param
    assert "includeStatus" not in call_args[1].get("params", {})


async def test_talk_list_participants_with_include_status(mocker):
    """include_status=True forwards includeStatus=1 as a query param."""
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )

    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")
    await client.list_participants("abc", include_status=True)

    params = mock_make_request.call_args[1]["params"]
    assert params["includeStatus"] == 1


async def test_talk_add_participant_normalizes_payload(mocker):
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": []}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    await client.add_participant("abc", user_id="  alice  ", source="")

    assert mock_make_request.call_args.args == (
        "POST",
        "/ocs/v2.php/apps/spreed/api/v4/room/abc/participants",
    )
    assert mock_make_request.call_args.kwargs["json"] == {
        "newParticipant": "alice",
        "source": "users",
    }


async def test_talk_add_participant_rejects_blank_user_before_request(mocker):
    mock_make_request = mocker.patch.object(TalkClient, "_make_request")
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    with pytest.raises(ValueError, match="user_id must not be empty"):
        await client.add_participant("abc", user_id="  ")

    mock_make_request.assert_not_called()


# Reaction tests


def _reaction_actor(actor_id="alice"):
    return {
        "actorType": "users",
        "actorId": actor_id,
        "actorDisplayName": actor_id.title(),
        "timestamp": 1700000000,
    }


@pytest.mark.parametrize("message_id", [0, -1])
def test_validate_message_id_rejects_nonpositive(message_id):
    with pytest.raises(ValueError, match="positive integer"):
        _validate_message_id(message_id)


async def test_talk_list_reactions_preserves_filtered_emoji_key(mocker):
    emoji = "\N{THUMBS UP SIGN}"
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": [_reaction_actor()]}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    result = await client.list_reactions("abc", 42, reaction=emoji)

    assert result == {emoji: [_reaction_actor()]}
    assert mock_make_request.call_args.args == (
        "GET",
        "/ocs/v2.php/apps/spreed/api/v1/reaction/abc/42",
    )
    assert mock_make_request.call_args.kwargs["params"] == {"reaction": emoji}


async def test_talk_list_reactions_accepts_grouped_payload(mocker):
    emoji = "\N{THUMBS UP SIGN}"
    mock_response = create_mock_response(
        status_code=200,
        json_data={
            "ocs": {"meta": {"status": "ok"}, "data": {emoji: [_reaction_actor()]}}
        },
    )
    mocker.patch.object(TalkClient, "_make_request", return_value=mock_response)
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    assert await client.list_reactions("abc", 42) == {emoji: [_reaction_actor()]}


@pytest.mark.parametrize(
    ("method_name", "http_method"),
    [("add_reaction", "POST"), ("delete_reaction", "DELETE")],
)
async def test_talk_reaction_mutations_preserve_emoji_key(
    mocker, method_name, http_method
):
    emoji = "\N{THUMBS UP SIGN}"
    mock_response = create_mock_response(
        status_code=200,
        json_data={"ocs": {"meta": {"status": "ok"}, "data": [_reaction_actor()]}},
    )
    mock_make_request = mocker.patch.object(
        TalkClient, "_make_request", return_value=mock_response
    )
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    result = await getattr(client, method_name)("abc", 42, emoji)

    assert result == {emoji: [_reaction_actor()]}
    assert mock_make_request.call_args.args[0] == http_method
    assert mock_make_request.call_args.kwargs["json"] == {"reaction": emoji}


@pytest.mark.parametrize("method_name", ["add_reaction", "delete_reaction"])
async def test_talk_reaction_mutations_reject_blank_emoji(mocker, method_name):
    mock_make_request = mocker.patch.object(TalkClient, "_make_request")
    client = TalkClient(mocker.AsyncMock(spec=httpx.AsyncClient), "testuser")

    with pytest.raises(ValueError, match="reaction emoji must not be empty"):
        await getattr(client, method_name)("abc", 42, "  ")

    mock_make_request.assert_not_called()
