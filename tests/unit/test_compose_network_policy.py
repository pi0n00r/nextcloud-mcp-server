"""Regression coverage for fleet-compatible Compose port publications."""

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

import re
from collections import defaultdict
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

COMPOSE_FILE = Path(__file__).parents[2] / "docker-compose.yml"
SHORT_PORT = re.compile(
    r"^(?P<host>\[[^]]+]|[^:]+):(?P<published>\d+):(?P<target>\d+)"
    r"(?:/(?P<protocol>tcp|udp))?$"
)


def _port_binding(port: Any) -> tuple[str, int, int, str]:
    if isinstance(port, str):
        match = SHORT_PORT.fullmatch(port)
        assert match is not None, f"port publication must name a host address: {port!r}"
        return (
            match.group("host").strip("[]"),
            int(match.group("published")),
            int(match.group("target")),
            match.group("protocol") or "tcp",
        )

    assert isinstance(port, dict), f"unsupported Compose port publication: {port!r}"
    host = port.get("host_ip")
    assert host, f"long-form port publication must set host_ip: {port!r}"
    return (
        str(host).strip("[]"),
        int(port["published"]),
        int(port["target"]),
        str(port.get("protocol", "tcp")),
    )


def test_every_compose_port_is_explicitly_dual_stack() -> None:
    """Each published socket must have matching IPv4 and IPv6 bindings."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))

    for service_name, service in compose["services"].items():
        bindings: defaultdict[tuple[int, int, str], list[str]] = defaultdict(list)
        for port in service.get("ports", []):
            host, published, target, protocol = _port_binding(port)
            bindings[(published, target, protocol)].append(host)

        for socket, hosts in bindings.items():
            addresses = [ip_address(host) for host in hosts]
            families = {address.version for address in addresses}
            assert families == {4, 6}, (
                f"{service_name} port {socket} must publish on both IPv4 and IPv6; "
                f"found {hosts}"
            )
            assert len({address.is_loopback for address in addresses}) == 1, (
                f"{service_name} port {socket} mixes loopback and non-loopback bindings: "
                f"{hosts}"
            )
