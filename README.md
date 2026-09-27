# Model of Care Engine

A clinical rules engine for a population health platform. It reads patient data,
decides which patients qualify for which clinical programs, stratifies them by
risk, derives the care each one needs, and turns unmet needs into tasks routed to
the right staff role.

Built for the ecares "Model of Care" case study.
Architecture document: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Quickstart

```bash
./run.sh
```

Then open **http://localhost:8000**. API docs at `/docs`.

The script creates a virtualenv, installs dependencies, builds the frontend, and
serves the API and the SPA together on one port. Re-running skips whatever is
already current.

**Requirements:** Python 3.10+ (3.12 recommended) and Node 18+.
No database to install — SQLite is rebuilt from `data/*.csv` at every startup, so
there is nothing to migrate and no state to clear between runs.

```bash
./run.sh --rebuild     # force a fresh frontend build
./run.sh --api-only    # skip the frontend entirely (no Node needed)
PORT=9000 ./run.sh     # different port
```

**Keeping run history across restarts.** By default the database is rebuilt at
every startup, so engine runs do not survive a restart. Set
`MOC_REBUILD_ON_STARTUP=false` on a later boot to keep the existing database, and
every run created so far stays queryable — pick any past `as_of` in the UI and you
are reading a stored result rather than recomputing it. That is the auditability
property the `(as_of_date, rules_version)` key exists for; retention is a config
choice, not a schema change.

<details>
<summary>Manual setup, without the script</summary>

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

cd frontend && npm install && npm run build && cd ..   # builds into backend/app/static
cd backend && ../.venv/bin/python -m uvicorn app.main:app --port 8000
```

For frontend development with hot reload, run the backend as above and in another
terminal `cd frontend && npm run dev` (port 5173, proxies `/api` to 8000).
</details>

<details>
<summary>Docker</summary>

```bash
docker build -t model-of-care . && docker run -p 8000:8000 model-of-care
```

Then open **http://localhost:8000**. Multi-stage build: Node compiles the SPA,
which is copied into a Python image that serves it alongside the API. ~300 MB,
builds in about a minute. The container holds no state — the database is rebuilt
from the CSVs at startup — so it can be restarted or replaced freely.

Verified: builds clean, reports healthy, and serves identical results to the
local path (273 tasks, same tier distribution).
</details>

### Tests

```bash
cd backend && ../.venv/bin/python -m pytest -q     # 49 tests, ~1s
```

---

## What it does

**Two programs**, fully specified in [`backend/config/programs.yaml`](backend/config/programs.yaml):

- **Primary Care Wellness** — everyone 18+; High Priority if 65+ or any chronic
  diagnosis (PCP every 180 days), otherwise Standard (365 days).
- **Diabetes Management** — anyone with an E10.x / E11.x diagnosis, stratified by
  the most recent HbA1c in the last six months into High / Moderate / Low /
  Unmonitored, each with its own set of specialist cadences.

**Results on the supplied data** (evaluated as of 2026-04-08):

| | |
|---|---|
| Patients | 300 (12 minors, excluded from wellness only) |
| Enrollments | 403 — 288 wellness, 115 diabetes |
| Tiers | 217 High Priority · 71 Standard · 29 High Risk · 39 Moderate · 24 Low · 23 Unmonitored |
| Needs | 621 — 154 due, 191 never seen, 125 scheduled, 151 satisfied |
| Tasks | **273** — 154 scheduling, 119 referral |
| Gaps that generate no task | 72 (no PCP history — see assumption 7) |
| Engine runtime | ~20 ms for the full population |

Stratification was verified patient-by-patient against a second, independent
implementation written straight from the CSVs: **0 mismatches across 300 patients
and both programs.** That cross-check found a real bug — see "ICD matching" below.

---

## Using the UI

- **Role switch** (top right) — Scheduler sees only scheduling tasks; Clinical
  Team sees scheduling and referrals. Enforced server-side.
- **Evaluate as of** — a date picker. Everything is computed relative to it.
- **Worklist tab** — one row per task. **Patients tab** — one row per patient,
  showing needs as well as tasks, so gaps that generate no work stay visible.
- Click any patient name for the evidence behind every decision.
- All state lives in the URL, so any filtered view is a shareable link.

**P0002 is the clearest single example**: enrolled in both programs, with the
evidence for each tier, a Cardiology need that is `SCHEDULED` despite the patient
never having been seen (an appointment exists), two referrals, and two satisfied
needs.

---

## Documented assumptions

Where the spec was ambiguous, I made a call and recorded it.

1. **`as_of` is an explicit input; the engine never calls `now()`.** It defaults to
   **2026-04-08**, the day after the latest clinical data point. The supplied data
   was generated around early April 2026: encounters run forward to 2026-08-12
   because the file mixes history with future appointments. Evaluated at a real
   present-day date instead, there are **zero** upcoming encounters and only 10 of
   115 diabetics have a current A1C, so every diabetic falls to Unmonitored.
   Overridable per request with `?as_of=`.
2. Encounters dated `<= as_of` are completed; later ones are upcoming. Labs and
   diagnoses after `as_of` are invisible, so evaluating a past date cannot leak
   future facts backwards. Age is computed at `as_of`.
3. **"Within the last 6 months"** is half-open: `result_date > as_of − 6 months`.
   A result dated exactly six months back does not count. No A1C in the data sits
   on that boundary, so this is unobservable from the data and pinned by test.
4. **"Older than cadence" is strictly greater.** A visit exactly one cadence ago
   is still satisfied; one day more is due.
5. **An upcoming encounter suppresses the task outright** — the spec taken
   literally. The appointment date is *not* compared against the due date, so an
   appointment eleven months out still suppresses. See "What I would change".
6. **ICD matching respects code structure.** A prefix containing a decimal may
   extend freely (`G47.3` matches `G47.33`, which the spec's "G47.3x" requires),
   while a bare category prefix must stop at a boundary (`I10` does not match a
   longer category). A naive `startswith` gets the first right and the second
   wrong; a strict boundary check gets it exactly backwards. **This was a real bug**,
   caught by the independent cross-check — P0139 (obstructive sleep apnea) was
   being under-stratified to Standard.
7. **No PCP history produces a need but no task.** Referral tasks apply only to
   specialists, so these 72 patients appear on nobody's worklist. They are still
   recorded, visible, and filterable, because they are precisely the population
   that falls through the cracks today.
8. **Minors are processed, not skipped.** They appear in the roster and are
   evaluated against every program; they are simply not *eligible* for wellness.
   Diabetes has no age floor, so a diabetic minor would be managed.
9. **Missing data fails closed.** An unknown birthday cannot satisfy an age floor,
   so it can never promote someone into a higher-risk tier. A patient with an
   unparseable date is kept, not dropped.
10. **Exact duplicate rows are collapsed at ingest and counted.** The supplied
    `encounters.csv` contains one (P0231 / PCP / 2024-10-02), so 1158 of 1159 load.
11. **Tier priority (1 = most urgent) is my own scheme**; the spec defines none.
    Used for worklist ordering. Unmonitored is priority 1 alongside High Risk: no
    current A1C is itself urgent.
12. **Ties on the most recent lab date break toward the higher value**, so a
    patient is never under-stratified by row order, and runs are deterministic.

---

## Where I cut corners, and why

- **Tasks have no lifecycle.** Every run regenerates them; there is no "in
  progress", no assignee, and no persistence of status across runs. This is the
  largest omission and a deliberate one — a real task lifecycle is a feature in
  its own right, and the case study asks for generation. The schema is ready for
  it: tasks have a natural key of `(patient, task_type, need_type, target)` to
  upsert against.
- **The whole population is loaded into memory** to evaluate. Correct for 300
  patients and fine for a single tenant; the fix is to stream in id-ordered
  chunks, which works because patients are evaluated independently.
- **No authentication.** `role` is a query parameter standing in for a token
  claim. Explicitly out of scope per the brief, and the enforcement point is
  already server-side, so wiring real auth changes only how the role is obtained.
- **SQLite and no migrations.** The database is rebuilt from the CSVs at startup.
  The models are plain SQLAlchemy with no dialect-specific types and port to MSSQL
  by changing the URL; a real deployment needs Alembic.
- **Test coverage is targeted, not broad.** 49 tests aimed at the boundaries the
  dataset cannot observe and at role visibility. I mutation-checked the important
  ones (reverting the ICD fix, relaxing the cadence to `>=`, letting a never-seen
  PCP produce a referral, making visibility ignore the role) to confirm they
  actually fail — a test that passes while the logic is broken is worse than none.
- **The UI was verified by contract, not visually.** No browser was available in
  the development environment, so every field each component reads was checked
  against live API responses and TypeScript strict mode compiles clean, but the
  rendered page was not inspected programmatically.

## What I would change with more time

1. **Compare the appointment date against the due date.** Today any upcoming
   appointment suppresses the task (assumption 5). A patient 400 days overdue with
   an appointment eleven months out should still be worked. The fix belongs in the
   visit resolver and is a few lines; I kept the literal reading because the spec
   is explicit and I would rather flag the divergence than invent policy.
2. **Persist task state across runs.** Upsert by natural key, auto-close a task
   when its need becomes satisfied or scheduled, and keep assignment and notes.
3. **Account for appointment status.** The source data has no cancelled/no-show
   flag, so every future encounter is taken at face value. A cancelled appointment
   currently suppresses a task forever. This is the most clinically dangerous gap
   in the data model and the first thing I would ask about in a real integration.
4. **Outreach as a first-class task type.** 74 adults have never seen a PCP; 2
   have an appointment booked, leaving **72 with an open gap and no task**, and 67
   of those 72 have an assigned PCP on file — attributed but unengaged. They
   generate no work under the spec. A third task type routed to care coordinators
   would close that hole; the engine already records the need.
5. **LOINC codes instead of free-text lab names**, and code sets resolved against
   a terminology service rather than string prefixes.
6. **Incremental evaluation** driven by EHR change events, rather than
   re-evaluating everyone.

---

## For an EHR integration (eCW)

Booking against a live EHR is a write into a system we do not control, so:
idempotency keys on every booking call; retries with exponential backoff and
jitter for 5xx and timeouts, never for 4xx; an outbox with a dead-letter queue; a
"pending confirmation" task state to make the in-flight window explicit; and
periodic reconciliation, because the EHR is the source of truth and the
consistency model is eventual. Appointment status must come back with it.

---

## Layout

```
data/                    provided CSVs (input, committed as-is)
backend/
  config/                programs.yaml, reference.yaml  ← all clinical rules
  app/
    domain/models.py     pure dataclasses the engine speaks in
    rules/               config schema, validation, predicate registry
    engine/              facts → stratify → resolvers → tasks (no DB, no clock)
    db/                  SQLAlchemy models, ingest, seeding, repository
    services/            run cache, filtered queries, role visibility
    api/                 routes and wire schemas
  tests/                 49 targeted tests
frontend/src/            React + TypeScript SPA
docs/ARCHITECTURE.md     required architecture document
run.sh                   one-command local setup and serve
```
