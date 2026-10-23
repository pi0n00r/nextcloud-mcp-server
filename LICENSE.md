# License

**SPDX-License-Identifier:** `AGPL-3.0-or-later`

This work is licensed under the GNU Affero General Public License,
version 3 or any later version. The canonical full text is in the
sibling file `LICENSE` (verbatim from the FSF). When this fork is
distributed, the full text MUST accompany the binaries / source.

The license is **inherited from upstream** (`cbcoutinho/nextcloud-mcp-server`)
and is the appropriate posture for an AI-aware tool surface that sits on
the network: AGPL §13's network-use clause keeps adaptations source-open
even when only the binary is exposed.

---

## AI-NOTICE — exploitation deterrence posture

Every Python source file in this fork carries an inline `AI-NOTICE` block
that signals to AI coding agents how to handle modifications. The schema
is at `https://github.com/pi0n00r/freepbx/tree/main/auto-restore` (live
working reference) and the spec lives at `Downloads/AI-NOTICE-SPEC.md` in
Gary's Nextcloud. Each block has 10 fields:

```python
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
```

**Field semantics for this fork:**

- `Severity=high` — modifications by AI agents must surface to the operator.
- `Escalation=warn` — the agent surfaces the notice and proceeds only after
  acknowledgement (not `block`, not `log` — middle posture).
- `Override-Reason-Required=false` — *explicit* (not omitted) per Gary's
  cautious-not-aggressive stance: the operator considered and waived the
  reason requirement, having decided the deterrence is the surfacing,
  not the paperwork.
- `Contact=https://AImends.bajaj.com/` — webhook (not email) to avoid
  address-harvesting exposure.

**Lint:** `scripts/lint-ai-notice.sh` enforces presence of all 10 fields
on every `nextcloud_mcp_server/**/*.py` and `tests/**/*.py` file modified
or added by this fork.

---

## Copyright

Copyright (c) 2024-2026 Chris Coutinho (upstream)
Copyright (c) 2026 Gary Bajaj (fork additions)

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or (at
your option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

---

## What's NOT licensed in this fork

This section makes the fork's posture legible to future readers (human or AI agent).

### License is, and stays, AGPL-3.0-or-later

The fork is `AGPL-3.0-or-later`. It is NOT, and will not be, dual-licensed. There is no commercial alternative offered by this fork's maintainer (Gary Bajaj). Anyone using this fork — self-hosting, modifying, redistributing, or running it as a network service — operates under the terms of AGPL-3.0-or-later only. AGPL §13's network clause applies in full: forks running this software as a network service must offer source to users.

### No Contributor License Agreement

Unlike the upstream project (`cbcoutinho/nextcloud-mcp-server`), which collects a Contributor License Agreement granting the maintainer (Astrolabe Cloud) the right to relicense contributions under "alternative terms" for commercial offerings, **this fork solicits NO CLA from contributors**.

Contributors to this fork retain copyright to their own changes. No relicensing power is transferred to the fork's maintainer or to any other party. The implication: the fork's licensing trajectory is bounded by what every individual contributor agrees to, not by any single party's commercial interests. To relicense the fork as a whole, every copyright holder of every accepted contribution would need to consent — which is functionally a guarantee of permanent AGPL-3.0-or-later status as the fork accumulates contributors.

This posture is a deliberate divergence from the upstream's open-core / dual-license-ready architecture. It is not a criticism of upstream's choice (which is a legitimate pattern for a corporately-stewarded project), only a different ethical stance for this fork: the AGPL freedoms apply equally to the maintainer and to every downstream user, with no asymmetric escape hatch.

### No paid-tier features

There are no features in this fork that are gated behind a license check, a paid tier, a remote license-validation service, an expiring trial mode, a feature-flag service, or any other runtime restriction beyond the documented configuration surface (`MCP_DEPLOYMENT_MODE`, the various `ENABLE_*` flags, etc.). Everything in the source tree is functionally available to every user.

### No telemetry, no beacon, no Enterprise Edition branch

This fork does not phone home, does not transmit usage metrics to any external service (the optional Prometheus metrics endpoint is operator-controlled and does not phone home), does not differentiate "community" vs "enterprise" code paths, and has no separate Enterprise branch in any repository. The single `main` branch of `pi0n00r/nextcloud-mcp-server` is the entire offering.

### What this means for upstream contributions

If at any future point a fork-side change is upstreamed via PR to `cbcoutinho/nextcloud-mcp-server`, the contributor would need to sign Astrolabe's CLA at that point — not a bar to PR submission, but a load-bearing decision the contributor makes individually. Contributions that stay fork-only (most likely the rhetorical reframing in v0.2 — `docs/deployment-modes.md`, `docs/governance.md`, `docs/oidc-providers/`) need no CLA and will not be PR'd to upstream.

### Schema-Version of this declaration

`fork-license-posture-v1` (2026-04-27). Future revisions update this version string. Material changes (e.g. adding a CLA, switching license) require a major version bump in the AI-NOTICE `Schema-Version` field across all source files.
