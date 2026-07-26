"""OCS envelope handling for Nextcloud's ``/ocs/v2.php`` API.

Every OCS response wraps its payload in a status envelope::

    {"ocs": {"meta": {"status": "ok", "statuscode": 200, "message": "OK"},
             "data": {...}}}

Two properties of that envelope bite callers who only check the HTTP status:

* **The v1 trap.** ``/ocs/v1.php`` answers *every* request with HTTP 200 — the
  real outcome lives in ``meta.statuscode``, where success is ``100``.
  ``/ocs/v2.php`` mirrors the OCS code onto the HTTP status and uses ``200``
  for success. This module accepts both success codes so a helper written
  against v2 still behaves correctly if it is ever pointed at a v1 route, and
  so the envelope is checked either way. New call sites should use v2 paths.

* **997 is not a server error.** Nextcloud returns ``997`` when the request was
  not authenticated *or* when it omitted the mandatory
  ``OCS-APIRequest: true`` header, which its CSRF check requires on every OCS
  call. Reporting that as a generic failure sends the reader hunting for a
  server-side fault that is not there, so it gets its own exception type and a
  message that names both causes.

:class:`OCSError` derives from ``RuntimeError``, matching what the OCS clients
raised before this module existed.
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

from typing import Any, Optional

#: Header Nextcloud's CSRF check requires on every OCS request. Omitting it
#: yields ``meta.statuscode: 997``, not a 4xx, which is why it is easy to
#: misdiagnose.
OCS_API_REQUEST_HEADER = {"OCS-APIRequest": "true"}

#: Success codes: ``100`` from OCS v1, ``200`` from v2.
OCS_SUCCESS_STATUS_CODES = frozenset({100, 200})

#: "Unauthorised" in OCS's vocabulary — bad credentials *or* a missing
#: ``OCS-APIRequest`` header.
OCS_STATUS_UNAUTHENTICATED = 997

_UNAUTHENTICATED_HINT = (
    "unauthenticated — the credentials were rejected, or the request omitted "
    "the required 'OCS-APIRequest: true' header that Nextcloud's CSRF check "
    "enforces on OCS routes"
)


class OCSError(RuntimeError):
    """An OCS response whose envelope reported a failure.

    Attributes:
        status_code: The ``ocs.meta.statuscode`` value, when present.
        ocs_message: The server's ``ocs.meta.message``, when present.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        ocs_message: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.ocs_message = ocs_message


class OCSAuthenticationError(OCSError):
    """``statuscode: 997`` — rejected credentials or a missing OCS header."""


def _meta(payload: Any) -> dict:
    """Return ``payload["ocs"]["meta"]`` defensively.

    ``x or {}`` rather than ``.get(k, {})`` so a present-but-null ``ocs`` or
    ``meta`` (which Nextcloud does emit) coerces to an empty dict instead of
    raising ``AttributeError`` on ``None.get``.
    """
    if not isinstance(payload, dict):
        return {}
    ocs = payload.get("ocs") or {}
    if not isinstance(ocs, dict):
        return {}
    meta = ocs.get("meta") or {}
    return meta if isinstance(meta, dict) else {}


def raise_for_ocs_status(payload: Any, *, context: str = "OCS API") -> None:
    """Raise if the OCS envelope in ``payload`` reports a failure.

    A payload carrying no ``meta.statuscode`` is treated as success: some
    endpoints (and every hand-rolled fixture) omit the meta block, and
    inventing a failure from its absence would be worse than not checking.

    Args:
        payload: Decoded JSON body of an OCS response.
        context: Operation name used in the raised message.

    Raises:
        OCSAuthenticationError: On ``statuscode: 997``.
        OCSError: On any other non-success ``statuscode``.
    """
    meta = _meta(payload)
    status_code = meta.get("statuscode")
    if not isinstance(status_code, int):
        return
    if status_code in OCS_SUCCESS_STATUS_CODES:
        return

    ocs_message = meta.get("message")
    detail = ocs_message if isinstance(ocs_message, str) else "Unknown error"

    if status_code == OCS_STATUS_UNAUTHENTICATED:
        raise OCSAuthenticationError(
            f"{context} error (code {status_code}): {_UNAUTHENTICATED_HINT} "
            f"[server said: {detail}]",
            status_code=status_code,
            ocs_message=ocs_message if isinstance(ocs_message, str) else None,
        )

    raise OCSError(
        f"{context} error (code {status_code}): {detail}",
        status_code=status_code,
        ocs_message=ocs_message if isinstance(ocs_message, str) else None,
    )


def ocs_data(payload: Any, *, context: str = "OCS API") -> Any:
    """Validate the envelope and return ``payload["ocs"]["data"]``.

    Args:
        payload: Decoded JSON body of an OCS response.
        context: Operation name used in any raised message.

    Raises:
        OCSAuthenticationError: On ``statuscode: 997``.
        OCSError: On any other non-success ``statuscode``, or when the response
            carries no ``ocs.data`` key at all.
    """
    raise_for_ocs_status(payload, context=context)

    ocs = payload.get("ocs") if isinstance(payload, dict) else None
    if not isinstance(ocs, dict) or "data" not in ocs:
        raise OCSError(f"{context}: response carried no ocs.data payload")
    return ocs["data"]
