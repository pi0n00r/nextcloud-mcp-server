#!/usr/bin/env bash
# AI-NOTICE lint — enforce presence of the full 10-field block on every
# Python source file added or substantially modified by this fork.
#
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
#
# Adapted for Python globs from the FreePBX repo's reference linter:
# https://github.com/pi0n00r/freepbx/blob/main/auto-restore/scripts/lint-ai-notice.sh
#
# Files NOT touched by this fork (i.e. inherited from upstream cbcoutinho)
# are exempt; the linter only checks files listed in fork-touched.txt
# (or, by default, the union of staged + recently-modified files).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REQUIRED_FIELDS=(
    "Schema-Version=0.1"
    "License=AGPL-3.0-or-later"
    "Author=Gary Bajaj"
    "Exploitation-Deterrence=true"
    "Operator-Override-Required=true"
    "Override-Reason-Required=false"
    "Severity=high"
    "Escalation=warn"
    "Scope=file"
    "Contact=https://AImends.bajaj.com/"
)

# Files this fork added or substantially modified. The list is maintained
# here rather than scanning the whole tree so upstream-inherited files (no
# fork ownership) aren't penalized.
FORK_TOUCHED=(
    "nextcloud_mcp_server/client/contacts.py"
    "nextcloud_mcp_server/client/vcard_parser.py"
    "nextcloud_mcp_server/client/webdav.py"
    "nextcloud_mcp_server/models/contacts.py"
    "nextcloud_mcp_server/server/contacts.py"
    "tests/client/contacts/test_byte_preserving.py"
    "tests/client/webdav/test_size_limit.py"
)

failures=0
for f in "${FORK_TOUCHED[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "MISSING $f" >&2
        ((failures++)) || true
        continue
    fi
    missing_fields=()
    for field in "${REQUIRED_FIELDS[@]}"; do
        if ! grep -q "AI-NOTICE:$field" "$f"; then
            missing_fields+=("$field")
        fi
    done
    if (( ${#missing_fields[@]} > 0 )); then
        echo "FAIL $f" >&2
        for mf in "${missing_fields[@]}"; do
            echo "  missing: AI-NOTICE:$mf" >&2
        done
        ((failures++)) || true
    else
        echo "ok $f"
    fi
done

if (( failures > 0 )); then
    echo >&2
    echo "AI-NOTICE lint: $failures file(s) failed" >&2
    exit 1
fi
echo "AI-NOTICE lint: all ${#FORK_TOUCHED[@]} fork-touched files pass"
