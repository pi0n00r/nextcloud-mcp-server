"""Unit coverage for the agent-facing Talk MCP tools."""

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

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError

from nextcloud_mcp_server.models.talk import TalkConversation
from nextcloud_mcp_server.server.talk import configure_talk_tools

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def basicauth_mode():
    with patch(
        "nextcloud_mcp_server.auth.scope_authorization.get_settings",
        return_value=SimpleNamespace(enable_login_flow=False),
    ):
        yield


@pytest.fixture
def tools():
    mcp = FastMCP("test-talk-tools")
    configure_talk_tools(mcp)
    return {tool.name: tool for tool in mcp._tool_manager.list_tools()}


@pytest.fixture
def talk_client(mocker):
    talk = SimpleNamespace(
        create_conversation=AsyncMock(),
        add_participant=AsyncMock(),
        list_reactions=AsyncMock(),
        add_reaction=AsyncMock(),
        delete_reaction=AsyncMock(),
    )
    client = SimpleNamespace(talk=talk)
    mocker.patch(
        "nextcloud_mcp_server.server.talk.get_client",
        new=AsyncMock(return_value=client),
    )
    return talk


def _context():
    return SimpleNamespace(request_context=SimpleNamespace(access_token=None))


def _conversation(token="room123", room_type=2, name="Operations"):
    return TalkConversation(
        id=7,
        token=token,
        room_type=room_type,
        name=name,
        displayName=name,
    )


def _actor(actor_id="alice"):
    return {
        "actorType": "users",
        "actorId": actor_id,
        "actorDisplayName": actor_id.title(),
        "timestamp": 1700000000,
    }


def test_talk_write_and_reaction_tools_are_registered_with_scopes(tools):
    expected_scopes = {
        "talk_create_conversation": ["talk.write"],
        "talk_add_participant": ["talk.write"],
        "talk_list_reactions": ["talk.read"],
        "talk_react": ["talk.write"],
        "talk_delete_reaction": ["talk.write"],
    }

    for name, scopes in expected_scopes.items():
        assert name in tools
        assert getattr(tools[name].fn, "_required_scopes") == scopes


async def test_create_group_then_adds_requested_participant(tools, talk_client):
    talk_client.create_conversation.return_value = _conversation()

    result = await tools["talk_create_conversation"].fn(
        ctx=_context(),
        room_type=2,
        room_name="Operations",
        invite="  alice  ",
    )

    assert result.conversation.token == "room123"
    talk_client.create_conversation.assert_awaited_once_with(
        room_type=2,
        room_name="Operations",
        invite=None,
    )
    talk_client.add_participant.assert_awaited_once_with("room123", user_id="alice")


async def test_create_direct_conversation_passes_invite_at_creation(tools, talk_client):
    talk_client.create_conversation.return_value = _conversation(
        room_type=1, name="Alice"
    )

    await tools["talk_create_conversation"].fn(
        ctx=_context(),
        room_type=1,
        room_name="Ignored for direct conversations",
        invite="alice",
    )

    talk_client.create_conversation.assert_awaited_once_with(
        room_type=1,
        room_name="Ignored for direct conversations",
        invite="alice",
    )
    talk_client.add_participant.assert_not_awaited()


@pytest.mark.parametrize(
    ("room_type", "room_name", "invite", "message"),
    [
        (99, "Room", None, "room_type must be 1, 2, or 3"),
        (1, "", None, "invite .* is required"),
        (2, "  ", None, "room_name is required"),
        (2, "x" * 256, None, "must not exceed 255 characters"),
    ],
)
async def test_create_conversation_rejects_invalid_contract(
    tools, talk_client, room_type, room_name, invite, message
):
    with pytest.raises(McpError, match=message):
        await tools["talk_create_conversation"].fn(
            ctx=_context(),
            room_type=room_type,
            room_name=room_name,
            invite=invite,
        )

    talk_client.create_conversation.assert_not_awaited()


async def test_add_participant_normalizes_source_and_user(tools, talk_client):
    result = await tools["talk_add_participant"].fn(
        ctx=_context(),
        token="room123",
        user_id="  alice  ",
        source="",
    )

    assert result.user_id == "alice"
    assert result.source == "users"
    talk_client.add_participant.assert_awaited_once_with(
        "room123", user_id="alice", source="users"
    )


async def test_reaction_tools_preserve_actor_projection(tools, talk_client):
    emoji = "\N{THUMBS UP SIGN}"
    projection = {emoji: [_actor()]}
    talk_client.list_reactions.return_value = projection
    talk_client.add_reaction.return_value = projection
    talk_client.delete_reaction.return_value = projection

    listed = await tools["talk_list_reactions"].fn(
        ctx=_context(), token="room123", message_id=42, reaction=emoji
    )
    added = await tools["talk_react"].fn(
        ctx=_context(), token="room123", message_id=42, reaction=emoji
    )
    removed = await tools["talk_delete_reaction"].fn(
        ctx=_context(), token="room123", message_id=42, reaction=emoji
    )

    assert listed.results[emoji][0].actorId == "alice"
    assert added.results[emoji][0].actorId == "alice"
    assert removed.results[emoji][0].actorId == "alice"
    talk_client.list_reactions.assert_awaited_once_with("room123", 42, reaction=emoji)
    talk_client.add_reaction.assert_awaited_once_with("room123", 42, emoji)
    talk_client.delete_reaction.assert_awaited_once_with("room123", 42, emoji)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "message"),
    [
        (
            "talk_list_reactions",
            {"message_id": 0, "reaction": None},
            "message_id must be positive",
        ),
        (
            "talk_react",
            {"message_id": 42, "reaction": "  "},
            "reaction must not be empty",
        ),
        (
            "talk_delete_reaction",
            {"message_id": -1, "reaction": "x"},
            "message_id must be positive",
        ),
    ],
)
async def test_reaction_tools_reject_invalid_arguments(
    tools, talk_client, tool_name, arguments, message
):
    with pytest.raises(McpError, match=message):
        await tools[tool_name].fn(ctx=_context(), token="room123", **arguments)

    talk_client.list_reactions.assert_not_awaited()
    talk_client.add_reaction.assert_not_awaited()
    talk_client.delete_reaction.assert_not_awaited()
