#!/usr/bin/env bash
# Undo any demo: restore the source CSVs and the rule config, then restart.
#
# Restores from git rather than backup copies. The CSVs and config are
# committed and are never legitimately edited during a demo, so the committed
# version is always the correct baseline -- whereas a backup file can itself be
# polluted if a demo runs twice without a reset in between.
set -euo pipefail
cd "$(dirname "$0")/.."
. demo/lib.sh

rm -f data/*.demo-backup

# Human state is deliberately not rebuilt from source, so a reset has to clear
# it explicitly -- that is the same property the claim-task demo relies on.
if [ -f backend/model_of_care.db ]; then
  python3 - <<'PYEOF' || true
import sqlite3, pathlib
db = pathlib.Path("backend/model_of_care.db")
try:
    con = sqlite3.connect(db)
    n = con.execute("delete from task_state").rowcount
    con.commit(); con.close()
    if n: print(f"cleared {n} human task-state row(s)")
except sqlite3.OperationalError:
    pass
PYEOF
fi
CHANGED="$(git diff --name-only -- data backend/config || true)"

if [ -n "$CHANGED" ]; then
  git checkout -- data backend/config
  echo "restored:"
  echo "$CHANGED" | sed 's/^/   /'
else
  echo "already at baseline"
fi

restart_server
echo "back to baseline — refresh the browser"
echo
