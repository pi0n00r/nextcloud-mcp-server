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

"""Chunk-viewer deep links for semantic search results.

The Astrolabe frontend opens its chunk viewer from query parameters on the app
root (``src/App.vue``'s ``handleUrlParameters``), and strips them via
``history.replaceState`` once the viewer is open. This module is the only place
that *builds* such a link; the parser has existed for far longer than any
generator.
"""

from urllib.parse import urlencode

# Astrolabe's app root. The chunk viewer is opened by query parameters on it —
# the app has no vue-router, so there is no per-document path to link to.
ASTROLABE_APP_PATH = "/index.php/apps/astrolabe/"


def chunk_url(
    base: str | None,
    *,
    doc_type: str,
    doc_id: int | str,
    chunk_start: int | None,
    chunk_end: int | None,
    title: str | None = None,
    path: str | None = None,
    page_number: int | None = None,
    chunk_index: int | None = None,
    total_chunks: int | None = None,
    extra: dict[str, str] | None = None,
) -> str | None:
    """Build a link that opens ``doc_id``'s chunk in the Astrolabe chunk viewer.

    ``base`` comes from :func:`nextcloud_mcp_server.links.browser_base`; it is a
    parameter rather than an internal call so a search resolves it once instead
    of once per result.

    Returns None when there is no base URL, or when either chunk offset is
    missing — Astrolabe requires ``doc_type``, ``doc_id``, ``chunk_start`` and
    ``chunk_end`` together, and opens nothing if any is absent, so a link
    without them would be a dead end rather than a degraded one.

    ``extra`` carries per-doc_type access-recheck identifiers (``board_id``,
    ``mailbox_id``, ``calendar_uri``) so a stale link gets the same local access
    check as a live search result.
    """
    if not base or chunk_start is None or chunk_end is None:
        return None

    params: dict[str, str] = {
        "doc_type": doc_type,
        "doc_id": str(doc_id),
        "chunk_start": str(chunk_start),
        "chunk_end": str(chunk_end),
    }
    # `extra` is spread FIRST so the named parameters win a key collision: they
    # are the typed, documented surface, and an opaque caller-supplied dict
    # should not be able to quietly redefine `title` or `path`.
    optional = {
        **(extra or {}),
        "title": title,
        "path": path,
        "page_number": page_number,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
    }
    # Omit unset values rather than emitting `page_number=None`, which Astrolabe
    # would parseInt into NaN.
    params.update({k: str(v) for k, v in optional.items() if v is not None})

    # urlencode quotes the spaces and slashes that real titles and paths carry.
    return f"{base}{ASTROLABE_APP_PATH}?{urlencode(params)}"
