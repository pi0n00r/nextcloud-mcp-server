#!/bin/bash

set -euox pipefail

# Talk (spreed) ships its appstore release on demand; on a fresh container the
# app is not yet installed, so app:install pulls it. On rebuilds where the
# volume already has it, app:install fails with "already installed" — that
# specific failure is benign, hence the trailing `|| true`. We then run
# app:enable separately, which is the action that actually has to succeed.
#
# This split (install || true; then enable) is preferred over the previous
# `app:install || app:enable` chain because:
#  - `--keep-disabled` keeps install side-effects strictly to fetching/extracting
#    the app, so the enable step is the single source of truth for whether the
#    app is active.
#  - `--force` skips the compatibility check, locking the script to spreed's
#    current behaviour rather than the appstore's view of NC compatibility.
#  - If app:install dies for an unrelated reason (network outage, appstore
#    unreachable on a fresh install), app:enable now fails with the clearer
#    "app not found" rather than the install-time error being masked entirely.
php /var/www/html/occ app:install spreed --keep-disabled --force || true
php /var/www/html/occ app:enable spreed
