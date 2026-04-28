"""Byte-preserving line-oriented vCard parser.

Implements DAVx5-style read-modify-write semantics: parse a vCard into
logical lines while preserving every untouched line's original bytes
verbatim. When a property is modified, regenerate only that line per
RFC 6350 §3.2 (property-line syntax) and RFC 5545 §3.1 (line folding).
Untouched lines (PHOTO blobs, X-properties, line-folded NOTEs, vendor
extensions) round-trip byte-equal.

This is the load-bearing implementation behind nc_contacts_patch_contact /
put_contact; the JSON↔vCard round-trip pattern in the legacy update_contact
silently dropped any property not represented in the JSON model.

Verified against the Mankind Grooming case (PHOTO + cell + barber-name
clobber) and the test corpus T1-T10 in NC-MCP-Implementation.md.

References:
- RFC 6350 (vCard 4.0): https://datatracker.ietf.org/doc/html/rfc6350
- RFC 2426 (vCard 3.0): https://datatracker.ietf.org/doc/html/rfc2426
- RFC 5545 §3.1 (line folding):
  https://datatracker.ietf.org/doc/html/rfc5545#section-3.1
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

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# RFC 5545 §3.1 / RFC 6350 §3.2: lines SHOULD be ≤ 75 octets; longer lines
# are folded by inserting CRLF followed by a single linear-white-space.
LINE_FOLD_LIMIT = 75

# A fold-continuation begins with SPACE (0x20) or HTAB (0x09).
_CONTINUATION = (" ", "\t")


@dataclass
class VCardLine:
    """A single logical (unfolded) vCard line.

    ``original_bytes`` carries the line as it appeared in the source —
    including line folds, original line endings, and the case/whitespace
    of property names and parameters. When this line is unmodified during
    edit, ``original_bytes`` is emitted verbatim. When ``modified`` is
    True, the line is regenerated from ``name`` / ``params`` / ``value``.

    ``name`` is the upper-cased property name (e.g. ``PHOTO``). ``params``
    is an ordered list of (key, value) tuples preserving param order and
    case for downstream display, but the parser uppercases keys for
    matching. ``value`` is the raw post-colon bytes (NOT line-unfolded
    once we've assembled the logical line; folds within the value are
    already removed by the unfolder).
    """

    name: str
    params: list[tuple[str, str]] = field(default_factory=list)
    value: str = ""
    original_bytes: str = ""
    modified: bool = False

    @property
    def param_keys_upper(self) -> set[str]:
        return {k.upper() for k, _ in self.params}

    def has_param(self, key: str, value: Optional[str] = None) -> bool:
        """Check whether this line has a parameter (optionally with a value)."""
        key_u = key.upper()
        for k, v in self.params:
            if k.upper() != key_u:
                continue
            if value is None:
                return True
            # TYPE parameters can appear comma-joined (e.g. TYPE=WORK,VOICE)
            if value.upper() in (s.upper() for s in v.split(",")):
                return True
        return False

    def selector_key(self) -> str:
        """A stable selector for matching/replacing this line.

        Default selector is the property name. Multi-value properties (TEL,
        EMAIL, ADR) include the TYPE parameter when present so callers can
        target a specific entry: ``TEL;TYPE=CELL`` vs ``TEL;TYPE=WORK``.
        """
        types = []
        for k, v in self.params:
            if k.upper() == "TYPE":
                types.extend(t.upper() for t in v.split(","))
        if types:
            return f"{self.name};TYPE={','.join(sorted(types))}"
        return self.name


class VCard:
    """A parsed vCard preserving original byte sequences for round-trip.

    Use :meth:`parse` to construct from raw text, manipulate via
    :meth:`set_property`, :meth:`add_property`, :meth:`remove_property`,
    then :meth:`serialize` to emit. Unchanged lines emit byte-identically;
    changed lines are regenerated under RFC 6350/5545 syntax + folding.
    """

    def __init__(self, lines: list[VCardLine], line_ending: str = "\r\n"):
        self.lines = lines
        # Track the dominant line ending observed in the input so emission
        # preserves CRLF vs LF (CardDAV servers typically emit CRLF; some
        # clients use LF — we pin to whatever the server gave us).
        self.line_ending = line_ending

    # --- parsing ---------------------------------------------------------

    @classmethod
    def parse(cls, raw: str) -> "VCard":
        """Parse raw vCard text into logical lines preserving original bytes.

        Algorithm:
        1. Split on line endings, preserving them so we know the dominant
           ending in the source.
        2. Walk lines: if a line starts with SPACE/HTAB, append it to the
           previous logical line's original_bytes AND strip the leading
           whitespace before appending to the value half (RFC 5545 unfold).
        3. For each logical line, parse name/params/value via the
           property-line grammar.
        """
        if not raw:
            raise ValueError("empty vCard input")

        # Detect the dominant line ending.
        crlf_count = raw.count("\r\n")
        lf_count = raw.count("\n") - crlf_count
        line_ending = "\r\n" if crlf_count >= lf_count else "\n"

        # Split keeping the line endings as separate pieces.
        # Pattern: any chars (non-greedy) followed by \r\n or \n.
        parts = re.split(r"(\r\n|\n)", raw)
        # parts is [content, ending, content, ending, ..., trailing_or_empty]

        # Reassemble into (content, ending) pairs.
        raw_lines: list[tuple[str, str]] = []
        i = 0
        while i < len(parts):
            content = parts[i] if i < len(parts) else ""
            ending = parts[i + 1] if (i + 1) < len(parts) else ""
            if content == "" and ending == "":
                break
            raw_lines.append((content, ending))
            i += 2

        # Unfold: build logical lines by appending fold-continuations to the
        # prior logical line. Track original bytes per logical line.
        logical: list[tuple[str, str]] = []  # (logical_value, original_bytes)
        for content, ending in raw_lines:
            if not content and not ending:
                continue
            if logical and content and content[0] in _CONTINUATION:
                # Fold continuation: append (without the leading whitespace)
                # to the prior logical value, but keep the original bytes
                # (CRLF + leading-whitespace + content) on the original side.
                prev_value, prev_orig = logical[-1]
                logical[-1] = (
                    prev_value + content[1:],
                    prev_orig + content + ending,
                )
                continue
            # Skip empty lines between logical lines (some servers add them
            # before END:VCARD on Windows-edited vCards).
            if not content.strip():
                if logical:
                    prev_value, prev_orig = logical[-1]
                    logical[-1] = (prev_value, prev_orig + content + ending)
                continue
            logical.append((content, content + ending))

        # Parse each logical line.
        out: list[VCardLine] = []
        for value_text, original_bytes in logical:
            line = _parse_logical_line(value_text, original_bytes)
            out.append(line)

        # Sanity: BEGIN:VCARD ... END:VCARD bracketing.
        if not out or out[0].name != "BEGIN" or out[-1].name != "END":
            logger.warning(
                "vCard parse: missing BEGIN/END envelope — accepting anyway "
                "for forensic round-trip but downstream consumers may fail"
            )

        return cls(out, line_ending=line_ending)

    # --- mutation --------------------------------------------------------

    def find(self, selector: str) -> list[int]:
        """Return indexes of all lines matching the selector.

        Selector forms:
        - ``"FN"`` — match by property name only
        - ``"TEL;TYPE=CELL"`` — match by name + type parameter (one or
          more types comma-joined; matches if the line carries every
          requested type)
        """
        if ";" not in selector:
            wanted_name = selector.upper()
            return [
                i for i, line in enumerate(self.lines) if line.name == wanted_name
            ]
        name, params_str = selector.split(";", 1)
        wanted_name = name.upper()
        # Extract requested TYPE values.
        wanted_types: set[str] = set()
        for chunk in params_str.split(";"):
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            if k.upper() != "TYPE":
                continue
            wanted_types.update(t.strip().upper() for t in v.split(","))
        out = []
        for i, line in enumerate(self.lines):
            if line.name != wanted_name:
                continue
            line_types: set[str] = set()
            for k, v in line.params:
                if k.upper() == "TYPE":
                    line_types.update(t.strip().upper() for t in v.split(","))
            if wanted_types.issubset(line_types):
                out.append(i)
        return out

    def set_property(self, selector: str, value: str) -> bool:
        """Replace value of single matching line. Returns True if replaced."""
        indexes = self.find(selector)
        if not indexes:
            return False
        # If multiple match, replace the first to preserve "primary" semantics
        # (matches the patch_contact ergonomics for TEL;TYPE=CELL etc.).
        idx = indexes[0]
        line = self.lines[idx]
        line.value = value
        line.modified = True
        return True

    def add_property(
        self, name: str, value: str, params: Optional[list[tuple[str, str]]] = None
    ) -> None:
        """Append a new property line before END:VCARD (or at the end if no END)."""
        new_line = VCardLine(
            name=name.upper(),
            params=list(params or []),
            value=value,
            modified=True,
        )
        # Insert before END:VCARD if present.
        for i in range(len(self.lines) - 1, -1, -1):
            if self.lines[i].name == "END":
                self.lines.insert(i, new_line)
                return
        self.lines.append(new_line)

    def remove_property(self, selector: str) -> int:
        """Remove all lines matching the selector. Returns count removed."""
        indexes = self.find(selector)
        if not indexes:
            return 0
        # Remove in reverse so earlier indexes remain valid.
        for idx in reversed(indexes):
            self.lines[idx].modified = True
            self.lines[idx].name = "__REMOVED__"
        # Drop sentinels after marking — keeps original_bytes elsewhere intact.
        before = len(self.lines)
        self.lines = [line for line in self.lines if line.name != "__REMOVED__"]
        return before - len(self.lines)

    # --- serialization ---------------------------------------------------

    def serialize(self) -> str:
        """Emit vCard text. Unmodified lines are byte-identical to input."""
        chunks: list[str] = []
        for line in self.lines:
            if not line.modified and line.original_bytes:
                chunks.append(line.original_bytes)
                continue
            chunks.append(_emit_logical_line(line, self.line_ending))
        return "".join(chunks)


# ---- internal helpers ---------------------------------------------------


# Property line grammar (RFC 6350 §3.3):
# contentline = name *(";" param) ":" value
# name = x-name / iana-token
# param = param-name "=" param-value *("," param-value)
#
# Values may contain colons (e.g. URLs) — we split on the FIRST unquoted colon
# only. Parameters may be quoted ("..."); we honour quote escaping for the
# colon search.
_PROP_LINE_RE = re.compile(
    r"""
    ^
    ([\w\-]+)             # group 1: property name
    (.*?)               # group 2: optional ;params (lazy, may be empty)
    :                   # the : delimiter (handled by _split_at_unquoted_colon)
    (.*)                # group 3: value
    $
    """,
    re.VERBOSE | re.DOTALL,
)


def _split_at_unquoted_colon(text: str) -> tuple[str, str]:
    """Return (head, tail) split at the first colon outside double-quotes."""
    in_quote = False
    for i, ch in enumerate(text):
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch == ":" and not in_quote:
            return text[:i], text[i + 1 :]
    return text, ""


def _parse_logical_line(text: str, original_bytes: str) -> VCardLine:
    """Parse a logical (unfolded) vCard line."""
    head, value = _split_at_unquoted_colon(text)
    if not head:
        # No colon found — treat the whole thing as a name with empty value.
        # Defensive against malformed input; original_bytes still round-trips.
        return VCardLine(name=text.strip().upper(), original_bytes=original_bytes)

    # head looks like NAME[;PARAM=VAL[;PARAM=VAL]...]
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: list[tuple[str, str]] = []
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params.append((k.strip(), v))
        else:
            # bare parameter (RFC 2425 syntax) — e.g. TEL;HOME:... in older
            # vCard 2.1 dialects. Promote to TYPE=<bare>.
            params.append(("TYPE", p.strip()))
    return VCardLine(
        name=name, params=params, value=value, original_bytes=original_bytes
    )


def _emit_logical_line(line: VCardLine, line_ending: str) -> str:
    """Emit a (potentially modified) logical line with RFC 5545 folding."""
    head = line.name
    for k, v in line.params:
        # Parameter values containing ":", ";", or "," need quoting per
        # RFC 6350 §3.3. We don't re-quote whitespace-bearing values that
        # were already unquoted in the source — that would be a behaviour
        # change. For modified lines we play conservative: quote when the
        # value contains a delimiter character.
        if any(ch in v for ch in (":", ";", ",")) and not (
            v.startswith('"') and v.endswith('"')
        ):
            v = f'"{v}"'
        head += f";{k}={v}"
    raw = f"{head}:{line.value}"
    return _fold(raw, line_ending) + line_ending


def _fold(content: str, line_ending: str) -> str:
    """Fold a line at LINE_FOLD_LIMIT octets per RFC 5545 §3.1.

    Note: this counts characters, not bytes. For ASCII-only content these
    coincide; for multi-byte UTF-8 the limit is approximate but well within
    the safety margin of all known CardDAV servers.
    """
    if len(content) <= LINE_FOLD_LIMIT:
        return content
    out_parts: list[str] = []
    cursor = 0
    while cursor < len(content):
        chunk = content[cursor : cursor + LINE_FOLD_LIMIT]
        out_parts.append(chunk)
        cursor += LINE_FOLD_LIMIT
    return (line_ending + " ").join(out_parts)


# ---- ergonomic helpers --------------------------------------------------


def patch_vcard(
    raw: str,
    *,
    set_props: Optional[dict[str, str]] = None,
    add_props: Optional[Iterable[tuple[str, str, Optional[list[tuple[str, str]]]]]] = None,
    remove_props: Optional[Iterable[str]] = None,
) -> str:
    """One-shot byte-preserving patch of a raw vCard.

    Args:
        raw: original vCard text from CardDAV (verbatim).
        set_props: ``{selector: new_value}`` — replace single matching line.
            Selector form: ``"FN"`` or ``"TEL;TYPE=CELL"``.
        add_props: iterable of ``(name, value, params or None)`` tuples to
            append before END:VCARD. ``params`` is a list of ``(key, value)``.
        remove_props: iterable of selectors to remove (all matches).

    Returns:
        New vCard text with untouched properties byte-identical to the input.
    """
    vcard = VCard.parse(raw)
    if remove_props:
        for sel in remove_props:
            vcard.remove_property(sel)
    if set_props:
        for sel, new_value in set_props.items():
            if not vcard.set_property(sel, new_value):
                # Auto-add on set when the property is absent — caller
                # ergonomic; matches DAVx5 semantics.
                if ";" in sel:
                    name, params_str = sel.split(";", 1)
                    params = [
                        tuple(p.split("=", 1)) for p in params_str.split(";") if "=" in p
                    ]
                    vcard.add_property(name, new_value, params)
                else:
                    vcard.add_property(sel, new_value)
    if add_props:
        for name, value, params in add_props:
            vcard.add_property(name, value, params or [])
    return vcard.serialize()
