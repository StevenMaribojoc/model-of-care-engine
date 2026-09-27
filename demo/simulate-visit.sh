#!/usr/bin/env bash
#
# Live demo: the engine re-derives when new clinical data arrives.
#
#   ./demo/simulate-visit.sh          picks the most overdue PCP task
#   ./demo/simulate-visit.sh P0081    or name a patient
#   ./demo/reset.sh                   put everything back
#
# Appends one completed visit to data/encounters.csv and restarts the API so it
# re-ingests. Nothing is mocked or special-cased: this is the same ingest path
# a real EHR feed would land on. The task closes itself because tasks are
# derived from clinical facts, not stored and manually ticked off.
#
set -euo pipefail
cd "$(dirname "$0")/.."
. demo/lib.sh

CSV="data/encounters.csv"
VISIT_DATE="${VISIT_DATE:-2026-04-01}"

curl -sf "$API/health" >/dev/null 2>&1 || {
  echo "API is not running on port $PORT — start it with ./run.sh first"; exit 1;
}

# Refuse to stack changes: running twice without resetting would append a
# second row and make the "before" state a previous demo rather than the
# shipped dataset.
if ! git diff --quiet -- "$CSV" 2>/dev/null; then
  echo "$CSV already has uncommitted changes — run ./demo/reset.sh first"; exit 1
fi

PATIENT="${1:-}"
if [ -z "$PATIENT" ]; then
  PATIENT="$(curl -s "$API/tasks?role=SCHEDULER&specialty=PCP&limit=1" \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["patient_id"])')"
fi

show() {
  curl -s "$API/patients/$PATIENT?role=CLINICAL" | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
pcp=[n for n in d['needs'] if n['target']=='PCP']
tasks=[t for t in d['tasks'] if t['target']=='PCP']
print(f\"   patient    {d['patient']['patient_id']}  {d['patient']['name']}\")
if pcp:
    n=pcp[0]
    over=f\" — {n['days_overdue']} days overdue\" if n['days_overdue'] else ''
    print(f\"   PCP need   {n['status']}{over}\")
    print(f\"   last seen  {n['last_completed_date'] or 'never'}\")
print(f\"   PCP task   {tasks[0]['task_type'] if tasks else 'none — nothing for staff to do'}\")
"
}

total() {
  curl -s "$API/summary?role=CLINICAL" \
    | "$PY" -c 'import json,sys; print(sum(json.load(sys.stdin)["tasks_by_type"].values()))'
}

echo
echo "BEFORE"
show
echo "   open tasks across the population: $(total)"

echo
echo "The EHR reports a completed visit. One row appended to $CSV:"
printf '   %s,PCP,%s,Dr. New Visit\n' "$PATIENT" "$VISIT_DATE"
printf '%s,PCP,%s,Dr. New Visit\n' "$PATIENT" "$VISIT_DATE" >> "$CSV"

echo
echo "Re-ingesting..."
restart_server

echo
echo "AFTER — nobody closed anything by hand"
show
echo "   open tasks across the population: $(total)"
echo
echo "Refresh the browser to see it. ./demo/reset.sh puts it back."
echo
