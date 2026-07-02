# Container Package

The published container package is the old-stable release image:

```bash
ghcr.io/pi0n00r/nextcloud-mcp-server:v1.1.8
```

The image is published as an OCI-compatible multi-architecture image for
`linux/amd64` and `linux/arm64`. It can be pulled with Docker or Podman.

## Podman Quick Start

Create an environment file outside the repository:

```bash
mkdir -p ~/.config/nextcloud-mcp
cat > ~/.config/nextcloud-mcp/env <<'EOF'
NEXTCLOUD_HOST=https://your.nextcloud.instance.com
NEXTCLOUD_USERNAME=your_username
NEXTCLOUD_PASSWORD=your_app_password
MCP_DEPLOYMENT_MODE=single_user_basic
EOF
chmod 600 ~/.config/nextcloud-mcp/env
```

Run the MCP server with rootless Podman:

```bash
podman run --replace --name nextcloud-mcp \
  --publish 127.0.0.1:8000:8000 \
  --env-file ~/.config/nextcloud-mcp/env \
  --health-cmd 'curl -fsS http://127.0.0.1:8000/health/live || exit 1' \
  --health-interval 30s \
  --health-timeout 5s \
  --health-on-failure kill \
  --health-retries 3 \
  ghcr.io/pi0n00r/nextcloud-mcp-server:v1.1.8
```

Then connect the MCP client to:

```text
http://127.0.0.1:8000/mcp
```

## Quadlet Service

Podman Quadlet lets systemd manage the container without a Docker daemon. Copy
the example unit from `contrib/podman/nextcloud-mcp.container`:

```bash
mkdir -p ~/.config/containers/systemd
cp contrib/podman/nextcloud-mcp.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start nextcloud-mcp.service
systemctl --user enable nextcloud-mcp.service
```

Check status and logs:

```bash
systemctl --user status nextcloud-mcp.service
journalctl --user -u nextcloud-mcp.service -f
```

For user services that should keep running after logout, enable linger for that
user from an administrator shell:

```bash
loginctl enable-linger "$USER"
```

## Docker Equivalent

The same image works with Docker:

```bash
docker run --rm --name nextcloud-mcp \
  --publish 127.0.0.1:8000:8000 \
  --env-file ~/.config/nextcloud-mcp/env \
  ghcr.io/pi0n00r/nextcloud-mcp-server:v1.1.8
```

## Notes

- The image default command starts the streamable HTTP MCP transport on port
  `8000`.
- The image exposes `/health/live` and `/health/ready` for container health
  checks.
- Newer source releases may exist without a published container package. Use the
  package tag above when you want the published old-stable image.
