"""Pydantic models for Contacts app responses.

Updated 2026-04-27 (A.3 schema gaps fix):
- ``Contact.uid`` is now optional with synthetic generation; older vCards
  exported by tools like ``ez-vcard 0.12.1`` lacked UIDs entirely. The
  recovery merge sub-agent had to fall back to FN-based fuzzy matching at
  0.85 threshold because UIDs were universally absent in the backup.
- ``Contact.fn`` falls back to ORG -> N -> opaque placeholder for FN-less
  contacts (typical of ORG-only company/service entries).
- All string fields used for matching/search/dedup are NFC-normalised.
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
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import BaseResponse, StatusResponse


def _nfc(value: Optional[str]) -> Optional[str]:
    """NFC-normalise a string, leaving None passthrough."""
    if value is None:
        return None
    return unicodedata.normalize("NFC", value)


class AddressBook(BaseModel):
    """Model for a Nextcloud address book."""

    uri: str = Field(description="Address book URI")
    displayname: Optional[str] = Field(None, description="Display name")
    ctag: Optional[str] = Field(None, description="CTag for sync")


class ContactField(BaseModel):
    """Model for a contact field (email, phone, etc.)."""

    type: str = Field(description="Field type (e.g., 'email', 'phone')")
    value: str = Field(description="Field value")
    preferred: bool = Field(default=False, description="Whether this is the preferred field")
    label: Optional[str] = Field(None, description="Optional label")


class Contact(BaseModel):
    """Model for a Nextcloud contact.

    UID is optional (older vCards may lack one); when absent at construction
    time, callers may either populate it explicitly OR call
    :meth:`synthesize_uid` from raw vCard bytes for a stable SHA1-derived UID.

    FN is also optional; the resolved display name is exposed via the
    :attr:`display_name` property which falls back through ORG -> family/given
    name -> opaque ``<unnamed UID:...>`` placeholder.
    """

    uid: Optional[str] = Field(
        None,
        description=(
            "Contact UID (vCard UID property). Optional — older vCards may "
            "lack this field. Use synthesize_uid() to derive a stable "
            "SHA1-based UID from raw content when needed."
        ),
    )
    fn: Optional[str] = Field(
        None,
        description=(
            "Full name (formatted name). Optional — ORG-only contacts may "
            "lack this. Use display_name to get the resolved name."
        ),
    )
    given_name: Optional[str] = Field(None, description="Given name")
    family_name: Optional[str] = Field(None, description="Family name")
    organization: Optional[str] = Field(None, description="Organization")
    title: Optional[str] = Field(None, description="Job title")
    birthday: Optional[str] = Field(None, description="Birthday (ISO format)")
    emails: List[ContactField] = Field(
        default_factory=list, description="Email addresses"
    )
    phones: List[ContactField] = Field(
        default_factory=list, description="Phone numbers"
    )
    addresses: List[Dict[str, Any]] = Field(
        default_factory=list, description="Physical addresses"
    )
    urls: List[ContactField] = Field(default_factory=list, description="URLs")
    note: Optional[str] = Field(None, description="Notes (vCard NOTE field)")
    photo: Optional[str] = Field(
        None,
        description=(
            "Photo (vCard PHOTO field). Either a URL or base64-encoded image "
            "data with content-type prefix. Stored verbatim from the source "
            "vCard for byte-preservation."
        ),
    )
    categories: List[str] = Field(
        default_factory=list, description="Contact categories (vCard CATEGORIES field)"
    )
    notes: Optional[str] = Field(None, description="Notes (deprecated alias; prefer note)")
    custom_fields: Dict[str, Any] = Field(
        default_factory=dict, description="Custom fields"
    )
    etag: Optional[str] = Field(None, description="ETag for versioning")
    vcard_text: Optional[str] = Field(
        None,
        description=(
            "Raw vCard text (byte-truth). Populated when the caller requests "
            "include_vcard=true; the only safe input to subsequent put_contact "
            "or input to a byte-preserving patch."
        ),
    )

    @field_validator("birthday", mode="before")
    @classmethod
    def _coerce_birthday(cls, value: Any) -> Any:
        """Accept ``date``/``datetime`` from vobject and serialise to ISO string."""
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return value

    @field_validator("fn", "organization", "given_name", "family_name", mode="before")
    @classmethod
    def _nfc_strings(cls, value: Any) -> Any:
        """NFC-normalise string fields used for matching/search/dedup."""
        if isinstance(value, str):
            return unicodedata.normalize("NFC", value)
        return value

    @model_validator(mode="after")
    def _backfill_uid_if_vcard(self) -> "Contact":
        """If UID is absent but raw vCard text is present, synthesise from content."""
        if not self.uid and self.vcard_text:
            self.uid = synthesize_uid(self.vcard_text)
        return self

    @property
    def display_name(self) -> str:
        """Resolve a display name with fallback chain.

        FN -> ORG -> "given family" -> "<unnamed UID:xxx>".
        """
        if self.fn:
            return self.fn
        if self.organization:
            return self.organization
        composed = " ".join(p for p in (self.given_name, self.family_name) if p).strip()
        if composed:
            return composed
        return f"<unnamed contact UID:{self.uid or 'unknown'}>"

    @property
    def primary_email(self) -> Optional[str]:
        if not self.emails:
            return None
        preferred = next(
            (email.value for email in self.emails if email.preferred), None
        )
        return preferred or self.emails[0].value

    @property
    def primary_phone(self) -> Optional[str]:
        if not self.phones:
            return None
        preferred = next(
            (phone.value for phone in self.phones if phone.preferred), None
        )
        return preferred or self.phones[0].value


def synthesize_uid(vcard_text: str) -> str:
    """Generate a stable synthetic UID for a UID-less vCard.

    SHA1 over the vCard's content; idempotent. Used when the source vCard
    lacks a UID and the substrate needs one (CardDAV requires UID for the
    file-name slug and for client-side dedup).
    """
    digest = hashlib.sha1(vcard_text.encode("utf-8")).hexdigest()
    return f"synthetic-{digest}"


class ListAddressBooksResponse(BaseResponse):
    """Response model for listing address books."""

    addressbooks: List[AddressBook] = Field(description="List of address books")
    total_count: int = Field(description="Total number of address books")


class ListContactsResponse(BaseResponse):
    """Response model for listing contacts."""

    contacts: List[Contact] = Field(description="List of contacts")
    addressbook: str = Field(description="Address book the contacts belong to")
    total_count: int = Field(description="Total number of contacts")


class GetContactResponse(BaseResponse):
    """Response model for fetching a single contact."""

    contact: Contact = Field(
        description=(
            "The fetched contact. ``vcard_text`` and ``etag`` are populated "
            "so callers can chain into patch_contact / put_contact / "
            "delete_contact under If-Match discipline."
        )
    )


class CreateContactResponse(BaseResponse):
    """Response model for contact creation."""

    uid: str = Field(description="The UID of the created contact")
    addressbook: str = Field(description="Address book the contact was created in")


class PatchContactResponse(BaseResponse):
    """Response model for surgical contact edits."""

    uid: str = Field(description="Contact UID")
    addressbook: str = Field(description="Address book")
    old_etag: Optional[str] = Field(None, description="ETag before the patch")
    new_etag: Optional[str] = Field(None, description="ETag after the patch")
    applied: List[str] = Field(
        default_factory=list,
        description="List of property selectors that were modified",
    )
    verified: bool = Field(
        default=False,
        description="Whether post-write GET-verification confirmed the change",
    )


class PutContactResponse(BaseResponse):
    """Response model for full vCard replacement."""

    uid: str = Field(description="Contact UID")
    addressbook: str = Field(description="Address book")
    etag: str = Field(description="ETag after the PUT")


class UpdateContactResponse(BaseResponse):
    """DEPRECATED — use PatchContactResponse. Kept for back-compat."""

    contact: Contact = Field(description="The updated contact")
    addressbook: str = Field(description="Address book the contact belongs to")
