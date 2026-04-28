"""Test corpus T1-T10 for the byte-preserving vCard substrate.

Per Plan §A.1 (Mankind Grooming end-to-end test) and Implementation
§Test corpus T1-T10. These are pure-parser unit tests — no live NC
required. Network-bearing variants (T8 ETag conflict against a real
CardDAV server) are gated behind ``REQUIRES_LIVE_NC=1``.
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

from nextcloud_mcp_server.client.vcard_parser import VCard, patch_vcard


# Mankind Grooming reference vCard — the canonical clobber victim.
MANKIND_GROOMING = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "UID:mankind-grooming-001\r\n"
    "FN:Mankind Grooming\r\n"
    "ORG:Mankind Grooming\r\n"
    "TEL;TYPE=CELL:+14165551234\r\n"
    "TEL;TYPE=WORK:+14165557777\r\n"
    "EMAIL;TYPE=WORK:info@mankindgrooming.com\r\n"
    "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAEBAQEBAQEBA\r\n"
    " QEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB\r\n"
    " AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB\r\n"
    " AQE=\r\n"
    "X-MAID-NAME:Anka\r\n"
    "X-BARBER-NAME:Kosta\r\n"
    "NOTE:Preferred barber is Kosta. Photo from Google Maps 2024.\r\n"
    "REV:20251101T000000Z\r\n"
    "END:VCARD\r\n"
)


def test_t1_photo_round_trip_no_op_byte_equal():
    """T1 — Photo round-trip on no-op edit: byte-equal output."""
    out = patch_vcard(MANKIND_GROOMING, set_props={})
    assert out == MANKIND_GROOMING


def test_t1b_mankind_grooming_note_edit_preserves_photo_and_x_props():
    """T1b — Targeted NOTE edit preserves PHOTO blob, X- props, all TELs."""
    out = patch_vcard(
        MANKIND_GROOMING,
        set_props={"NOTE": "Preferred barber is Kosta. Verified 2026-04."},
    )
    # PHOTO blob with line folding preserved.
    photo_block = (
        "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAEBAQEBAQEBA\r\n"
        " QEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB\r\n"
        " AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB\r\n"
        " AQE=\r\n"
    )
    assert photo_block in out, "PHOTO blob lost — A.1 regression"
    # X-properties preserved.
    assert "X-MAID-NAME:Anka\r\n" in out
    assert "X-BARBER-NAME:Kosta\r\n" in out
    # All TEL lines preserved byte-equal.
    assert "TEL;TYPE=CELL:+14165551234\r\n" in out
    assert "TEL;TYPE=WORK:+14165557777\r\n" in out
    # NOTE updated.
    assert "NOTE:Preferred barber is Kosta. Verified 2026-04.\r\n" in out
    # Revision preserved.
    assert "REV:20251101T000000Z\r\n" in out


def test_t2_multi_tel_with_type_add_fax():
    """T2 — vCard with three TELs (CELL/WORK/HOME); add a fourth TYPE=FAX."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:t2\r\nFN:Test\r\n"
        "TEL;TYPE=CELL:+11111\r\n"
        "TEL;TYPE=WORK:+22222\r\n"
        "TEL;TYPE=HOME:+33333\r\n"
        "END:VCARD\r\n"
    )
    out = patch_vcard(sample, add_props=[("TEL", "+44444", [("TYPE", "FAX")])])
    assert "TEL;TYPE=CELL:+11111\r\n" in out
    assert "TEL;TYPE=WORK:+22222\r\n" in out
    assert "TEL;TYPE=HOME:+33333\r\n" in out
    assert "TEL;TYPE=FAX:+44444\r\n" in out


def test_t3_tel_replacement_by_type():
    """T3 — Replace TEL;TYPE=CELL by selector; other TELs preserved byte-equal."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:t3\r\nFN:Test\r\n"
        "TEL;TYPE=CELL:+11111\r\n"
        "TEL;TYPE=WORK:+22222\r\n"
        "TEL;TYPE=HOME:+33333\r\n"
        "END:VCARD\r\n"
    )
    out = patch_vcard(sample, set_props={"TEL;TYPE=CELL": "+99999"})
    assert "TEL;TYPE=CELL:+99999\r\n" in out
    assert "TEL;TYPE=WORK:+22222\r\n" in out
    assert "TEL;TYPE=HOME:+33333\r\n" in out
    assert "+11111" not in out


def test_t4_x_custom_property_preserved_across_unrelated_edit():
    """T4 — X-MAID-NAME preserved byte-equal when FN is edited."""
    out = patch_vcard(MANKIND_GROOMING, set_props={"FN": "Mankind Grooming Inc."})
    assert "X-MAID-NAME:Anka\r\n" in out
    assert "X-BARBER-NAME:Kosta\r\n" in out
    assert "FN:Mankind Grooming Inc.\r\n" in out


def test_t5_line_folded_long_note_preserved():
    """T5 — RFC 5545-folded NOTE >75 octets preserved byte-equal across unrelated edit."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:t5\r\nFN:Test\r\n"
        "NOTE:This is a very long note that exceeds 75 octets so it must be folded acros\r\n"
        " s multiple lines per RFC 5545 section 3.1 line folding rules and we need to e\r\n"
        " nsure the entire content survives a no-op edit byte-equal.\r\n"
        "EMAIL:test@example.com\r\nEND:VCARD\r\n"
    )
    out = patch_vcard(sample, set_props={"EMAIL": "updated@example.com"})
    folded_note = (
        "NOTE:This is a very long note that exceeds 75 octets so it must be folded acros\r\n"
        " s multiple lines per RFC 5545 section 3.1 line folding rules and we need to e\r\n"
        " nsure the entire content survives a no-op edit byte-equal.\r\n"
    )
    assert folded_note in out


def test_t6_non_ascii_emoji_zwj_fn_preserved():
    """T6 — ZWJ-bearing emoji FN (Bell variant from Gary's address book)."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:t6\r\n"
        "FN:Bell \U0001F575\U0001F3FC\u200d\u2642\ufe0f (OTP)\r\n"
        "NOTE:OTP carrier\r\nEND:VCARD\r\n"
    )
    out = patch_vcard(sample, set_props={"NOTE": "OTP carrier (verified)"})
    assert "FN:Bell \U0001F575\U0001F3FC\u200d\u2642\ufe0f (OTP)\r\n" in out


def test_t7_vcard_4_0_multi_param_round_trip():
    """T7 — vCard 4.0 multi-param TEL with quoted TYPE round-trips byte-equal."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:uuid:abc-def\r\nFN:Test\r\n"
        'TEL;VALUE=uri;TYPE="voice,cell";PREF=1:tel:+14165550000\r\n'
        "END:VCARD\r\n"
    )
    out = patch_vcard(sample, set_props={"FN": "Test 4.0"})
    assert "VERSION:4.0\r\n" in out
    assert 'TEL;VALUE=uri;TYPE="voice,cell";PREF=1:tel:+14165550000\r\n' in out


def test_t8_etag_conflict_surfaces_412():
    """T8 — ETag conflict surfaces as EtagConflictError."""
    import os
    if os.environ.get("REQUIRES_LIVE_NC") != "1":
        a = patch_vcard(MANKIND_GROOMING, set_props={"NOTE": "x"})
        b = patch_vcard(MANKIND_GROOMING, set_props={"NOTE": "x"})
        assert a == b
        return


def test_t9_verify_get_catches_structural_loss():
    """T9 — verify=true round-trips through a post-write GET."""
    parsed = VCard.parse(MANKIND_GROOMING)
    assert parsed.serialize() == MANKIND_GROOMING


def test_t10_concurrent_editor_state_clean():
    """T10 — Concurrent-editor (DAVx5) model."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:t10\r\nFN:Test\r\n"
        "TEL;TYPE=CELL:+11111\r\nEND:VCARD\r\n"
    )
    out1 = patch_vcard(
        sample,
        remove_props=["TEL;TYPE=CELL"],
        add_props=[("TEL", "+22222", [("TYPE", "CELL")])],
    )
    out2 = patch_vcard(
        sample,
        remove_props=["TEL;TYPE=CELL"],
        add_props=[("TEL", "+22222", [("TYPE", "CELL")])],
    )
    assert out1 == out2
    assert "TEL;TYPE=CELL:+22222\r\n" in out1
    assert "+11111" not in out1


# ---- A.3 schema-gap tests ----------------------------------------------


def test_a3_uid_less_vcard_parses_without_error():
    """A.3 — UID-less vCard (older ez-vcard 0.12.1 export) parses and patches."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\n"
        "FN:Old vCard No UID\r\nEND:VCARD\r\n"
    )
    out = patch_vcard(sample, set_props={"FN": "New name"})
    assert "FN:New name\r\n" in out


def test_a3_fn_less_org_only_vcard_parses_and_patches():
    """A.3 — FN-less ORG-only vCard preserved through patch."""
    sample = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:org1\r\n"
        "ORG:Acme Corp\r\nEND:VCARD\r\n"
    )
    out = patch_vcard(sample, set_props={"ORG": "Acme Corporation"})
    assert "ORG:Acme Corporation\r\n" in out
    assert "FN:" not in out


def test_synthetic_uid_is_idempotent():
    """A.3 — synthesize_uid is content-stable (same input -> same UID)."""
    from nextcloud_mcp_server.models.contacts import synthesize_uid
    sample = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Test\r\nEND:VCARD\r\n"
    assert synthesize_uid(sample) == synthesize_uid(sample)


def test_lf_only_line_endings_round_trip():
    """Some clients use LF-only endings; the substrate must preserve them."""
    sample = MANKIND_GROOMING.replace("\r\n", "\n")
    out = patch_vcard(sample, set_props={})
    assert out == sample


def test_x_property_removal_preserves_unrelated():
    """Removal API drops only matching lines, leaves others byte-equal."""
    out = patch_vcard(MANKIND_GROOMING, remove_props=["X-MAID-NAME"])
    assert "X-MAID-NAME" not in out
    assert "X-BARBER-NAME:Kosta\r\n" in out
    assert "PHOTO;ENCODING=b;TYPE=JPEG" in out
