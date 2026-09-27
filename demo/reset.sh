#!/usr/bin/env bash
# Undo simulate-visit.sh: restore the source CSVs and restart the API.
#
# Restores from git rather than a backup copy. The provided CSVs are committed
# and are never legitimately edited, so the committed version is always the
# correct baseline -- whereas a backup file can itself be polluted if a demo is
# run twice without resetting in between.
set -euo pipefail
cd "$(dirname "$0")/.."
. demo/lib.sh

rm -f data/*.demo-backup
git checkout -- data/encounters.csv
echo "restored data/encounters.csv from git ($(( $(wc -l < data/encounters.csv) )) rows)"

restart_server
echo "back to baseline — refresh the browser"
echo
