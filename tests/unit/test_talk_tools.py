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
from mcp.server.fastmcp.exceptions import ToolError

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
        remove_reaction=AsyncMock(),
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
        "talk_remove_reaction": ["talk.write"],
    }

    for name, scopes in expected_scopes.items():
        assert name in tools
        assert getattr(tools[name].fn, "_required_scopes") == scopes


async def test_create_group_passes_requested_invite(tools, talk_client):
    talk_client.create_conversation.return_value = _conversation()

    result = await tools["talk_create_conversation"].fn(
        ctx=_context(),
        room_type=2,
        room_name="Operations",
        invite="alice",
    )

    assert result.conversation.token == "room123"
    talk_client.create_conversation.assert_awaited_once_with(
        room_type=2,
        room_name="Operations",
        invite="alice",
    )
    talk_client.add_participant.assert_not_awaited()


async def test_create_direct_conversation_passes_invite_at_creation(tools, talk_client):
    talk_client.create_conversation.return_value = _conversation(
        room_type=1, name="Alice"
    )

    await tools["talk_create_conversation"].fn(
        ctx=_context(),
        room_type=1,
        room_name=None,
        invite="alice",
    )

    talk_client.create_conversation.assert_awaited_once_with(
        room_type=1,
        room_name=None,
        invite="alice",
    )
    talk_client.add_participant.assert_not_awaited()


@pytest.mark.parametrize(
    ("room_type", "room_name", "invite", "message"),
    [
        (1, "", None, "invite is required"),
        (2, "  ", None, "room_name is required"),
    ],
)
async def test_create_conversation_rejects_invalid_contract(
    tools, talk_client, room_type, room_name, invite, message
):
    with pytest.raises(ToolError, match=message):
        await tools["talk_create_conversation"].fn(
            ctx=_context(),
            room_type=room_type,
            room_name=room_name,
            invite=invite,
        )

    talk_client.create_conversation.assert_not_awaited()


async def test_add_participant_dispatches_source_and_participant(tools, talk_client):
    result = await tools["talk_add_participant"].fn(
        ctx=_context(),
        token="room123",
        participant="alice",
        source="users",
    )

    assert result.participant == "alice"
    assert result.source == "users"
    talk_client.add_participant.assert_awaited_once_with(
        "room123", "alice", source="users"
    )


async def test_add_participant_rejects_blank_participant(tools, talk_client):
    with pytest.raises(ToolError, match="participant must not be empty"):
        await tools["talk_add_participant"].fn(
            ctx=_context(),
            token="room123",
            participant="  ",
            source="users",
        )

    talk_client.add_participant.assert_not_awaited()


async def test_reaction_tools_preserve_actor_projection(tools, talk_client):
    emoji = "\N{THUMBS UP SIGN}"
    projection = {emoji: [_actor()]}
    talk_client.list_reactions.return_value = projection
    talk_client.add_reaction.return_value = projection
    talk_client.remove_reaction.return_value = projection

    listed = await tools["talk_list_reactions"].fn(
        ctx=_context(), token="room123", message_id=42, reaction=emoji
    )
    added = await tools["talk_react"].fn(
        ctx=_context(), token="room123", message_id=42, reaction=emoji
    )
    removed = await tools["talk_remove_reaction"].fn(
        ctx=_context(), token="room123", message_id=42, reaction=emoji
    )

    assert listed.reactions[emoji][0].actorId == "alice"
    assert added.reactions[emoji][0].actorId == "alice"
    assert removed.reactions[emoji][0].actorId == "alice"
    assert listed.distinct_emoji == 1
    talk_client.list_reactions.assert_awaited_once_with("room123", 42, reaction=emoji)
    talk_client.add_reaction.assert_awaited_once_with("room123", 42, emoji)
    talk_client.remove_reaction.assert_awaited_once_with("room123", 42, emoji)
