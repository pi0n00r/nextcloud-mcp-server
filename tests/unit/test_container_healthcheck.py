from pathlib import Path

import yaml

from nextcloud_mcp_server.container_healthcheck import (
    DEFAULT_PORT,
    HEALTH_HOST,
    resolve_health_port,
)

REPO_ROOT = Path(__file__).parents[2]


def test_health_port_defaults_compatibly_to_8000():
    assert DEFAULT_PORT == 8000
    assert resolve_health_port([], {}) == 8000


def test_health_port_uses_port_environment():
    assert resolve_health_port([], {"PORT": "9000"}) == 9000


def test_explicit_cli_port_matches_actual_listener_before_environment():
    argv = ["nextcloud-mcp-server", "run", "--port", "8002"]
    assert resolve_health_port(argv, {"PORT": "9000"}) == 8002


def test_probe_uses_dual_stack_localhost_name():
    assert HEALTH_HOST == "localhost"


def test_dockerfile_runs_dynamic_internal_probe():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "ENV PORT=8000" in dockerfile
    assert (
        'CMD ["/app/.venv/bin/python", "-m", '
        '"nextcloud_mcp_server.container_healthcheck"]' in dockerfile
    )
    assert "127.0.0.1:8000/health/live" not in dockerfile


def test_shipped_compose_profile_ports_match_health_resolution():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())

    for service_name, expected_port in (
        ("mcp-keycloak", 8002),
        ("mcp-login-flow", 8004),
    ):
        service = compose["services"][service_name]
        environment = dict(item.split("=", 1) for item in service["environment"])
        argv = ["nextcloud-mcp-server", "run", *service["command"]]

        assert environment["PORT"] == str(expected_port)
        assert resolve_health_port(argv, environment) == expected_port
