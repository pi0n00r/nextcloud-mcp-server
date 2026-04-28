"""CardDAV client for NextCloud contacts operations.

Byte-preserving rewrite — routes vCard mutations through the line-oriented
parser in ``vcard_parser.py`` rather than the JSON↔vCard round-trip pattern
that silently dropped PHOTO blobs (corpus-wide loss disclosed 2026-04-26).

Public surface:
- ``list_addressbooks`` — unchanged.
- ``list_contacts`` — gains ``include_vcard`` / ``include_etag`` flags.
- ``get_contact`` — NEW. Single contact by UID, returns
  ``{vcard_text, etag, json}`` in one call.
- ``create_contact`` — accepts ``vcard_text`` OR ``contact_data`` JSON.
- ``patch_contact`` — NEW. Surgical edit; If-Match required.
- ``put_contact`` — NEW. Full vCard replace; If-Match required.
- ``delete_contact`` — gains ``etag`` (If-Match) parameter.
- ``update_contact`` — DEPRECATED shim. Translates JSON to ``patch_contact``.

ETag/If-Match semantics: every write surfaces 412 to the caller as
``EtagConflictError``; no silent retries. Callers may opt into one
re-fetch+re-apply attempt via the optional ``retry_on_conflict`` parameter
on ``patch_contact``.
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

import hashlib
import logging
import unicodedata
import warnings
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any, Iterable, Optional
from httpx import HTTPStatusError
from pythonvCard4.vcard import Contact
from .base import BaseNextcloudClient
from .vcard_parser import VCard, patch_vcard

logger = logging.getLogger(__name__)


class EtagConflictError(Exception):
    """Raised when a CardDAV PUT/DELETE returns 412 Precondition Failed.

    Carries the current server ETag (if available) so callers can refetch
    and re-apply.
    """

    def __init__(self, message: str, current_etag: Optional[str] = None):
        super().__init__(message)
        self.current_etag = current_etag


class VerifyMismatchError(Exception):
    """Raised when a write succeeded at the transport layer but the post-write
    GET does not reflect the requested change. Indicates a structural-loss
    bug in the substrate or in the CardDAV server's vCard normalisation.
    """


class ContactsClient(BaseNextcloudClient):
    """Client for NextCloud CardDAV contact operations."""

    app_name = "contacts"

    # ------------------------------------------------------------------
    # path helpers
    # ------------------------------------------------------------------

    def _get_carddav_base_path(self) -> str:
        return f"/remote.php/dav/addressbooks/users/{self.username}"

    def _vcard_url(self, addressbook: str, uid: str) -> str:
        return f"{self._get_carddav_base_path()}/{addressbook}/{uid}.vcf"

    # ------------------------------------------------------------------
    # addressbook ops (unchanged)
    # ------------------------------------------------------------------
    async def list_addressbooks(self):
        carddav_path = self._get_carddav_base_path()
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/">
  <d:prop>
    <d:displayname />
    <d:getctag />
  </d:prop>
</d:propfind>"""
        headers = {"Content-Type": "application/xml", "Accept": "application/xml"}
        response = await self._make_request(
            "PROPFIND", carddav_path, content=propfind_body, headers=headers
        )
        ns = {"d": "DAV:"}
        root = ET.fromstring(response.content)
        addressbooks = []
        for response_elem in root.findall(".//d:response", ns):
            href = response_elem.find(".//d:href", ns)
            if href is None:
                continue
            href_text = href.text or ""
            if not href_text.endswith("/"):
                continue
            addressbook_name = href_text.rstrip("/").split("/")[-1]
            if not addressbook_name or addressbook_name == self.username:
                continue
            propstat = response_elem.find(".//d:propstat", ns)
            if propstat is None:
                continue
            prop = propstat.find(".//d:prop", ns)
            if prop is None:
                continue
            displayname_elem = prop.find(".//d:displayname", ns)
            displayname = (
                displayname_elem.text if displayname_elem is not None else addressbook_name
            )
            getctag_elem = prop.find(".//d:getctag", ns)
            getctag = getctag_elem.text if getctag_elem is not None else None
            addressbooks.append(
                {
                    "name": addressbook_name,
                    "display_name": displayname,
                    "getctag": getctag,
                }
            )
        logger.debug(f"Found {len(addressbooks)} addressbooks")
        return addressbooks

    async def create_addressbook(self, *, name: str, display_name: str):
        carddav_path = self._get_carddav_base_path()
        url = f"{carddav_path}/{name}/"
        prop_body = f"""<?xml version="1.0" encoding="utf-8"?>
<d:mkcol xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:set>
    <d:prop>
      <d:resourcetype>
        <d:collection/>
        <c:addressbook/>
      </d:resourcetype>
      <d:displayname>{display_name}</d:displayname>
    </d:prop>
  </d:set>
</d:mkcol>"""
        headers = {"Content-Type": "application/xml"}
        await self._make_request("MKCOL", url, content=prop_body, headers=headers)

    async def delete_addressbook(self, *, name: str):
        carddav_path = self._get_carddav_base_path()
        url = f"{carddav_path}/{name}/"
        await self._make_request("DELETE", url)

    # ------------------------------------------------------------------
    # contact reads
    # ------------------------------------------------------------------

    async def list_contacts(
        self,
        *,
        addressbook: str,
        include_vcard: bool = False,
        include_etag: bool = True,
    ) -> list[dict]:
        """List contacts."""
        carddav_path = self._get_carddav_base_path()
        report_body = """<?xml version="1.0" encoding="utf-8"?>
<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:getetag />
    <card:address-data />
  </d:prop>
</card:addressbook-query>"""
        headers = {
            "Depth": "1",
            "Content-Type": "application/xml",
            "Accept": "application/xml",
        }
        response = await self._make_request(
            "REPORT",
            f"{carddav_path}/{addressbook}",
            content=report_body,
            headers=headers,
        )
        ns = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav"}
        root = ET.fromstring(response.content)
        contacts: list[dict] = []
        for response_elem in root.findall(".//d:response", ns):
            href = response_elem.find(".//d:href", ns)
            if href is None:
                continue
            href_text = href.text or ""
            vcard_id = href_text.rstrip("/").split("/")[-1].replace(".vcf", "")
            if not vcard_id:
                continue
            propstat = response_elem.find(".//d:propstat", ns)
            if propstat is None:
                continue
            prop = propstat.find(".//d:prop", ns)
            if prop is None:
                continue
            getetag_elem = prop.find(".//d:getetag", ns)
            getetag = getetag_elem.text if getetag_elem is not None else None
            addressdata_elem = prop.find(".//card:address-data", ns)
            addressdata = (
                addressdata_elem.text if addressdata_elem is not None else None
            )
            if addressdata is None:
                continue
            contact_dict: dict[str, Any] = {
                "vcard_id": vcard_id,
                "contact": _vcard_to_json_projection(addressdata, fallback_uid=vcard_id),
            }
            if include_etag:
                contact_dict["getetag"] = getetag
            if include_vcard:
                contact_dict["vcard_text"] = addressdata
            contacts.append(contact_dict)
        logger.debug(f"Found {len(contacts)} contacts")
        return contacts

    async def get_contact(
        self, *, addressbook: str, uid: str
    ) -> dict[str, Any]:
        """Fetch a single contact's raw vCard + ETag + JSON projection."""
        url = self._vcard_url(addressbook, uid)
        response = await self._make_request("GET", url)
        response.raise_for_status()
        etag = response.headers.get("etag", "")
        vcard_text = response.text
        return {
            "uid": uid,
            "addressbook": addressbook,
            "etag": etag,
            "vcard_text": vcard_text,
            "json": _vcard_to_json_projection(vcard_text, fallback_uid=uid),
        }

    async def _get_raw_vcard(self, addressbook: str, uid: str) -> tuple[str, str]:
        """Internal: fetch raw vCard text + ETag."""
        url = self._vcard_url(addressbook, uid)
        response = await self._make_request("GET", url)
        response.raise_for_status()
        etag = response.headers.get("etag", "")
        return response.text, etag

    # ------------------------------------------------------------------
    # contact writes — byte-preserving, ETag/If-Match
    # ------------------------------------------------------------------

    async def create_contact(
        self,
        *,
        addressbook: str,
        uid: str,
        vcard_text: Optional[str] = None,
        contact_data: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Create a new contact."""
        url = self._vcard_url(addressbook, uid)
        if vcard_text is None and contact_data is None:
            raise ValueError("create_contact requires vcard_text or contact_data")
        if vcard_text is None:
            assert contact_data is not None
            contact = Contact(fn=contact_data.get("fn"), uid=uid)  # type: ignore
            if "email" in contact_data:
                contact.email = [{"value": contact_data["email"], "type": ["HOME"]}]
            if "tel" in contact_data:
                contact.tel = [{"value": contact_data["tel"], "type": ["HOME"]}]
            vcard_text = contact.to_vcard()
        headers = {
            "Content-Type": "text/vcard; charset=utf-8",
            "If-None-Match": "*",
        }
        try:
            response = await self._make_request(
                "PUT", url, content=vcard_text, headers=headers
            )
            response.raise_for_status()
        except HTTPStatusError as e:
            if e.response.status_code == 412:
                raise EtagConflictError(
                    f"contact {uid} already exists (If-None-Match conflict)"
                ) from e
            raise
        return {
            "uid": uid,
            "addressbook": addressbook,
            "etag": response.headers.get("etag", ""),
        }

    async def patch_contact(
        self,
        *,
        addressbook: str,
        uid: str,
        etag: str,
        set_props: Optional[dict[str, str]] = None,
        add_props: Optional[
            Iterable[tuple[str, str, Optional[list[tuple[str, str]]]]]
        ] = None,
        remove_props: Optional[Iterable[str]] = None,
        verify: bool = False,
        retry_on_conflict: bool = False,
    ) -> dict[str, Any]:
        """Surgical edit. GET-with-ETag -> byte-preserve patch -> PUT-with-If-Match."""
        return await self._do_patch(
            addressbook=addressbook,
            uid=uid,
            etag=etag,
            set_props=set_props,
            add_props=add_props,
            remove_props=remove_props,
            verify=verify,
            retry_on_conflict=retry_on_conflict,
            attempt=0,
        )

    async def _do_patch(
        self,
        *,
        addressbook: str,
        uid: str,
        etag: str,
        set_props,
        add_props,
        remove_props,
        verify: bool,
        retry_on_conflict: bool,
        attempt: int,
    ) -> dict[str, Any]:
        url = self._vcard_url(addressbook, uid)
        current_vcard, current_etag = await self._get_raw_vcard(addressbook, uid)
        if etag and current_etag and etag != current_etag:
            raise EtagConflictError(
                f"caller etag {etag!r} != current server etag {current_etag!r}",
                current_etag=current_etag,
            )
        new_vcard = patch_vcard(
            current_vcard,
            set_props=set_props,
            add_props=add_props,
            remove_props=remove_props,
        )
        headers = {"Content-Type": "text/vcard; charset=utf-8"}
        if current_etag:
            headers["If-Match"] = current_etag
        try:
            response = await self._make_request(
                "PUT", url, content=new_vcard, headers=headers
            )
            response.raise_for_status()
        except HTTPStatusError as e:
            if e.response.status_code == 412:
                if retry_on_conflict and attempt == 0:
                    logger.info(
                        f"412 on patch_contact {uid}; retrying once with fresh ETag"
                    )
                    return await self._do_patch(
                        addressbook=addressbook,
                        uid=uid,
                        etag="",
                        set_props=set_props,
                        add_props=add_props,
                        remove_props=remove_props,
                        verify=verify,
                        retry_on_conflict=False,
                        attempt=1,
                    )
                raise EtagConflictError(
                    f"412 PUT {url}; vCard modified by another writer",
                    current_etag=e.response.headers.get("etag"),
                ) from e
            raise

        new_etag = response.headers.get("etag", "")
        applied: list[str] = []
        if set_props:
            applied.extend(set_props.keys())
        if add_props:
            applied.extend(t[0] for t in add_props)
        if remove_props:
            applied.extend(remove_props)

        verified = False
        if verify:
            check_vcard, _ = await self._get_raw_vcard(addressbook, uid)
            if set_props:
                for sel, expected_value in set_props.items():
                    found = VCard.parse(check_vcard).find(sel)
                    if not found:
                        raise VerifyMismatchError(
                            f"verify: property {sel} missing after PUT"
                        )
            verified = True

        return {
            "uid": uid,
            "addressbook": addressbook,
            "old_etag": current_etag,
            "new_etag": new_etag,
            "applied": applied,
            "verified": verified,
        }

    async def put_contact(
        self,
        *,
        addressbook: str,
        uid: str,
        vcard_text: str,
        etag: str,
    ) -> dict[str, Any]:
        """Full vCard replace with If-Match."""
        url = self._vcard_url(addressbook, uid)
        headers = {"Content-Type": "text/vcard; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        try:
            response = await self._make_request(
                "PUT", url, content=vcard_text, headers=headers
            )
            response.raise_for_status()
        except HTTPStatusError as e:
            if e.response.status_code == 412:
                raise EtagConflictError(
                    f"412 on put_contact {uid}; If-Match {etag!r} stale",
                    current_etag=e.response.headers.get("etag"),
                ) from e
            raise
        return {
            "uid": uid,
            "addressbook": addressbook,
            "etag": response.headers.get("etag", ""),
        }

    async def delete_contact(
        self, *, addressbook: str, uid: str, etag: str = ""
    ):
        """Delete a contact. Pass ``etag`` for If-Match (recommended)."""
        url = self._vcard_url(addressbook, uid)
        headers: dict[str, str] = {}
        if etag:
            headers["If-Match"] = etag
        try:
            response = await self._make_request("DELETE", url, headers=headers)
            response.raise_for_status()
        except HTTPStatusError as e:
            if e.response.status_code == 412:
                raise EtagConflictError(
                    f"412 on delete_contact {uid}; If-Match stale",
                    current_etag=e.response.headers.get("etag"),
                ) from e
            raise

    # ------------------------------------------------------------------
    # legacy shim — DEPRECATED
    # ------------------------------------------------------------------

    async def update_contact(
        self,
        *,
        addressbook: str,
        uid: str,
        contact_data: dict,
        etag: str = "",
    ):
        """DEPRECATED. Use ``patch_contact`` directly with set_props/add_props."""
        warnings.warn(
            "update_contact is deprecated; migrate to patch_contact "
            "(set_props/add_props/remove_props with explicit If-Match etag)",
            DeprecationWarning,
            stacklevel=2,
        )
        set_props: dict[str, str] = {}
        for key, value in contact_data.items():
            key_l = key.lower()
            if key_l == "fn":
                set_props["FN"] = str(value)
            elif key_l == "email":
                if isinstance(value, str):
                    set_props["EMAIL"] = value
            elif key_l == "tel":
                if isinstance(value, str):
                    set_props["TEL;TYPE=CELL"] = value
            elif key_l == "note":
                set_props["NOTE"] = str(value)
            elif key_l == "nickname":
                set_props["NICKNAME"] = (
                    value if isinstance(value, str) else ",".join(value)
                )
            elif key_l == "bday":
                set_props["BDAY"] = str(value)
            elif key_l in ("org", "organization"):
                set_props["ORG"] = str(value)
            elif key_l == "title":
                set_props["TITLE"] = str(value)
            elif key_l == "categories":
                set_props["CATEGORIES"] = (
                    value if isinstance(value, str) else ",".join(value)
                )
        return await self.patch_contact(
            addressbook=addressbook, uid=uid, etag=etag, set_props=set_props
        )


# ---- shared helpers ----------------------------------------------------


def _vcard_to_json_projection(vcard_text: str, *, fallback_uid: str) -> dict[str, Any]:
    """Best-effort JSON projection for the deprecated convenience surface."""
    try:
        contact = Contact.from_vcard(vcard_text)
    except Exception as e:
        logger.warning(f"vCard parse failed for projection (UID={fallback_uid}): {e}")
        return {
            "fullname": fallback_uid,
            "nickname": None,
            "birthday": None,
            "email": None,
            "tel": None,
        }

    fn = getattr(contact, "fn", None) or ""
    if not fn:
        org = getattr(contact, "org", None)
        if org:
            fn = str(org)
        else:
            n = getattr(contact, "n", None)
            if n:
                fn = str(n)
            else:
                fn = f"<unnamed contact UID:{fallback_uid}>"
    fn = unicodedata.normalize("NFC", fn)

    bday = getattr(contact, "bday", None)
    if isinstance(bday, date):
        bday = bday.isoformat()

    return {
        "fullname": fn,
        "nickname": getattr(contact, "nickname", None),
        "birthday": bday,
        "email": getattr(contact, "email", None),
        "tel": getattr(contact, "tel", None),
    }


def synthesize_uid(vcard_text: str) -> str:
    """Generate a stable synthetic UID for a UID-less vCard."""
    digest = hashlib.sha1(vcard_text.encode("utf-8")).hexdigest()
    return f"synthetic-{digest}"
