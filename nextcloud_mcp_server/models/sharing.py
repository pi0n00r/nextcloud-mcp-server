"""Pydantic models for Nextcloud sharing responses."""

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

from enum import IntEnum

from pydantic import Field

from .base import BaseResponse


class ShareType(IntEnum):
    """OCS ``shareType`` constants (``OCP\\Share\\IShare::TYPE_*``).

    The wire format is a bare integer, so the values below are the whole
    contract — a transposed digit silently creates a share of the wrong kind
    rather than failing. Named constants make the call sites readable and give
    validation something to check against.

    Availability varies by server: ``EMAIL``/``FEDERATED``/``CIRCLE``/``TALK``
    each depend on the corresponding app being installed and enabled, so a
    server may reject a type that is nonetheless valid here.
    """

    #: Share with a single user; ``shareWith`` is the user id.
    USER = 0
    #: Share with a group; ``shareWith`` is the group id.
    GROUP = 1
    #: Anonymous public link. Takes **no** ``shareWith`` — see
    #: :func:`nextcloud_mcp_server.client.sharing.validate_share_with`.
    PUBLIC_LINK = 3
    #: Mail share; ``shareWith`` is the recipient address.
    EMAIL = 4
    #: Federated (server-to-server) share; ``shareWith`` is ``user@remote``.
    FEDERATED = 6
    #: Circles app; ``shareWith`` is the circle id.
    CIRCLE = 7
    #: Talk conversation; ``shareWith`` is the conversation token.
    TALK = 10
    TALK_CONVERSATION = 10
    #: Deck card attachment; ``shareWith`` is the card id. Fired by the Deck
    #: UI when a file is attached to a card.
    DECK = 12
    DECK_CARD = 12


#: Share types whose ``shareWith`` names a recipient and is therefore required.
#: ``PUBLIC_LINK`` is deliberately absent: it addresses nobody.
SHARE_TYPES_REQUIRING_RECIPIENT = frozenset(
    {
        ShareType.USER,
        ShareType.GROUP,
        ShareType.EMAIL,
        ShareType.FEDERATED,
        ShareType.CIRCLE,
        ShareType.TALK,
        ShareType.DECK,
    }
)


class PublicDownloadLinkResponse(BaseResponse):
    """Response for a short-lived public download link (OCS ``shareType=3``).

    Lets MCP clients fetch the original binary file out-of-band (via
    ``download_url``) instead of receiving a base64 payload inline, which can
    exceed the client response budget and get truncated.
    """

    path: str = Field(description="Path of the shared file/folder")
    share_id: int = Field(description="OCS share ID (use to delete the link early)")
    url: str = Field(description="Public share page URL (e.g. https://host/s/<token>)")
    download_url: str = Field(
        description="Direct download URL for the original file (url + '/download')"
    )
    token: str | None = Field(
        None, description="Public share token embedded in the URL"
    )
    permissions: int = Field(
        description="Granted permissions (1 = read-only for a download link)"
    )
    expires_at: str | None = Field(
        None,
        description=(
            "Advisory RFC3339 instant the link was requested to expire. NOTE: "
            "Nextcloud enforces public-link expiry at date granularity — a link "
            "expires at 00:00:00 on expireDate in the owner's timezone (the end "
            "of the day before expireDate) — so the link may remain valid until "
            "the end of that day server-side."
        ),
    )
