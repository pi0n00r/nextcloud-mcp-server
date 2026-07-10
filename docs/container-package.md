# Container Package

The published container package is the stable release image:

```bash
ghcr.io/pi0n00r/nextcloud-mcp-server:v1.3.0
```

The image is published as a multi-architecture Docker image for `linux/amd64`
and `linux/arm64`.

## Docker Quick Start

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

Run the MCP server:

```bash
docker run --detach --name nextcloud-mcp \
  --restart unless-stopped \
  --publish 127.0.0.1:8000:8000 \
  --env-file ~/.config/nextcloud-mcp/env \
  --health-cmd 'curl -fsS http://127.0.0.1:8000/health/live || exit 1' \
  --health-interval 30s \
  --health-timeout 5s \
  --health-retries 3 \
  --health-start-period 20s \
  ghcr.io/pi0n00r/nextcloud-mcp-server:v1.3.0
```

Then connect the MCP client to:

```text
http://127.0.0.1:8000/mcp
```

Check status and logs:

```bash
docker ps --filter name=nextcloud-mcp
docker logs --follow nextcloud-mcp
```

Check both health endpoints before routing client traffic:

```text
http://127.0.0.1:8000/health/live
http://127.0.0.1:8000/health/ready
```

## Notes

- The image default command starts the streamable HTTP MCP transport on port
  `8000`.
- The image exposes `/health/live` and `/health/ready` for container health
  checks.
- The package uses the exact release tag and does not publish a floating
  `latest` tag.
