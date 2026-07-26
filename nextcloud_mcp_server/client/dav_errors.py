"""Typed WebDAV/CalDAV/CardDAV error surfacing.

Nextcloud's DAV layer (Sabre/DAV) answers a failed request with an XML body
naming the concrete failure::

    <?xml version="1.0" encoding="utf-8"?>
    <d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">
      <s:exception>Sabre\\DAV\\Exception\\Locked</s:exception>
      <s:message>File is currently write locked</s:message>
    </d:error>

The HTTP status alone under-specifies the cause: a bare "412 Precondition
Failed" says nothing about *which* precondition failed, and a 403 can mean
anything from a share permission to a blocked filename. Discarding that body
is the difference between an actionable error and a shrug, so this module
lifts the ``s:exception``/``s:message`` pair into the raised exception and
maps the three statuses that need distinct handling onto their own types:

===  ==============================  =======================================
412  :class:`DavPreconditionFailed`  ``If-Match`` ETag no longer current
423  :class:`DavLocked`              resource write-locked by another client
507  :class:`DavInsufficientStorage` quota exhausted
===  ==============================  =======================================

Every type derives from :class:`httpx.HTTPStatusError`, so existing
``except HTTPStatusError`` handlers keep working untouched; callers that want
to branch on the failure mode catch the specific subclass instead.
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

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional, Type, Union

from httpx import HTTPStatusError, Response, ResponseNotRead

#: Namespaces carried by a Sabre/DAV error document.
DAV_NAMESPACE = "DAV:"
SABREDAV_NAMESPACE = "http://sabredav.org/ns"

#: Upper bound on the body we will hand to the XML parser. DAV error documents
#: are a few hundred bytes; anything larger is not one, and parsing it would
#: turn a failed request into an unbounded amount of work.
MAX_ERROR_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class DavErrorDetail:
    """The ``s:exception``/``s:message`` pair from a DAV error document."""

    exception: Optional[str] = None
    message: Optional[str] = None

    def describe(self) -> str:
        """Render as ``"<exception>: <message>"``, omitting missing halves."""
        if self.exception and self.message:
            return f"{self.exception}: {self.message}"
        return self.exception or self.message or ""


class DavError(HTTPStatusError):
    """A DAV request that failed, with the server's own explanation attached.

    Subclasses :class:`httpx.HTTPStatusError` so it stays catchable by the
    handlers that predate this module.
    """

    def __init__(
        self,
        message: str,
        *,
        request,
        response,
        detail: Optional[DavErrorDetail] = None,
    ):
        super().__init__(message, request=request, response=response)
        self.detail = detail


class DavPreconditionFailed(DavError):
    """412 — an ``If-Match``/``If-None-Match`` precondition did not hold."""


class DavLocked(DavError):
    """423 — the resource is write-locked by another client."""


class DavInsufficientStorage(DavError):
    """507 — the write would exceed the account or folder quota."""


#: Statuses that get their own type and a remediation hint. Kept as a literal
#: table rather than a chain of ``if``s so adding a status is a one-line edit.
_STATUS_MAP: dict[int, tuple[Type[DavError], str]] = {
    412: (
        DavPreconditionFailed,
        "the If-Match ETag no longer matches the server copy (the resource "
        "was modified by another writer) — re-read it and re-apply the change",
    ),
    423: (
        DavLocked,
        "the resource is locked by another client — retry once the lock is "
        "released, or clear it from the Files app",
    ),
    507: (
        DavInsufficientStorage,
        "insufficient storage — the account or folder quota is exhausted",
    ),
}


def _decode_body(body: Union[bytes, str, None]) -> Optional[str]:
    """Return ``body`` as text if it is small enough to be an error document."""
    if body is None:
        return None
    if isinstance(body, bytes):
        if len(body) > MAX_ERROR_BODY_BYTES:
            return None
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        if len(body.encode("utf-8", errors="replace")) > MAX_ERROR_BODY_BYTES:
            return None
        return body
    # Anything else (a mock, a stream) is not something we can parse.
    return None


def _local_name(tag: object) -> str:
    """Strip the ``{namespace}`` prefix from an ElementTree tag."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def parse_dav_error(body: Union[bytes, str, None]) -> Optional[DavErrorDetail]:
    """Extract the exception/message pair from a Sabre/DAV error document.

    Args:
        body: Raw response body. Non-XML, oversized, or non-DAV bodies are not
            an error condition here — they simply yield ``None``.

    Returns:
        The parsed detail, or ``None`` if ``body`` is not a DAV error document
        or carries neither element.
    """
    text = _decode_body(body)
    if not text:
        return None

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    # Sabre wraps every failure in <d:error>; a multistatus or an unrelated
    # XML document is not ours to interpret.
    if _local_name(root.tag) != "error":
        return None

    exception: Optional[str] = None
    message: Optional[str] = None
    for element in root.iter():
        name = _local_name(element.tag)
        text_value = (element.text or "").strip()
        if not text_value:
            continue
        if name == "exception" and exception is None:
            exception = text_value
        elif name == "message" and message is None:
            message = text_value

    if exception is None and message is None:
        return None
    return DavErrorDetail(exception=exception, message=message)


def _response_body(response: Response) -> Union[bytes, None]:
    """Read an already-buffered response body, tolerating a streamed one."""
    try:
        return response.content
    except ResponseNotRead:
        # A streaming response has not been read; there is nothing to parse and
        # consuming it here would steal the caller's body.
        return None


def _request_summary(response: Response) -> str:
    """Describe the failed request as ``"<METHOD> <path>"`` where possible."""
    request = getattr(response, "request", None)
    method = getattr(request, "method", None)
    url = getattr(request, "url", None)
    if not isinstance(method, str) or url is None:
        return "DAV request"
    return f"{method} {url}"


def dav_error_from_status_error(exc: HTTPStatusError) -> Optional[DavError]:
    """Build the typed DAV error for ``exc``, or ``None`` if it is not one.

    A response qualifies only when its body is a valid Sabre/DAV error
    document. Status alone is insufficient because JSON/REST endpoints also
    use 412/423/507 for non-DAV failures. WebDAV callers that own the protocol
    boundary may still classify a bare status there.

    Args:
        exc: The ``HTTPStatusError`` raised by ``raise_for_status``.

    Returns:
        A :class:`DavError` (or subclass) carrying the parsed detail, or
        ``None`` when the response is not DAV-shaped.
    """
    response = exc.response
    status_code = getattr(response, "status_code", None)
    detail = parse_dav_error(_response_body(response))
    if detail is None:
        return None

    error_type: Type[DavError] = DavError
    hint = ""
    if isinstance(status_code, int) and status_code in _STATUS_MAP:
        error_type, hint = _STATUS_MAP[status_code]

    described = detail.describe()
    parts = [f"{status_code} on {_request_summary(response)}"]
    if hint:
        parts.append(hint)
    if described:
        parts.append(described)
    message = ": ".join(parts)

    return error_type(
        message,
        request=exc.request,
        response=response,
        detail=detail,
    )
