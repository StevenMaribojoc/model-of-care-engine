#!/usr/bin/env bash
#
# Live demo: human state survives the engine regenerating everything.
#
#   ./demo/claim-task.sh          claim the most overdue PCP task and rebuild
#   ./demo/reset.sh               clear it
#
# Assigns a task, sets a status, writes a note, then rebuilds the whole
# database from the CSVs -- every derived row dropped and recomputed -- and
# shows the note still attached.
#
# It survives because it is stored against the natural key of the work
# (patient, action, specialty) rather than against the task's row id, and
# because the rebuild deliberately does not drop the one table that is not
# derived from source data.
#
set -euo pipefail
cd "$(dirname "$0")/.."
. demo/lib.sh

ASSIGNEE="${ASSIGNEE:-Maria Alvarez}"
NOTE="${NOTE:-Called, left voicemail}"

curl -sf "$API/health" >/dev/null 2>&1 || {
  echo "API is not running on port $PORT — start it with ./run.sh first"; exit 1;
}

PATIENT="${1:-}"
if [ -z "$PATIENT" ]; then
  PATIENT="$(curl -s "$API/tasks?role=SCHEDULER&specialty=PCP&limit=1" \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["patient_id"])')"
fi

task_line() {
  curl -s "$API/tasks?role=CLINICAL&search=$PATIENT&specialty=PCP" | "$PY" -c "
import json,sys
items=json.load(sys.stdin)['items']
if not items:
    print('   (no PCP task for this patient)'); raise SystemExit
t=items[0]; s=t['state']
print(f\"   {t['patient_name']}  —  {t['target']} {t['task_type']}\")
if s:
    print(f\"   assigned to  {s['assignee'] or 'nobody'}\")
    print(f\"   status       {s['status']}\")
    print(f\"   note         {s['note'] or '—'}\")
else:
    print('   assigned to  nobody')
    print('   status       —   (no human has touched this task)')
    print('   note         —')
"
}

db_counts() {
  "$PY" - <<'PY'
import sqlite3, pathlib
db = pathlib.Path("backend/model_of_care.db")
con = sqlite3.connect(db)
tasks = con.execute("select count(*) from task").fetchone()[0]
state = con.execute("select count(*) from task_state").fetchone()[0]
print(f"   task rows        {tasks}   (derived — dropped and rebuilt every run)")
print(f"   task_state rows  {state}   (human — never dropped)")
con.close()
PY
}

echo
echo "BEFORE"
task_line

TASK_ID="$(curl -s "$API/tasks?role=CLINICAL&search=$PATIENT&specialty=PCP" \
  | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["task_id"])')"

echo
echo "A clinician picks it up (this is the only write in the whole system):"
echo "   PATCH /api/tasks/$TASK_ID/state"
curl -s -X PATCH "$API/tasks/$TASK_ID/state?role=CLINICAL" \
  -H 'Content-Type: application/json' \
  -d "{\"status\":\"IN_PROGRESS\",\"assignee\":\"$ASSIGNEE\",\"note\":\"$NOTE\"}" >/dev/null

echo
echo "CLAIMED"
task_line
echo
db_counts

echo
echo "Now rebuild everything from the CSVs — every task row is dropped and recomputed..."
restart_server

echo
echo "AFTER THE REBUILD"
task_line
echo
db_counts
echo
echo "The task row was destroyed and recreated. The note was not, because it is"
echo "keyed on what the work IS, not on the row id."
echo
echo "./demo/reset.sh clears it."
echo
