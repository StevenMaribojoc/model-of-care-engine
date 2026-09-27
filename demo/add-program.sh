#!/usr/bin/env bash
#
# Live demo: adding a whole clinical program is a configuration edit.
#
#   ./demo/add-program.sh     add Chronic Kidney Disease, restart, show the diff
#   ./demo/reset.sh           put the config back
#
# Appends a program to config/programs.yaml and the code set it needs to
# config/reference.yaml, then restarts. No Python and no TypeScript changes:
# the engine compiles the new rules, the API advertises them, and the UI builds
# its filter dropdowns from that.
#
# The new program deliberately wants Cardiology, which Diabetes Management also
# wants -- so it also demonstrates task merging on real patients.
#
set -euo pipefail
cd "$(dirname "$0")/.."
. demo/lib.sh

PROGRAMS="backend/config/programs.yaml"
REFERENCE="backend/config/reference.yaml"

curl -sf "$API/health" >/dev/null 2>&1 || {
  echo "API is not running on port $PORT — start it with ./run.sh first"; exit 1;
}

if ! git diff --quiet -- "$PROGRAMS" "$REFERENCE" 2>/dev/null; then
  echo "config already has uncommitted changes — run ./demo/reset.sh first"; exit 1
fi

snapshot() {
  "$PY" - <<'PY'
import json, urllib.request, os
api = f"http://localhost:{os.environ.get('PORT','8000')}/api"
get = lambda p: json.load(urllib.request.urlopen(api + p))

meta = get("/meta")
summary = get("/summary?role=CLINICAL")
tasks = get("/tasks?role=CLINICAL&limit=500")["items"]
merged = [t for t in tasks if len(t["program_codes"]) > 1]

print(f"   programs        {len(meta['programs'])}  ({', '.join(p['code'] for p in meta['programs'])})")
print(f"   rules version   {meta['rules_version']}")
print(f"   enrollments     {sum(summary['enrollments_by_tier'].values())}")
print(f"   open tasks      {sum(summary['tasks_by_type'].values())}")
print(f"   merged tasks    {len(merged)}   (one appointment, more than one program)")
for t in merged[:3]:
    print(f"       {t['patient_id']}  {t['target']:<12} {' + '.join(t['program_codes'])}")
PY
}

echo
echo "BEFORE"
snapshot

echo
echo "Appending a new program to $PROGRAMS:"
cat <<'YAML'

  - code: CKD_MGMT
    name: Chronic Kidney Disease
    eligibility: { has_diagnosis: CKD }
    tiers:
      - code: CKD_STANDARD
        name: CKD Standard
        priority: 2
        criteria: {}
        needs:
          - { type: visit, target: Nephrology, every_days: 180 }
          - { type: visit, target: Cardiology, every_days: 180 }
YAML

cat >> "$PROGRAMS" <<'YAML'

  # Added by demo/add-program.sh -- removed again by demo/reset.sh
  - code: CKD_MGMT
    name: Chronic Kidney Disease
    description: Nephrology and cardiology follow-up for chronic kidney disease.
    eligibility:
      has_diagnosis: CKD
    tiers:
      - code: CKD_STANDARD
        name: CKD Standard
        priority: 2
        criteria: {}
        needs:
          - { type: visit, target: Nephrology, every_days: 180 }
          - { type: visit, target: Cardiology, every_days: 180 }
YAML

echo "...and the code set it refers to, in $REFERENCE:"
echo "     - code: CKD   prefixes: [N18]"
"$PY" - <<'PY'
path = "backend/config/reference.yaml"
text = open(path).read()
anchor = """  - code: DIABETES
    name: Diabetes (Type 1 or Type 2)
    prefixes: [E10, E11]"""
assert anchor in text, "reference.yaml anchor not found"
text = text.replace(anchor, anchor + """
  # Added by demo/add-program.sh -- removed again by demo/reset.sh
  - code: CKD
    name: Chronic Kidney Disease
    prefixes: [N18]""", 1)
open(path, "w").write(text)
PY

echo
echo "Reloading the rules..."
restart_server

echo
echo "AFTER — no Python or TypeScript changed"
snapshot
echo
echo "Refresh the browser: the new program is in the Program and Risk tier filters."
echo "./demo/reset.sh puts it back."
echo
