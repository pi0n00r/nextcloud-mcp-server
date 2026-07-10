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
from urllib.parse import unquote

from httpx import HTTPStatusError
from pythonvCard4.vcard import Contact

from .base import BaseNextcloudClient
from .vcard_parser import VCard, patch_vcard

logger = logging.getLogger(__name__)


# Canonical keys accepted by _build_contact_from_data. Callers normalise aliases
# (``phone``→``tel``, ``organization``→``org``) via _normalize_contact_data beforehand
# so the set never needs to list them.
_SUPPORTED_CONTACT_KEYS = frozenset(
    {
        "fn",
        "email",
        "tel",
        "org",
        "note",
        "title",
        "nickname",
        "bday",
        "categories",
        "url",
    }
)


def _normalize_contact_data(contact_data: dict[str, Any]) -> dict[str, Any]:
    """Map documented aliases to canonical keys.

    ``phone`` → ``tel``, ``organization`` → ``org``. The canonical key wins if both
    are supplied, so callers who set ``tel`` don't lose it to a stray ``phone`` entry.
    Returns a new dict — does not mutate the caller's argument.
    """
    normalised = dict(contact_data)
    if "phone" in normalised and "tel" not in normalised:
        normalised["tel"] = normalised.pop("phone")
    else:
        normalised.pop("phone", None)
    if "organization" in normalised and "org" not in normalised:
        normalised["org"] = normalised.pop("organization")
    else:
        normalised.pop("organization", None)
    return normalised


def _wrap_contact_field(
    value: str | dict[str, Any] | list[str | dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize an email/tel input into pythonvCard4's list-of-dicts shape.

    Accepts a plain string, a dict already in ``{value, type}`` form, or a list of
    either. Empty strings and dicts without a ``value`` key are dropped. Always
    returns a list (possibly empty).
    """
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else [value]
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("value"):
            types = item.get("type") or ["HOME"]
            # Wrap a bare string so ``list("WORK")`` doesn't iterate it into
            # ``["W", "O", "R", "K"]`` — same char-iteration footgun this whole
            # helper exists to avoid for the outer ``value``.
            if isinstance(types, str):
                types = [types]
            out.append({"value": item["value"], "type": list(types)})
        elif isinstance(item, str) and item:
            out.append({"value": item, "type": ["HOME"]})
    return out


def _as_str_list(value: str | list[str]) -> list[str]:
    """Wrap a bare string in a list. Does NOT split on commas.

    Used for ORG/NICKNAME/URL where commas are part of the value (e.g.
    ``"Smith, Jones & Associates"``) and only the list wrapper is needed to
    prevent pythonvCard4 from iterating the string character-by-character.
    """
    return value if isinstance(value, list) else [value]


def _split_categories(value: str | list[str]) -> list[str]:
    """Normalise CATEGORIES input: a comma-separated string is split into a list.

    Unlike ORG/NICKNAME, CATEGORIES is canonically comma-separated in vCards
    (``CATEGORIES:a,b,c``) so splitting a bare string is the expected shape.
    Lists pass through unchanged — callers that already provide ``["a,b"]`` keep
    their exact item, no double-splitting.
    """
    if isinstance(value, list):
        return value
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_bday(value: str | date | None) -> date | None:
    """Parse a BDAY input to a ``date``. Logs and returns ``None`` if unparseable.

    Shared by the create path (``_build_contact_from_data``) and the update path
    (``_merge_vcard_properties``) so a non-ISO BDAY is rejected consistently
    instead of being written raw on update.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            logger.warning("Ignoring non-ISO bday value: %r", value)
    return None


def _first_custom(custom: dict[str, str | list[str]], key: str) -> str | None:
    """Return the first raw value pythonvCard4 stashed in ``custom[key]``.

    The library has no typed parser for ORG / TITLE / unencoded PHOTO, so they
    end up in ``Contact.custom`` keyed by property name. The library's typeshed
    declares the values as ``str | list[str]`` even though the current parser
    always appends to a list — accept both shapes so we don't break on a future
    library version that switches to bare strings. Returns ``None`` when the
    key is absent or the value is empty.
    """
    values = custom.get(key)
    if isinstance(values, list):
        return values[0] if values else None
    if isinstance(values, str):
        return values or None
    return None


def _safe_vcard_value(value: Any) -> Any:
    """Escape newlines in a value so it can't inject additional vCard properties.

    Per RFC 6350 §3.4 newlines inside a property value are encoded as ``\\n``.
    Unfolding this on the read side is pythonvCard4's job; we only need to make
    sure ``contact_data`` strings don't terminate the line on the way out.
    """
    if isinstance(value, str):
        return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return value


def _build_contact_from_data(contact_data: dict[str, Any], uid: str) -> Contact:
    """Build a pythonvCard4 Contact from an MCP ``contact_data`` dict.

    Maps every key documented on ``nc_contacts_create_contact`` onto the underlying
    library, normalising shapes (list/str) to avoid pythonvCard4's char-by-char
    iteration of bare strings — see issue #716.

    Callers must pre-normalise aliases via ``_normalize_contact_data`` before
    invoking this helper; it assumes canonical keys only.
    """
    data = contact_data

    if not data.get("fn"):
        logger.warning(
            "contact_data missing required 'fn' field; pythonvCard4 may reject or "
            "produce an invalid vCard"
        )

    kwargs: dict[str, Any] = {"fn": data.get("fn"), "uid": uid}

    emails = _wrap_contact_field(data.get("email"))
    if emails:
        kwargs["email"] = emails

    tels = _wrap_contact_field(data.get("tel"))
    if tels:
        kwargs["tel"] = tels

    if data.get("org"):
        kwargs["org"] = _as_str_list(data["org"])

    if data.get("note"):
        kwargs["note"] = data["note"]

    if data.get("title"):
        kwargs["title"] = data["title"]

    if data.get("nickname"):
        kwargs["nickname"] = _as_str_list(data["nickname"])

    if data.get("categories"):
        kwargs["categories"] = _split_categories(data["categories"])

    if data.get("url"):
        kwargs["url"] = _as_str_list(data["url"])

    bday = _parse_bday(data.get("bday"))
    if bday is not None:
        kwargs["bday"] = bday

    unknown = set(data) - _SUPPORTED_CONTACT_KEYS
    if unknown:
        logger.debug("Ignoring unknown contact_data keys: %s", sorted(unknown))

    # kwargs built dynamically from contact_data; pythonvCard4's Contact typeshed
    # has specific typed params and doesn't accept **dict[str, Any].
    return Contact(**kwargs)  # type: ignore[arg-type]


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
        return f"/remote.php/dav/addressbooks/users/{self._principal_or_username()}"

    def _vcard_url(self, addressbook: str, uid: str) -> str:
        return f"{self._get_carddav_base_path()}/{addressbook}/{uid}.vcf"

    async def _list_object_names(self, addressbook: str) -> list[str]:
        """Return CardDAV object filenames stored in ``addressbook``."""
        await self._ensure_principal_id()
        carddav_path = self._get_carddav_base_path()
        propfind_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>'
        )
        response = await self._make_request(
            "PROPFIND",
            f"{carddav_path}/{addressbook}",
            content=propfind_body,
            headers={
                "Depth": "1",
                "Content-Type": "application/xml",
                "Accept": "application/xml",
            },
        )
        ns = {"d": "DAV:"}
        root = ET.fromstring(response.content)
        names: list[str] = []
        for response_elem in root.findall(".//d:response", ns):
            href = response_elem.find(".//d:href", ns)
            if href is None or not href.text:
                continue
            href_text = unquote(href.text)
            if href_text.endswith("/"):
                continue
            names.append(href_text.split("/")[-1])
        return names

    async def _resolve_object_name(self, addressbook: str, uid: str) -> str | None:
        """Map a surfaced contact id back to its real CardDAV filename."""
        candidates = [
            name
            for name in await self._list_object_names(addressbook)
            if name.removesuffix(".vcf") == uid
        ]
        if not candidates:
            return None
        conventional = f"{uid}.vcf"
        return conventional if conventional in candidates else candidates[0]

    async def _resolved_vcard_url(self, addressbook: str, uid: str) -> str:
        await self._ensure_principal_id()
        object_name = await self._resolve_object_name(addressbook, uid) or f"{uid}.vcf"
        return f"{self._get_carddav_base_path()}/{addressbook}/{object_name}"

    # ------------------------------------------------------------------
    # addressbook ops (unchanged)
    # ------------------------------------------------------------------
    async def list_addressbooks(self):
        await self._ensure_principal_id()
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
            if (
                not addressbook_name
                or addressbook_name == self._principal_or_username()
            ):
                continue
            propstat = response_elem.find(".//d:propstat", ns)
            if propstat is None:
                continue
            prop = propstat.find(".//d:prop", ns)
            if prop is None:
                continue
            displayname_elem = prop.find(".//d:displayname", ns)
            displayname = (
                displayname_elem.text
                if displayname_elem is not None
                else addressbook_name
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
        logger.debug("Found %s addressbooks", len(addressbooks))
        return addressbooks

    async def create_addressbook(self, *, name: str, display_name: str):
        await self._ensure_principal_id()
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
        await self._ensure_principal_id()
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
        await self._ensure_principal_id()
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
            href_text = unquote(href.text or "")
            vcard_id = href_text.rstrip("/").split("/")[-1].removesuffix(".vcf")
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
                "object_name": href_text.rstrip("/").split("/")[-1],
                "object_path": href_text,
                "contact": _vcard_to_json_projection(
                    addressdata, fallback_uid=vcard_id
                ),
            }
            if include_etag:
                contact_dict["getetag"] = getetag
            if include_vcard:
                contact_dict["vcard_text"] = addressdata
            contacts.append(contact_dict)
        logger.debug("Found %s contacts", len(contacts))
        return contacts

    async def get_contact(self, *, addressbook: str, uid: str) -> dict[str, Any]:
        """Fetch a single contact's raw vCard + ETag + JSON projection."""
        url = await self._resolved_vcard_url(addressbook, uid)
        response = await self._make_request("GET", url)
        response.raise_for_status()
        etag = response.headers.get("etag", "")
        vcard_text = response.text
        return {
            "uid": uid,
            "addressbook": addressbook,
            "object_name": url.rstrip("/").split("/")[-1],
            "object_path": url,
            "etag": etag,
            "vcard_text": vcard_text,
            "json": _vcard_to_json_projection(vcard_text, fallback_uid=uid),
        }

    async def _get_raw_vcard(self, addressbook: str, uid: str) -> tuple[str, str]:
        """Internal: fetch raw vCard text + ETag."""
        url = await self._resolved_vcard_url(addressbook, uid)
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
        await self._ensure_principal_id()
        url = self._vcard_url(addressbook, uid)
        if vcard_text is None and contact_data is None:
            raise ValueError("create_contact requires vcard_text or contact_data")
        if vcard_text is None:
            assert contact_data is not None
            contact_data = _normalize_contact_data(contact_data)
            vcard_text = _build_contact_from_data(contact_data, uid).to_vcard()
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
        url = await self._resolved_vcard_url(addressbook, uid)
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
                        "412 on patch_contact %s; retrying once with fresh ETag", uid
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
        url = await self._resolved_vcard_url(addressbook, uid)
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

    async def delete_contact(self, *, addressbook: str, uid: str, etag: str = ""):
        """Delete a contact. Pass ``etag`` for If-Match (recommended)."""
        url = await self._resolved_vcard_url(addressbook, uid)
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
            elif key_l in ("url", "urls"):
                if isinstance(value, str):
                    set_props["URL"] = value
                elif isinstance(value, list):
                    first_url = next(
                        (item for item in value if isinstance(item, str)), None
                    )
                    if first_url is not None:
                        set_props["URL"] = first_url
            elif key_l == "categories":
                set_props["CATEGORIES"] = (
                    value if isinstance(value, str) else ",".join(value)
                )
        return await self.patch_contact(
            addressbook=addressbook, uid=uid, etag=etag, set_props=set_props
        )


# ---- shared helpers ----------------------------------------------------


def _vcard_to_json_projection(vcard_text: str, *, fallback_uid: str) -> dict[str, Any]:
    """Build the complete JSON projection used by contact read tools."""
    try:
        contact = Contact.from_vcard(vcard_text)
    except Exception as e:
        logger.warning(
            "vCard parse failed for projection (UID=%s): %s", fallback_uid, e
        )
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

    custom = getattr(contact, "custom", None) or {}
    custom_fields = {
        key: value
        for key, value in custom.items()
        if isinstance(key, str) and key.upper().startswith("X-")
    }

    return {
        "fullname": fn,
        "nickname": getattr(contact, "nickname", None),
        "birthday": bday,
        "email": getattr(contact, "email", None),
        "tel": getattr(contact, "tel", None),
        "org": getattr(contact, "org", None) or _first_custom(custom, "ORG"),
        "title": getattr(contact, "title", None) or _first_custom(custom, "TITLE"),
        "note": getattr(contact, "note", None),
        "url": getattr(contact, "url", None),
        "categories": getattr(contact, "categories", None),
        "photo": getattr(contact, "photo", None) or _first_custom(custom, "PHOTO"),
        "custom_fields": custom_fields,
    }


def synthesize_uid(vcard_text: str) -> str:
    """Generate a stable synthetic UID for a UID-less vCard."""
    digest = hashlib.sha1(vcard_text.encode("utf-8")).hexdigest()
    return f"synthetic-{digest}"
