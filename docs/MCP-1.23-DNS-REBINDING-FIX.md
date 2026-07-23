# MCP 1.23.x DNS Rebinding Protection Configuration

## Problem

MCP Python SDK 1.23.0 introduced **automatic DNS rebinding protection** that breaks containerized deployments (Kubernetes, Docker) when the protection is unintentionally auto-enabled.

### Root Cause

From `mcp/server/fastmcp/server.py:177-183` in the Python SDK:

```python
# Auto-enable DNS rebinding protection for localhost (IPv4 and IPv6)
if transport_security is None and host in ("127.0.0.1", "localhost", "::1"):
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )
```

### What Was Happening

1. **FastMCP initialization** in `app.py` didn't pass `host` or `transport_security` parameters
2. **Defaults applied**: `host="127.0.0.1"`, `transport_security=None`
3. **Auto-enablement triggered**: Condition `transport_security is None and host == "127.0.0.1"` was TRUE
4. **Protection activated** with `allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"]`
5. **Kubernetes requests rejected**: `Host: nextcloud-mcp-server.default.svc.cluster.local:8000` didn't match allowed hosts

### Why `--host 0.0.0.0` Didn't Help

The `--host` CLI flag (used in Dockerfile/docker-compose) controls **uvicorn's bind address**, NOT the **FastMCP `host` parameter**. These are separate concerns:

- **Uvicorn bind address** (`--host 0.0.0.0`): Where the HTTP server listens
- **FastMCP host parameter** (defaulted to `"127.0.0.1"`): Used for auto-enablement logic

## Solution

Always pass explicit transport-security settings to FastMCP so its loopback
auto-enablement cannot break container service names. The compatibility default
remains disabled, but operators can enable protection with environment-driven
Host and Origin allowlists.

### Changes Made

Modified `nextcloud_mcp_server/app.py` and
`nextcloud_mcp_server/config.py`:

1. Build `TransportSecuritySettings` from the server's typed settings.
2. Pass those settings to the OAuth and BasicAuth FastMCP instances.
3. Default protection to off for backward compatibility.
4. Fail closed when protection is enabled without an allowed Host.
5. Log the enabled posture and the number of configured allowlist entries.

Configure the gate with:

```dotenv
MCP_DNS_REBINDING_PROTECTION=true
MCP_DNS_REBINDING_ALLOWED_HOSTS=nextcloud-mcp:*,localhost:*,127.0.0.1:*
MCP_DNS_REBINDING_ALLOWED_ORIGINS=https://operator.example.com
```

Every Host value presented by clients or reverse proxies must be listed.
Origin is optional for non-browser same-origin requests; when present, it must
match the Origin allowlist.

## Impact

### What This Fixes

- **Kubernetes deployments**: Requests with k8s service DNS names now work
- **Docker deployments**: Port-mapped requests (localhost:8000 → container) now work
- **Reverse proxy deployments**: Proxied requests with various Host headers now work
- **Ingress controllers**: Requests via ingress hostnames now work

### Security Considerations

DNS rebinding protection defends against attacks where:
1. Attacker controls a DNS domain (e.g., `evil.com`)
2. DNS initially resolves to attacker's IP
3. After victim's browser caches the origin, DNS changes to victim's localhost
4. Attacker's page can now make requests to victim's localhost services

The default-off posture preserves existing container deployments; it is not a
claim that every such deployment is safe. Authentication, reverse-proxy access
controls, network policy, and DNS rebinding protection are independent layers.

Enable the gate whenever the transport may be reached from browser-controlled
or otherwise untrusted networks. Include every legitimate direct, container,
and reverse-proxy Host value. An empty Host allowlist deliberately rejects all
requests with `421 Misdirected Request`; a disallowed Origin is rejected with
`403 Forbidden`.

## Testing

- Configuration parsing and default compatibility tests
- Request-level Host rejection and acceptance tests
- Request-level Origin rejection and acceptance tests
- Empty-Host-allowlist fail-closed test
- Ruff and import/compile checks
- Compatible with MCP 1.23.x

## References

- [MCP Python SDK 1.23.0 Release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.23.0)
- Commit: `d3a1841` - "Auto-enable DNS rebinding protection for localhost servers"
- Issue #373 (original report of k8s breakage)
- PR #382 (MCP 1.23.x upgrade)
