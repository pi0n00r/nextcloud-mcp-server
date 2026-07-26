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

"""Strict HTTP entity-tag validation shared by DAV clients."""


class StrongEntityTagError(ValueError):
    """Raised when an If-Match value is not exactly one strong entity-tag."""


def require_strong_entity_tag(etag: str | None, *, operation: str) -> str:
    """Return *etag* verbatim if it is exactly one valid strong entity-tag."""
    if etag is None or not etag.strip():
        raise StrongEntityTagError(
            f"{operation} requires a non-blank strong ETag for If-Match"
        )
    if etag.startswith("W/"):
        raise StrongEntityTagError(
            f"{operation} requires a strong ETag; weak ETags cannot be used "
            "with If-Match"
        )
    if (
        len(etag) < 2
        or etag[0] != '"'
        or etag[-1] != '"'
        or any(
            char == '"' or ord(char) < 0x21 or ord(char) == 0x7F or ord(char) > 0xFF
            for char in etag[1:-1]
        )
    ):
        raise StrongEntityTagError(
            f"{operation} requires exactly one syntactically valid strong "
            "HTTP entity-tag"
        )
    return etag
