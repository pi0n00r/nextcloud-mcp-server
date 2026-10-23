#!/usr/bin/env bash
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

set -euo pipefail
umask 0077

if [[ "$#" -ne 1 ]]; then
  echo "publication identity gate error: exact source tree required" >&2
  exit 2
fi
if [[ -z "${AI_NOTICE_PRIVATE_L1_TEXT:-}" ]]; then
  echo "publication identity gate error: private comparison secret is missing" >&2
  exit 2
fi

comparison_file="$(mktemp "${RUNNER_TEMP:-/tmp}/ai-notice-private.XXXXXXXX")"
trap 'rm -f "$comparison_file"' EXIT
printf '%s' "$AI_NOTICE_PRIVATE_L1_TEXT" > "$comparison_file"
unset AI_NOTICE_PRIVATE_L1_TEXT

AI_NOTICE_PRIVATE_L1_FILE="$comparison_file" \
  python3 scripts/check_publication_identity.py \
    --git-tree "$1" --require-private-comparison
