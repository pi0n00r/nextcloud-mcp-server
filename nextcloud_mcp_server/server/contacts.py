"""MCP tool surface for Nextcloud Contacts (CardDAV).

Unified ``nc_contacts_*`` namespace, 8 ops total. Byte-preserving vCard
substrate underneath every write — no JSON<->vCard round-trip on properties
not being modified.

Ops:
  1. nc_contacts_list_addressbooks
  2. nc_contacts_list_contacts (gains include_vcard / include_etag)
  3. nc_contacts_get_contact NEW (vcard_text + etag + JSON)
  4. nc_contacts_create_contact (accepts vcard_text or JSON)
  5. nc_contacts_patch_contact NEW (surgical edit, If-Match)
  6. nc_contacts_put_contact NEW (full vCard replace, If-Match)
  7. nc_contacts_delete_contact (gains If-Match)
  8. nc_contacts_create_addressbook + nc_contacts_delete_addressbook
     (counted as one — administrative)

nc_contacts_update_contact is DEPRECATED (kept as a thin shim that
translates JSON-shape calls to nc_contacts_patch_contact and emits a
deprecation warning in logs). One minor version then removed.
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

import logging
from datetime import date
from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from nextcloud_mcp_server.client.contacts import EtagConflictError
from nextcloud_mcp_server.auth import require_scopes
from nextcloud_mcp_server.context import get_client
from nextcloud_mcp_server.models.contacts import (
    AddressBook,
    Contact,
    ContactField,
    GetContactResponse,
    ListAddressBooksResponse,
    ListContactsResponse,
    PatchContactResponse,
    PutContactResponse,
)
from nextcloud_mcp_server.observability.metrics import instrument_tool

logger = logging.getLogger(__name__)


def _parse_vcard_fields(
    raw_values: str | dict | list | None, field_type: str
) -> list[ContactField]:
    if raw_values is None:
        return []
    items: list[str | dict] = (
        raw_values if isinstance(raw_values, list) else [raw_values]
    )
    fields: list[ContactField] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("value", "")
            if not value:
                continue
            types = item.get("type", [])
            if not isinstance(types, list):
                types = [types]
            preferred = any(t.upper() == "PREF" for t in types)
            label_parts = [t for t in types if t.upper() != "PREF"]
            label = ", ".join(label_parts).lower() if label_parts else None
            fields.append(
                ContactField(
                    type=field_type, value=value, preferred=preferred, label=label
                )
            )
        elif isinstance(item, str) and item:
            fields.append(ContactField(type=field_type, value=item))
    return fields


def _raw_contact_to_model(raw: dict) -> Contact:
    contact_info = raw.get("contact", {})
    emails = _parse_vcard_fields(contact_info.get("email"), "email")
    phones = _parse_vcard_fields(contact_info.get("tel"), "phone")
    custom_fields: dict[str, Any] = {}
    nickname = contact_info.get("nickname")
    if nickname:
        custom_fields["nickname"] = nickname
    return Contact(
        uid=raw["vcard_id"],
        fn=contact_info.get("fullname", ""),
        etag=raw.get("getetag"),
        vcard_text=raw.get("vcard_text"),
        birthday=contact_info["birthday"].isoformat()
        if isinstance(contact_info.get("birthday"), date)
        else contact_info.get("birthday"),
        emails=emails,
        phones=phones,
        custom_fields=custom_fields,
    )


def configure_contacts_tools(mcp: FastMCP):
    # ------------------------------------------------------------------
    # 1. list_addressbooks
    # ------------------------------------------------------------------
    @mcp.tool(
        title="List Address Books",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    )
    @require_scopes("contacts.read")
    @instrument_tool
    async def nc_contacts_list_addressbooks(ctx: Context) -> ListAddressBooksResponse:
        """List all available address books."""
        client = await get_client(ctx)
        addressbooks_data = await client.contacts.list_addressbooks()
        addressbooks = [
            AddressBook(
                uri=ab["name"],
                displayname=ab.get("display_name", ab["name"]),
                ctag=ab.get("getctag"),
            )
            for ab in addressbooks_data
        ]
        return ListAddressBooksResponse(
            addressbooks=addressbooks, total_count=len(addressbooks)
        )

    # ------------------------------------------------------------------
    # 2. list_contacts (gains include_vcard / include_etag)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="List Contacts",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    )
    @require_scopes("contacts.read")
    @instrument_tool
    async def nc_contacts_list_contacts(
        ctx: Context,
        *,
        addressbook: str,
        include_vcard: bool = False,
        include_etag: bool = True,
    ) -> ListContactsResponse:
        """List all contacts in the specified addressbook.

        Args:
            addressbook: URI slug of the addressbook (e.g. "contacts").
            include_vcard: include the raw vcard_text per contact (byte-truth,
                useful for byte-preserving subsequent writes).
            include_etag: include the per-contact ETag (default True so
                callers can chain into patch_contact without an extra GET).
        """
        client = await get_client(ctx)
        contacts_data = await client.contacts.list_contacts(
            addressbook=addressbook,
            include_vcard=include_vcard,
            include_etag=include_etag,
        )
        contacts = [_raw_contact_to_model(c) for c in contacts_data]
        return ListContactsResponse(
            contacts=contacts, addressbook=addressbook, total_count=len(contacts)
        )

    # ------------------------------------------------------------------
    # 3. get_contact (NEW)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Get Contact",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    )
    @require_scopes("contacts.read")
    @instrument_tool
    async def nc_contacts_get_contact(
        ctx: Context, *, addressbook: str, uid: str
    ) -> GetContactResponse:
        """Fetch a single contact by UID — returns raw vcard_text + etag + JSON.

        Use the returned ``etag`` as the If-Match precondition for any
        subsequent ``patch_contact`` / ``put_contact`` / ``delete_contact``.

        Args:
            addressbook: URI slug of the addressbook.
            uid: Contact UID.
        """
        client = await get_client(ctx)
        raw = await client.contacts.get_contact(addressbook=addressbook, uid=uid)
        contact = Contact(
            uid=raw["uid"],
            fn=raw["json"].get("fullname", ""),
            etag=raw.get("etag"),
            vcard_text=raw.get("vcard_text"),
            emails=_parse_vcard_fields(raw["json"].get("email"), "email"),
            phones=_parse_vcard_fields(raw["json"].get("tel"), "phone"),
            birthday=raw["json"].get("birthday"),
        )
        return GetContactResponse(contact=contact)

    # ------------------------------------------------------------------
    # 4. create_contact (accepts vcard_text or JSON)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Create Contact",
        annotations=ToolAnnotations(idempotentHint=False, openWorldHint=True),
    )
    @require_scopes("contacts.write")
    @instrument_tool
    async def nc_contacts_create_contact(
        ctx: Context,
        *,
        addressbook: str,
        uid: str,
        vcard_text: Optional[str] = None,
        contact_data: Optional[dict] = None,
    ):
        """Create a new contact.

        Pass ``vcard_text`` (preferred — full fidelity, all properties round-trip)
        OR ``contact_data`` (JSON projection — convenience). At least one is
        required.

        Args:
            addressbook: URI slug.
            uid: UID for the new contact.
            vcard_text: full vCard text per RFC 6350.
            contact_data: convenience JSON, e.g.
                ``{"fn": "John Doe", "email": "john@example.com"}``.
        """
        client = await get_client(ctx)
        return await client.contacts.create_contact(
            addressbook=addressbook,
            uid=uid,
            vcard_text=vcard_text,
            contact_data=contact_data,
        )

    # ------------------------------------------------------------------
    # 5. patch_contact (NEW — surgical edit with If-Match)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Patch Contact (byte-preserving)",
        annotations=ToolAnnotations(idempotentHint=False, openWorldHint=True),
    )
    @require_scopes("contacts.write")
    @instrument_tool
    async def nc_contacts_patch_contact(
        ctx: Context,
        *,
        addressbook: str,
        uid: str,
        etag: str,
        set_props: Optional[dict] = None,
        add_props: Optional[list] = None,
        remove_props: Optional[list] = None,
        verify: bool = False,
        retry_on_conflict: bool = False,
    ) -> PatchContactResponse:
        """Surgical edit. GET-with-ETag -> byte-preserve patch -> PUT-with-If-Match.

        Untouched properties (PHOTO, X-extensions, line-folded NOTEs, vendor
        fields) round-trip byte-equal. Only the touched lines regenerate.

        Args:
            addressbook: URI slug.
            uid: Contact UID.
            etag: If-Match precondition. Get from prior ``get_contact`` /
                ``list_contacts(include_etag=true)``.
            set_props: ``{selector: new_value}`` — replace single matching line.
                Selectors: ``"FN"``, ``"NOTE"``, ``"TEL;TYPE=CELL"``, etc.
            add_props: list of ``[name, value, params]`` — append a new line.
            remove_props: list of selectors to remove (all matches).
            verify: post-write GET to confirm the change reflects.
            retry_on_conflict: on 412, refetch and re-apply once before failing.
        """
        client = await get_client(ctx)
        result = await client.contacts.patch_contact(
            addressbook=addressbook,
            uid=uid,
            etag=etag,
            set_props=set_props,
            add_props=[
                (item[0], item[1], item[2] if len(item) > 2 else None)
                for item in (add_props or [])
            ],
            remove_props=remove_props,
            verify=verify,
            retry_on_conflict=retry_on_conflict,
        )
        return PatchContactResponse(**result)

    # ------------------------------------------------------------------
    # 6. put_contact (NEW — full vCard replace)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Put Contact (full vCard replace)",
        annotations=ToolAnnotations(idempotentHint=True, openWorldHint=True),
    )
    @require_scopes("contacts.write")
    @instrument_tool
    async def nc_contacts_put_contact(
        ctx: Context, *, addressbook: str, uid: str, vcard_text: str, etag: str
    ) -> PutContactResponse:
        """Full vCard replace. Caller is responsible for byte-correctness.

        Use for recovery import or corrective rewrite — most callers want
        ``patch_contact`` (surgical, byte-preserving) instead.

        Args:
            addressbook: URI slug.
            uid: Contact UID.
            vcard_text: complete vCard per RFC 6350.
            etag: If-Match precondition.
        """
        client = await get_client(ctx)
        result = await client.contacts.put_contact(
            addressbook=addressbook, uid=uid, vcard_text=vcard_text, etag=etag
        )
        return PutContactResponse(**result)

    # ------------------------------------------------------------------
    # 7. delete_contact (gains If-Match)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Delete Contact",
        annotations=ToolAnnotations(
            destructiveHint=True, idempotentHint=True, openWorldHint=True
        ),
    )
    @require_scopes("contacts.write")
    @instrument_tool
    async def nc_contacts_delete_contact(
        ctx: Context, *, addressbook: str, uid: str, etag: str = ""
    ):
        """Delete a contact. Pass ``etag`` for If-Match (recommended).

        Args:
            addressbook: URI slug.
            uid: Contact UID.
            etag: optional If-Match precondition; without it, the delete is
                unconditional (allows a concurrent edit to be lost).
        """
        client = await get_client(ctx)
        return await client.contacts.delete_contact(
            addressbook=addressbook, uid=uid, etag=etag
        )

    # ------------------------------------------------------------------
    # 8a. create_addressbook (unchanged)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Create Address Book",
        annotations=ToolAnnotations(idempotentHint=False, openWorldHint=True),
    )
    @require_scopes("contacts.write")
    @instrument_tool
    async def nc_contacts_create_addressbook(
        ctx: Context, *, name: str, display_name: str
    ):
        """Create a new addressbook."""
        client = await get_client(ctx)
        return await client.contacts.create_addressbook(
            name=name, display_name=display_name
        )

    # ------------------------------------------------------------------
    # 8b. delete_addressbook (unchanged)
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Delete Address Book",
        annotations=ToolAnnotations(
            destructiveHint=True, idempotentHint=True, openWorldHint=True
        ),
    )
    @require_scopes("contacts.write")
    @instrument_tool
    async def nc_contacts_delete_addressbook(ctx: Context, *, name: str):
        """Delete an addressbook."""
        client = await get_client(ctx)
        return await client.contacts.delete_addressbook(name=name)

    # ------------------------------------------------------------------
    # DEPRECATED — update_contact shim
    # ------------------------------------------------------------------
    @mcp.tool(
        title="Update Contact (DEPRECATED — use patch_contact)",
        annotations=ToolAnnotations(idempotentHint=False, openWorldHint=True),
    )
    @require_scopes("contacts.write")
    @instrument_tool
    async def nc_contacts_update_contact(
        ctx: Context, *, addressbook: str, uid: str, contact_data: dict, etag: str = ""
    ):
        """DEPRECATED. Use ``nc_contacts_patch_contact`` directly.

        This shim translates the legacy JSON shape to a ``patch_contact``
        invocation underneath; it inherits the byte-preserving substrate so
        PHOTO blobs and X-properties are no longer dropped, but the JSON
        translation is still lossy for fields not in the legacy schema.
        Callers should migrate to ``patch_contact`` for full fidelity.

        Args:
            addressbook: URI slug.
            uid: Contact UID.
            contact_data: legacy JSON, e.g. {"fn": "Jane Doe"}.
            etag: optional If-Match.
        """
        logger.warning(
            "nc_contacts_update_contact is DEPRECATED for uid=%s in %s; "
            "migrate caller to nc_contacts_patch_contact",
            uid,
            addressbook,
        )
        client = await get_client(ctx)
        return await client.contacts.update_contact(
            addressbook=addressbook, uid=uid, contact_data=contact_data, etag=etag
        )
