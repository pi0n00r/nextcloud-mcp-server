# AGENTS.md — Nextcloud MCP Server (pi0n00r fork)

This repo is Gary Bajaj's fork of `cbcoutinho/nextcloud-mcp-server` (AGPL-3.0).
The fork's purpose is to fix three regressions surfaced during the 2026-04
work cycle and integrate byte-preserving DAVx5-style CardDAV semantics:

1. **A.1 — Photo clobber** on every contact updated through the legacy
   `nc_contacts_update_contact`. Every contact in Gary's address book that
   had been previously updated through this tool had its `PHOTO` blob
   silently dropped (corpus-wide loss disclosed 2026-04-26). Root cause:
   `_merge_vcard_properties` split on `\n` and `strip()`-ed lines, breaking
   RFC 5545 line-folding for PHOTO base64 blocks and X-properties.
2. **A.2 — `nc_webdav_write_file`** silently truncating writes near 20KB.
3. **A.3 — Schema gaps** for UID-less / FN-less / non-Latin vCards.

The fix is in three places:

- `nextcloud_mcp_server/client/vcard_parser.py` — NEW. Byte-preserving
  line-oriented vCard parser. Parses into `(folded-line, parsed-property)`
  tuples; untouched lines emit verbatim from `original_bytes`; only
  modified lines regenerate per RFC 6350 §3.2 + RFC 5545 §3.1.
- `nextcloud_mcp_server/client/contacts.py` — rewritten to route writes
  through the new parser and surface `EtagConflictError` on 412.
- `nextcloud_mcp_server/client/webdav.py` — `write_file` now routes
  bodies above `CHUNK_THRESHOLD` (1MB) through NC's chunked-upload v2
  endpoint, eliminating the silent-truncation failure mode.

## Repo layout

```
nextcloud_mcp_server/
├── client/
│   ├── contacts.py         CardDAV client — byte-preserving rewrite
│   ├── vcard_parser.py     Line-oriented vCard parser (load-bearing core)
│   ├── webdav.py           WebDAV client — chunked upload >1MB
│   └── ...
├── models/
│   ├── contacts.py         Pydantic schemas — UID optional, NFC, FN fallback
│   └── ...
├── server/
│   ├── contacts.py         8-op MCP tool surface (unified namespace)
│   └── ...
tests/
├── client/
│   ├── contacts/test_byte_preserving.py    T1–T10 + A.3 schema gaps
│   └── webdav/test_size_limit.py           Chunk-threshold sanity
scripts/
└── lint-ai-notice.sh       CI lint enforcing the 10-field AI-NOTICE block
```

## The unified `nc_contacts_*` tool surface (8 ops)

| # | Op | Purpose |
|---|---|---|
| 1 | `nc_contacts_list_addressbooks` | List addressbooks |
| 2 | `nc_contacts_list_contacts` | List contacts (`include_vcard`, `include_etag` flags) |
| 3 | `nc_contacts_get_contact` | NEW. Single contact: `vcard_text + etag + json` |
| 4 | `nc_contacts_create_contact` | Create from `vcard_text` or JSON |
| 5 | `nc_contacts_patch_contact` | NEW. Surgical edit; If-Match required |
| 6 | `nc_contacts_put_contact` | NEW. Full vCard replace; If-Match required |
| 7 | `nc_contacts_delete_contact` | Delete; If-Match optional but recommended |
| 8 | `nc_contacts_create_addressbook` / `nc_contacts_delete_addressbook` | Admin |

`nc_contacts_update_contact` is **deprecated**. It remains as a thin shim
that translates JSON-shape calls to `patch_contact` and emits a deprecation
warning. One minor version, then removed.

## Build / test

```bash
# Dev install (uses uv, matches upstream)
uv sync --dev

# Run the byte-preserving test corpus
uv run pytest tests/client/contacts/test_byte_preserving.py -v

# Lint the AI-NOTICE block on every Python source file
bash scripts/lint-ai-notice.sh
```

Live integration tests against a real Nextcloud instance require:

```bash
export REQUIRES_LIVE_NC=1
export NC_HOST=https://hub.bajaj.com
export NC_USER=gary
export NC_APP_PASSWORD="<scoped-app-password>" # never logged
uv run pytest tests/client/contacts/ -v
```

## Conventions (load-bearing for any contributor — human or AI)

- **No JSON↔vCard round-trip on properties not being modified.** If a
  property isn't in the change set, its bytes round-trip verbatim. Period.
- **`If-Match` mandatory on every CardDAV write.** Concurrency is
  ETag-based; 412 surfaces to the caller as `EtagConflictError`. No silent
  retries unless the caller opts in via `retry_on_conflict=True`.
- **Logs never include the NC app password or any X-Isla-Auth value.**
  Healthz output returns auth-presence (`"ok"`/`"missing"`) not values.
- **AI-NOTICE on every Python source file** under `nextcloud_mcp_server/`
  and `tests/`. The 10-field block (Schema-Version through Contact) is
  enforced by `scripts/lint-ai-notice.sh`. See `LICENSE.md` for the
  posture's license tie-in.

## Licensing

This repo is **AGPL-3.0-or-later**, matching upstream
`cbcoutinho/nextcloud-mcp-server`. Full text in `LICENSE.md`. The
exploitation-deterrence posture is set per source file in the AI-NOTICE
block; see `LICENSE.md § AI-NOTICE` for what each field signals.

## Pointers

- **NC instance:** `raidio.bajaj.com:443` (TrueNAS Mini R), publicly proxied
  as `hub.bajaj.com`. Backend creds are an NC app password scoped to
  Contacts read+write only — never log or echo.
- **Plan and implementation docs:**
  - `Documents/Projects/NC/NC-MCP-Plan.md` — mission, work streams, operator
    context, open decisions.
  - `Documents/Projects/NC/NC-MCP-Implementation.md` — codebase survey,
    architecture decisions, build sequence, deliverables.
- **Per-regression analysis:** `Documents/Projects/NC/regressions/A1-photo-clobber.md`,
  `A2-webdav-truncation.md`, `A3-schema-gaps.md`.
- **Photo-update embargo (lifts at Phase 7):**
  `Documents/Projects/Isla/contacts-policy.md § INTERIM FREEZE`.
- **Live AI-NOTICE three-layer reference:**
  `https://github.com/pi0n00r/freepbx/tree/main/auto-restore`.

## Upstream PR posture

The A.1 / A.2 / A.3 fixes are non-fork-specific — they benefit anyone
running this MCP. PRs to `cbcoutinho/nextcloud-mcp-server` should NOT
include the AI-NOTICE additions (those are fork-only); branch the
upstream-target work cleanly, and layer AI-NOTICE additions on top in the
fork's `main` branch as a separate commit.
