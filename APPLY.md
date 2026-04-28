# NC MCP Server — v0.1 patch series APPLY guide

Three patches landing the v0.1 work in `pi0n00r/nextcloud-mcp-server`:

1. **0001-scaffold-AGENTS.md-LICENSE.md-AI-NOTICE-CI-lint.patch** — Phase 0
   scaffolding: `AGENTS.md`, `LICENSE.md` (markdown form, AGPL-3.0-or-later),
   `scripts/lint-ai-notice.sh` (adapted from `pi0n00r/freepbx`'s reference).
2. **0002-fix-A.1-A.3-byte-preserving-CardDAV-substrate-8-op-n.patch** —
   The load-bearing implementation: new `vcard_parser.py`, rewritten
   `client/contacts.py`, rewritten `server/contacts.py`, schema-gap fix in
   `models/contacts.py`, full T1-T10 test corpus.
3. **0003-fix-A.2-chunked-upload-1MB-to-escape-silent-truncati.patch** —
   Webdav chunked-upload above 1MB to escape the MCP transport's silent
   truncation cap.

## Apply

From a Windows machine where `git` is configured against the `pi0n00r`
GitHub account (push works without credential prompt):

```powershell
# Clone the fork (skip if already present)
git clone https://github.com/pi0n00r/nextcloud-mcp-server.git
cd nextcloud-mcp-server

# Branch off main
git checkout -b fix/regressions-and-byte-preserving-substrate

# Apply the patch series in order
git am C:\Users\Gary\Downloads\nc-mcp-patches\0001-scaffold-AGENTS.md-LICENSE.md-AI-NOTICE-CI-lint.patch
git am C:\Users\Gary\Downloads\nc-mcp-patches\0002-fix-A.1-A.3-byte-preserving-CardDAV-substrate-8-op-n.patch
git am C:\Users\Gary\Downloads\nc-mcp-patches\0003-fix-A.2-chunked-upload-1MB-to-escape-silent-truncati.patch

# Verify the AI-NOTICE lint passes
bash scripts/lint-ai-notice.sh

# Push the branch
git push -u origin fix/regressions-and-byte-preserving-substrate

# Open a PR (or merge directly to main if pi0n00r solo-owns this fork)
# — at your discretion. The PR description can copy from the commit messages.
```

## Rollback

If something looks wrong post-merge:

```powershell
git reset --hard origin/main
git push --force-with-lease origin fix/regressions-and-byte-preserving-substrate
```

The patch files remain in `Downloads/nc-mcp-patches/` for re-apply.

## Verify post-deployment

After deploying the rebuilt MCP (replace-in-place per the plan's Decision 1):

1. `curl https://<mcp-host>/healthz` → 200 OK.
2. From a Cowork session, invoke `nc_contacts_get_contact` against any
   photo-bearing contact. Response includes `vcard_text` with the PHOTO
   block intact and `etag`.
3. Invoke `nc_contacts_patch_contact` with `set_props={"NOTE": "test"}`
   and the etag from step 2. Response: `{old_etag, new_etag, applied,
   verified}`. Subsequent `nc_contacts_get_contact` shows the PHOTO
   still present — the canonical lift-condition for the embargo in
   `Documents/Projects/Isla/contacts-policy.md § INTERIM FREEZE`.
4. Write a >100KB file via `nc_webdav_write_file`; subsequent
   `nc_webdav_read_file` returns it byte-equal.

## Upstream PR posture (per Plan Decision 2 — still open)

The A.1 / A.2 / A.3 fixes are non-fork-specific. Once Gary clears
Decision 2 (PR back to `cbcoutinho/nextcloud-mcp-server`), branch the
upstream-target work cleanly **without** the AI-NOTICE additions on
existing files (the AI-NOTICE pattern is a fork-only convention).
The vcard_parser.py + the rewritten client surface are clean
contributions; the AGENTS.md + LICENSE.md + lint-ai-notice.sh stay
fork-only.

## Loose end (parked)

The `pi0n00r/freepbx` commit `1c98b4c` (auto-restore CSS-hide hook for
FreePBX 17 nag suppression) is still parked at
`Downloads/freepbx-nag-hook-patch/0001-Add-auto-restore-CSS-hide-hook-for-FreePBX-17-nag-su.patch`
with its own APPLY.md. Push it at the same time as this series since
you'll already be in a git context.

---

## v0.1.1 — fork-policy patch (2026-04-27, post-v0.1)

A fourth patch landed after v0.1 ship + embargo lift:

- **0004-fork-policy-extend-LICENSE.md-with-AGPL-invariant-an.patch** (2-3 KB)
  — Extends `LICENSE.md` with a "What's NOT licensed in this fork" section
  declaring AGPL-3.0-or-later as load-bearing, no CLA solicited, no
  paid-tier features, no telemetry. Diverges from upstream's CLA-collecting
  posture without disparaging it. Authored after the v0.2 plan audit
  identified the upstream `CLA.md` as the most ethically-relevant "gate."

Apply order is the same `git am` chain (0001 → 0002 → 0003 → 0004); the
v0.1.1 patch only touches `LICENSE.md` so it has no functional impact on
the running MCP. It's a posture/documentation patch.
