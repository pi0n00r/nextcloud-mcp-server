<!--
AI-NOTICE:Schema-Version=0.1
AI-NOTICE:License=AGPL-3.0-or-later
AI-NOTICE:Author=Gary Bajaj
AI-NOTICE:Exploitation-Deterrence=true
AI-NOTICE:Operator-Override-Required=true
AI-NOTICE:Override-Reason-Required=false
AI-NOTICE:Severity=high
AI-NOTICE:Escalation=warn
AI-NOTICE:Scope=file
AI-NOTICE:Contact=https://AImends.bajaj.com/
-->

<div align="center">

# Nextcloud MCP Server

**A production-ready Model Context Protocol server for Nextcloud.**

Give AI assistants controlled access to files, calendars, contacts, notes,
Deck, Talk, and other Nextcloud application surfaces through 155 MCP tools.

[![Tests](https://github.com/pi0n00r/nextcloud-mcp-server/actions/workflows/test.yml/badge.svg?branch=master)](https://github.com/pi0n00r/nextcloud-mcp-server/actions/workflows/test.yml)
[![Latest release](https://img.shields.io/github/v/release/pi0n00r/nextcloud-mcp-server?label=release)](https://github.com/pi0n00r/nextcloud-mcp-server/releases/latest)
[![Container](https://img.shields.io/badge/GHCR-v1.6.6.2-2496ED?logo=docker&logoColor=white)](https://github.com/pi0n00r/nextcloud-mcp-server/pkgs/container/nextcloud-mcp-server)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/pi0n00r/nextcloud-mcp-server)](LICENSE)

[Quick start](#quick-start) | [Capabilities](#nextcloud-capabilities) |
[Documentation](#documentation) | [Security](#security) |
[Contributing](#contributing)

</div>

> [!WARNING]
> **Historical WebDAV filename-search erratum:** published releases through `v1.6.2`
> can emit an invalid empty SEARCH predicate when no recognized filter reaches
> `nc_webdav_search_files`. Clients using the unsupported `path` and `query`
> argument names may therefore receive HTTP 500, sometimes followed by a proxy
> HTTP 502, while ordinary WebDAV operations remain available. Package
> `v1.6.6.2` contains the correction. On older builds, use `scope` and
> `name_pattern` (for example,
> `{"scope":"/Documents","name_pattern":"%activity%","limit":50}`). See
> [ERRATA.md](ERRATA.md#webdav-search-with-an-empty-predicate) for affected
> releases and package guidance.

Nextcloud MCP Server is a standalone bridge between MCP clients and an
existing Nextcloud instance. It runs outside Nextcloud and exposes a broad,
typed tool surface over WebDAV, CalDAV, CardDAV, OCS, and application REST
endpoints.

This production-focused fork maintains hardened data paths for real-world
automation:

- **Byte-preserving CardDAV updates** retain contact photos, folded fields,
  custom properties, and untouched vCard bytes.
- **Concurrency-safe writes** use ETags for CardDAV and WebDAV changes instead
  of silently overwriting newer data.
- **Atomic large-file uploads** route through Nextcloud's chunked upload path
  without weakening destination preconditions.
- **Calendar and Deck fidelity** preserves value types, timezones, alarms,
  recurring-task state, card order, and due dates through updates.
- **Document-aware file reads** select automatic extraction, structured
  Markdown, or byte-preserving raw content on each request.
- **First-class container networking** serves IPv4 and IPv6 from one listener
  in the stable image.

## At a Glance

| Property | Value |
|---|---|
| **Stable tool surface** | 155 tools across 12 Nextcloud application surfaces |
| **Transports** | Streamable HTTP and stdio |
| **Stable package** | `ghcr.io/pi0n00r/nextcloud-mcp-server:v1.6.6.2` |
| **Stable application version** | `0.166.1` |
| **Development preview** | `v1.7` / `0.169.1`: 160 tools, including 5 Talk tools not yet tested against a live Talk installation |
| **Architectures** | `linux/amd64`, `linux/arm64` |
| **Authentication** | Nextcloud app password |
| **Operations** | Liveness/readiness probes, Prometheus metrics, OpenTelemetry |

## Quick Start

### 1. Create a Nextcloud app password

In Nextcloud, open **Personal settings > Security > Devices & sessions** and
create an app password for the MCP server. Do not use your primary account
password.

### 2. Create a private environment file

```bash
mkdir -p ~/.config/nextcloud-mcp
chmod 700 ~/.config/nextcloud-mcp

printf '%s\n' \
  'NEXTCLOUD_HOST=https://cloud.example.com' \
  'NEXTCLOUD_USERNAME=your_username' \
  'NEXTCLOUD_PASSWORD=your_app_password' \
  'MCP_DEPLOYMENT_MODE=single_user_basic' \
  > ~/.config/nextcloud-mcp/env

chmod 600 ~/.config/nextcloud-mcp/env
```

### 3. Run the stable container

```bash
docker run --detach \
  --name nextcloud-mcp \
  --restart unless-stopped \
  --publish 127.0.0.1:8000:8000 \
  --env-file ~/.config/nextcloud-mcp/env \
  ghcr.io/pi0n00r/nextcloud-mcp-server:v1.6.6.2
```

Verify the service before connecting a client:

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

The MCP endpoint is:

```text
http://127.0.0.1:8000/mcp
```

See the [container package guide](docs/container-package.md) for persistent
deployment details and health-check configuration.

### Run from source

For a local stdio integration:

```bash
git clone --branch v1.6.6.2 --depth 1 \
  https://github.com/pi0n00r/nextcloud-mcp-server.git
cd nextcloud-mcp-server
uv sync --locked

NEXTCLOUD_HOST=https://cloud.example.com \
NEXTCLOUD_USERNAME=your_username \
NEXTCLOUD_PASSWORD=your_app_password \
MCP_DEPLOYMENT_MODE=single_user_basic \
  uv run nextcloud-mcp-server run --transport stdio
```

## Nextcloud Capabilities

The stable single-user profile exposes the following core tool surface:

| Surface | Tools | Coverage |
|---|---:|---|
| **Deck** | 36 | Boards, stacks, cards, comments, labels, assignees, attachments |
| **Collectives** | 20 | Collectives, pages, tags, hierarchy, trash and restore |
| **Calendar and Tasks** | 18 | Events, todos, recurring-task backlog/current occurrence, availability, bulk operations |
| **Cookbook** | 13 | Recipes, categories, keywords, imports, configuration |
| **Files (WebDAV)** | 11 | Read/write, search, move/copy, directories, favorites |
| **Contacts** | 11 | Address books, byte-preserving create/patch/replace/delete |
| **News** | 8 | Feeds, folders, items, unread/starred views, feed health |
| **Notes** | 7 | Create, read, update, append, search, attachments |
| **Mail** | 13 | Accounts, messages, raw source, flags, tags, move/delete, sending |
| **Sharing** | 6 | User/group shares, public links, listing and lifecycle |
| **Tables** | 6 | Schemas and row-level create/read/update/delete |
| **Talk** | 6 | Conversations, participants, messages, read state |
| **Total** | **155** | Core tools in the stable single-user profile |

MCP resources provide additional structured browsing paths. Optional semantic
search adds cross-application retrieval for supported content when its
indexing infrastructure is enabled.

## Production Features

### Data integrity

- Byte-preserving vCard parser with explicit patch and full-replacement tools
- ETag preconditions and clear conflict reporting
- Chunked WebDAV uploads with atomic destination checks
- Stale pooled-read and verified short-read recovery
- Typed responses for file, contact, calendar, and sharing operations

### Safety and access control

- App-password isolation for single-user deployments
- Optional pre-shared gateway secret for HTTP transport
- Configurable DNS-rebinding protection
- Tag-based file and folder exclusion through `EXCLUDED_TAGS`

### Search and document processing

- Keyword and semantic search modes
- Qdrant-backed indexing with optional dense and sparse embeddings
- Per-read `auto`, structured `markdown`, and byte-preserving `raw` modes
- PDF, Office document, image, and OCR extraction with reported parse status
- Optional Docling backend, including VLM pipelines
- Optional cross-encoder reranking with bounded concurrency and graceful fallback
- Verify-on-read safeguards for indexed results

### Operations

- Docker health checks at `/health/live` and `/health/ready`
- Prometheus metrics and OpenTelemetry tracing
- SQLite and PostgreSQL storage backends
- Structured logging and optional continuous profiling
- CI coverage across multiple Nextcloud and authentication profiles

## Authentication

The stable profile connects to one existing Nextcloud account using an app
password and `MCP_DEPLOYMENT_MODE=single_user_basic`. For HTTP deployments,
configure the gateway secret and keep the service behind a trusted
TLS-terminating reverse proxy.

## Documentation

### Start here

- [Container package](docs/container-package.md)
- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Running the server](docs/running.md)
- [Troubleshooting](docs/troubleshooting.md)

### Application guides

- [Files and WebDAV](docs/webdav.md)
- [Calendar and Tasks](docs/calendar.md)
- [Contacts](docs/contacts.md)
- [Notes](docs/notes.md)
- [Deck](docs/deck.md)
- [Cookbook](docs/cookbook.md)
- [Tables](docs/table.md)

### Advanced capabilities

- [Semantic search architecture](docs/semantic-search-architecture.md)
- [Vector sync UI](docs/user-guide/vector-sync-ui.md)
- [Document processing configuration](docs/configuration.md)
- [Observability](docs/observability.md)
- [Database migrations](docs/database-migrations.md)
- [Architecture decisions](docs/)

## Release Policy

Stable container images use exact release tags and are published for amd64 and
arm64. A floating `latest` tag is intentionally not published.

- [Latest release](https://github.com/pi0n00r/nextcloud-mcp-server/releases/latest)
- [Container package](https://github.com/pi0n00r/nextcloud-mcp-server/pkgs/container/nextcloud-mcp-server)
- [Changelog](CHANGELOG.md)

## Security

Do not report vulnerabilities through a public issue. Use
[GitHub private vulnerability reporting](https://github.com/pi0n00r/nextcloud-mcp-server/security/advisories/new)
and review [SECURITY.md](SECURITY.md).

For network deployments, terminate TLS at a trusted reverse proxy, keep the
MCP service private where possible, and configure the gateway secret and
allowed-host policy described in the configuration guide.

## Contributing

Bug reports, focused fixes, documentation improvements, and new application
integrations are welcome.

- [Open an issue](https://github.com/pi0n00r/nextcloud-mcp-server/issues)
- [Submit a pull request](https://github.com/pi0n00r/nextcloud-mcp-server/pulls)
- Target this repository's `master` branch and preserve the documented data
  integrity contracts

This fork is built on the work of Chris Coutinho and the
`nextcloud-mcp-server` contributor community. Thank you to everyone whose
features, fixes, reviews, and testing have strengthened the project.

## License

Licensed under the [GNU Affero General Public License v3](LICENSE).

## References

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Nextcloud](https://nextcloud.com/)
