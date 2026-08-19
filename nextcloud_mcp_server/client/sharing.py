"""Nextcloud OCS Sharing API client for file/folder sharing operations."""

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
from typing import Any

from nextcloud_mcp_server.models.sharing import (
    SHARE_TYPES_REQUIRING_RECIPIENT,
    ShareType,
)

from .base import BaseNextcloudClient, retry_on_429
from .ocs import ocs_data, raise_for_ocs_status

logger = logging.getLogger(__name__)


class PublicLinkRecipientError(ValueError):
    """A public-link share was given a recipient it cannot address."""


def validate_share_with(share_type: int, share_with: str | None) -> None:
    """Check the ``shareType``/``shareWith`` pairing before it reaches the wire.

    Nextcloud does not reject a public link that carries a recipient: it
    ignores ``shareWith`` and returns a perfectly valid anonymous link, so the
    caller believes it shared with a named user when it published the file to
    anyone holding the URL. That silence is the reason this check exists on the
    client side. The inverse case (a recipient type with no ``shareWith``) does
    fail server-side, but with a generic OCS 400 that says nothing useful.

    Args:
        share_type: OCS ``shareType`` value (see :class:`ShareType`).
        share_with: Recipient identifier, if any.

    Raises:
        ValueError: If a public link carries a recipient, or a recipient-typed
            share is missing one.
    """
    has_recipient = bool(share_with and share_with.strip())

    if share_type == ShareType.PUBLIC_LINK and has_recipient:
        raise PublicLinkRecipientError(
            "shareType 3 (public link) must not carry shareWith: a public link "
            "addresses nobody, and Nextcloud silently ignores the recipient — "
            f"the file would be published to anyone holding the URL, not shared "
            f"with {share_with!r}. Use shareType 0 (user) or 1 (group) to share "
            "with a recipient, or omit shareWith for an anonymous public link."
        )

    if share_type in SHARE_TYPES_REQUIRING_RECIPIENT and not has_recipient:
        raise ValueError(
            f"shareType {share_type} requires a non-empty shareWith recipient "
            "(user id, group id, email, circle id, conversation token, or card "
            "id, depending on the type)"
        )


class SharingClient(BaseNextcloudClient):
    """Client for Nextcloud OCS Sharing API operations."""

    app_name = "sharing"

    #: Every OCS call needs this header or Nextcloud's CSRF check answers 997.
    _OCS_HEADERS = {"OCS-APIRequest": "true", "Accept": "application/json"}

    @retry_on_429
    async def create_share(
        self,
        path: str,
        share_with: str | None = None,
        share_type: int = 0,
        permissions: int = 1,
    ) -> dict[str, Any]:
        """Create a share for a file or folder.

        Args:
            path: Path to file/folder to share (relative to user's files)
            share_with: Optional recipient identifier — user id, group id, email
                address, federated ``user@remote``, circle id, Talk conversation
                token or Deck card id, depending on ``share_type``. Omit this
                only for a public link (``share_type=3``).
            share_type: OCS share type — see :class:`ShareType`. 0=user (default),
                1=group, 4=email, 6=federated, 7=circle, 10=Talk, 12=Deck card.
                Type 3 creates a public link and requires ``share_with`` to be
                omitted.
            permissions: Share permissions:
                - 1 = read
                - 2 = update
                - 4 = create
                - 8 = delete
                - 16 = share
                - 31 = all permissions
                Common combinations: 1 (read-only), 3 (read+update), 15 (read+update+create+delete)

        Returns:
            Share data including share ID

        Raises:
            ValueError: If the ``share_type``/``share_with`` pairing is invalid.
            OCSError: If the OCS envelope reports a failure.
            HTTPStatusError: If the request fails
        """
        validate_share_with(share_type, share_with)

        payload: dict[str, Any] = {
            "path": path,
            "shareType": share_type,
            "permissions": permissions,
        }
        if share_with is not None:
            payload["shareWith"] = share_with

        response = await self._client.post(
            "/ocs/v2.php/apps/files_sharing/api/v1/shares",
            headers=self._OCS_HEADERS,
            data=payload,
        )
        response.raise_for_status()
        data = response.json()

        share_data = ocs_data(data, context="OCS create_share")

        # Handle case where data might be an empty list on error
        if not share_data or (isinstance(share_data, list) and len(share_data) == 0):
            meta = data["ocs"]["meta"]
            ocs_message = meta.get("message", "Unknown error")
            raise RuntimeError(
                f"Share creation failed: {ocs_message} "
                f"(status {meta.get('statuscode')})"
            )

        logger.info(
            "Created share %s: %s -> %s (type=%s, permissions=%s)",
            share_data["id"],
            path,
            share_with,
            share_type,
            permissions,
        )
        return share_data

    @retry_on_429
    async def create_public_link(
        self,
        path: str,
        permissions: int = 1,
        expire_date: str | None = None,
    ) -> dict[str, Any]:
        """Create a public link share (``shareType=3``) for a file or folder.

        Unlike :meth:`create_share`, this targets anonymous public access, so no
        ``shareWith`` recipient is sent. The returned data carries the public
        ``url`` and ``token`` for the link.

        Args:
            path: Path to file/folder to share (relative to the user's files)
            permissions: Share permissions (default: 1 = read-only). See
                :meth:`create_share` for the bit values.
            expire_date: Optional expiry as ``YYYY-MM-DD``. Nextcloud enforces
                public-link expiry at date granularity — the link expires at
                midnight (start of this date) in the owner's timezone.

        Returns:
            Share data including the public ``url`` and ``token``

        Raises:
            HTTPStatusError: If the request fails
            RuntimeError: If the OCS API reports an error
        """
        data: dict[str, Any] = {
            "path": path,
            # No shareWith: a public link addresses nobody, and sending one
            # would be silently ignored by the server.
            "shareType": int(ShareType.PUBLIC_LINK),
            "permissions": permissions,
        }
        if expire_date is not None:
            data["expireDate"] = expire_date

        response = await self._client.post(
            "/ocs/v2.php/apps/files_sharing/api/v1/shares",
            headers=self._OCS_HEADERS,
            data=data,
        )
        response.raise_for_status()
        result = response.json()

        share_data = ocs_data(result, context="OCS create_public_link")

        # An empty list/dict means the share was not created despite an OK code.
        if not share_data or (isinstance(share_data, list) and len(share_data) == 0):
            meta = result["ocs"]["meta"]
            ocs_message = meta.get("message", "Unknown error")
            raise RuntimeError(
                f"Public link creation failed: {ocs_message} "
                f"(status {meta.get('statuscode')})"
            )

        logger.info(
            "Created public link %s: %s (permissions=%s, expire_date=%s)",
            share_data["id"],
            path,
            permissions,
            expire_date,
        )
        return share_data

    @retry_on_429
    async def delete_share(self, share_id: int) -> None:
        """Delete a share by its ID.

        Args:
            share_id: The share ID to delete

        Raises:
            HTTPStatusError: If the request fails
        """
        response = await self._client.delete(
            f"/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}",
            headers=self._OCS_HEADERS,
        )
        response.raise_for_status()
        raise_for_ocs_status(response.json(), context="OCS delete_share")

        logger.info("Deleted share %s", share_id)

    @retry_on_429
    async def get_share(self, share_id: int) -> dict[str, Any]:
        """Get information about a specific share.

        Args:
            share_id: The share ID

        Returns:
            Share data

        Raises:
            HTTPStatusError: If the request fails
        """
        response = await self._client.get(
            f"/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}",
            headers=self._OCS_HEADERS,
        )
        response.raise_for_status()

        share_data = ocs_data(response.json(), context="OCS get_share")
        # The API returns a list with a single share, extract the first element
        if isinstance(share_data, list) and len(share_data) > 0:
            return share_data[0]
        return share_data

    @retry_on_429
    async def list_shares(
        self, path: str | None = None, shared_with_me: bool = False
    ) -> list[dict[str, Any]]:
        """List shares.

        Args:
            path: Optional path to filter shares for a specific file/folder
            shared_with_me: If True, list shares shared with the current user

        Returns:
            List of share data

        Raises:
            HTTPStatusError: If the request fails
        """
        params = {}
        if path:
            params["path"] = path
        if shared_with_me:
            params["shared_with_me"] = "true"

        response = await self._client.get(
            "/ocs/v2.php/apps/files_sharing/api/v1/shares",
            params=params,
            headers=self._OCS_HEADERS,
        )
        response.raise_for_status()

        # Handle both single share and list of shares
        shares_data = ocs_data(response.json(), context="OCS list_shares")
        if isinstance(shares_data, dict):
            return [shares_data]
        return shares_data if shares_data else []

    @retry_on_429
    async def update_share(
        self, share_id: int, permissions: int | None = None
    ) -> dict[str, Any]:
        """Update a share's permissions.

        Args:
            share_id: The share ID to update
            permissions: New permissions value (see create_share for values)

        Returns:
            Updated share data

        Raises:
            HTTPStatusError: If the request fails
        """
        data = {}
        if permissions is not None:
            data["permissions"] = permissions

        response = await self._client.put(
            f"/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}",
            headers=self._OCS_HEADERS,
            data=data,
        )
        response.raise_for_status()

        updated = ocs_data(response.json(), context="OCS update_share")
        logger.info("Updated share %s", share_id)
        return updated
