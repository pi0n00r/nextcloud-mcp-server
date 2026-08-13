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
    "nextcloud_mcp_server/api/visualization.py"
    "nextcloud_mcp_server/client/base.py"
    "nextcloud_mcp_server/client/calendar.py"
    "nextcloud_mcp_server/client/contacts.py"
    "nextcloud_mcp_server/client/dav_errors.py"
    "nextcloud_mcp_server/client/entity_tag.py"
    "nextcloud_mcp_server/client/ocs.py"
    "nextcloud_mcp_server/client/sharing.py"
    "nextcloud_mcp_server/client/vcard_parser.py"
    "nextcloud_mcp_server/client/webdav.py"
    "nextcloud_mcp_server/config.py"
    "nextcloud_mcp_server/links.py"
    "nextcloud_mcp_server/migrations.py"
    "nextcloud_mcp_server/models/__init__.py"
    "nextcloud_mcp_server/models/calendar.py"
    "nextcloud_mcp_server/models/contacts.py"
    "nextcloud_mcp_server/models/sharing.py"
    "nextcloud_mcp_server/observability/metrics.py"
    "nextcloud_mcp_server/server/calendar.py"
    "nextcloud_mcp_server/server/contacts.py"
    "nextcloud_mcp_server/server/deck.py"
    "nextcloud_mcp_server/server/sharing.py"
    "nextcloud_mcp_server/server/semantic.py"
    "nextcloud_mcp_server/server/webdav.py"
    "tests/client/calendar/test_calendar_operations.py"
    "tests/client/calendar/test_field_preservation.py"
    "tests/client/calendar/test_task_operations.py"
    "tests/client/contacts/test_byte_preserving.py"
    "tests/client/webdav/test_size_limit.py"
    "tests/contract/test_gateway_rerank_consumer.py"
    "tests/server/test_calendar_todos_mcp.py"
    "tests/server/test_mcp.py"
    "tests/test_tool_description_metacharacters.py"
    "tests/unit/api/test_search_rerank_api.py"
    "tests/unit/api/test_search_usage_metering.py"
    "tests/unit/client/test_calendar_etag_concurrency.py"
    "tests/unit/client/test_contacts.py"
    "tests/unit/client/test_dav_errors.py"
    "tests/unit/client/test_ocs.py"
    "tests/unit/client/test_share_types.py"
    "tests/unit/client/test_webdav_write_conflicts.py"
    "tests/unit/test_calendar_tools.py"
    "tests/unit/test_links.py"
    "tests/unit/test_migrations_concurrency.py"
    "tests/unit/test_unified_verifier.py"
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
