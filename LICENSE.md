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
