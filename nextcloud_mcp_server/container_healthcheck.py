"""Container-internal liveness probe that follows the configured server port."""

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

import http.client
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

DEFAULT_PORT = 8000
HEALTH_HOST = "localhost"


def _pid1_argv() -> list[str]:
    """Read the container entrypoint arguments without invoking process tools."""
    try:
        raw = Path("/proc/1/cmdline").read_bytes()
    except OSError:
        return []
    return [item.decode(errors="replace") for item in raw.split(b"\0") if item]


def _option_value(argv: Sequence[str], *names: str) -> str | None:
    for index, argument in enumerate(argv):
        if argument in names and index + 1 < len(argv):
            return argv[index + 1]
        for name in names:
            prefix = f"{name}="
            if argument.startswith(prefix):
                return argument[len(prefix) :]
    return None


def _valid_port(value: object) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def resolve_health_port(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve the actual CLI port, then ``PORT``, then the compatible default."""
    args = _pid1_argv() if argv is None else argv
    env = os.environ if environ is None else environ
    return (
        _valid_port(_option_value(args, "--port", "-p"))
        or _valid_port(env.get("PORT"))
        or DEFAULT_PORT
    )


def main() -> int:
    port = resolve_health_port()
    connection = http.client.HTTPConnection(HEALTH_HOST, port, timeout=5)
    try:
        connection.request("GET", "/health/live")
        response = connection.getresponse()
        response.read()
        if 200 <= response.status < 300:
            return 0
        print(
            f"health probe returned HTTP {response.status} on {HEALTH_HOST}:{port}",
            file=sys.stderr,
        )
    except OSError as exc:
        print(f"health probe failed on {HEALTH_HOST}:{port}: {exc}", file=sys.stderr)
    finally:
        connection.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
