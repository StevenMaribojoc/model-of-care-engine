# Architecture

A clinical rules engine: which patients qualify for which programs, what risk tier
they fall into, what care they need, and who acts on it.

Assumptions, trade-offs, and known gaps are in the [README](../README.md).

---

## Components

```
data/*.csv ──ingest──▶  SOURCE tables         (immutable facts)
config/*.yaml ──load──▶ RULES                 (validated, compiled, hashed)
                              │
              ┌───────────────┴────────────────┐
              │   ENGINE  (pure Python)        │  no SQLAlchemy, no FastAPI,
              │   facts → eligibility → tier   │  no clock
              │   → needs → tasks              │
              └───────────────┬────────────────┘
                              ▼
                    DERIVED tables, keyed by engine_run
                              │
                    Services  (run cache, filtered reads, role visibility)
                              │
                    FastAPI /api/*  ◀──  React SPA (same process, same origin)
```

One process serves the API and the built SPA, so setup is one command and there is
no CORS outside development. The layering is what matters, not the packaging —
each boundary is one that could be cut:

| Concern | Today | In production |
|---|---|---|
| Ingest | CSV → SQLite at startup | eCW feed → MSSQL, incremental |
| Evaluation | on demand, cached per run | scheduled batch job |
| API | reads derived tables | unchanged |
| Frontend | bundle served by the API | Azure Static Web Apps |

Because the engine is a pure function `evaluate(records, rules, as_of)`, moving
evaluation to a nightly job changes one service function: `ensure_run` becomes a
lookup that raises instead of computing. Nothing downstream notices.

---

## Data model

Three layers that never blur together.

**Source** — `patient`, `diagnosis`, `lab_result`, `encounter`. Facts as received;
the engine only reads them. There is exactly one patient table, deliberately.

**Reference / rules** — `specialty`, `code_set`, `program`, `risk_tier`,
`need_requirement`, `role_task_visibility`. Seeded from YAML, content-hashed into a
`rules_version`.

**Derived** — owned by a run:

```
engine_run(run_id, as_of_date, rules_version)    UNIQUE(as_of_date, rules_version)
  └─ enrollment(patient_id, program_id, tier_id NULL, evidence JSON)
       └─ clinical_need(need_type, target, cadence_days, status,
                        last_completed_date, next_scheduled_date, due_date)
            └─ task_need ──▶ task(task_type, need_type, target, priority, due_date)
```

Decisions worth defending:

- **A run is identified by `(as_of_date, rules_version)`.** Results are additive,
  never updated in place, so re-running under changed rules produces a new run
  rather than overwriting the old answer. "What did this worklist say in January,
  under January's rules?" is a query.
- **`evidence` JSON on the enrollment** records *why* a patient landed in a tier
  (`{"test": "HbA1c", "value": 9.4, "result_date": "2025-11-10"}`). A tier
  assignment that cannot explain itself is not usable clinically.
- **Needs and tasks are separate because their cardinalities differ.** A need is
  clinical, recorded per program, because quality is measured per program. A task
  is operational, recorded per unit of work, because a human does it once.
  `task_need` is many-to-many: one appointment closes two programs' gaps, and
  closing the task can report both.
- **One `task` table with a `task_type` discriminator.** Both types share a
  lifecycle and a worklist. Type-specific fields would go in a 1:1 extension table.
- **`requires_referral` is a column on `specialty`.** That turns "referrals apply
  only to specialists, not primary care" from an `if target == "PCP"` branch into
  a property of the data.

Indexes follow the access paths: `(patient_id, specialty, encounter_date)`,
`(patient_id, test_name, result_date)`, `(run_id, task_type, target)`.

---

## Rules as data

Both programs are fully specified in `backend/config/programs.yaml` — no Python.

```yaml
- code: DIABETES_MGMT
  eligibility: { has_diagnosis: DIABETES }
  tiers:
    - code: HIGH_RISK
      priority: 1
      criteria: { lab_latest: { test: HbA1c, within_months: 6, gte: 9.0 } }
      needs: [ { type: visit, target: Endocrinology, every_days: 90 } ]
```

Criteria compile at startup into predicates `(PatientFacts) -> (matched, evidence)`,
with `all` / `any` / `not` combinators; an empty `{}` always matches, which is how
"all other eligible patients" is expressed. Tiers evaluate in order, first match
wins — ordering the decision instead of demanding mutually exclusive predicates.

Anything unresolvable **fails at boot**, never silently at runtime: unknown
predicates, undeclared code sets, unknown lab tests, need targets that are not real
specialties, non-positive cadences, duplicate codes, and task types no role can see.

---

## Engine

```
for each patient:
    facts = build_facts(patient, as_of)     # once, shared by every program
    for each active program:
        tier = first tier whose criteria match
        needs += RESOLVERS[need_type](requirement, facts, ctx)
tasks = merge(needs)                        # across programs
```

`build_facts` is the scaling lever: digesting each patient once (age, diagnosis
prefixes, latest lab per test, last and next visit per specialty) means the tenth
program costs little beyond evaluating its criteria, and no two programs can
disagree about "most recent A1C".

**`as_of` is an input, never `now()`.** Data dated after it is invisible, so
evaluating a past date reproduces what was knowable then, and the engine is
deterministic and testable with hand-written patients.

The visit resolver's ordering *is* the rule: an upcoming appointment wins outright
(`SCHEDULED`, no task — checked first, so a patient can be both overdue and
scheduled); otherwise no completed visit means `NEVER_SEEN`, producing a referral
only where the specialty requires one and **no task at all** for primary care;
otherwise `as_of > last + cadence` is `DUE`; otherwise `SATISFIED`.

Merging groups actionable needs by `(patient, need_type, target)` and takes the
strictest value on each axis: most urgent program sets priority, earliest deadline
sets the due date.

---

## API and role visibility

`GET /api/meta` (filter options, generated from config) · `/tasks` · `/patients` ·
`/patients/{id}` · `/summary`. All accept `as_of`; task endpoints take `role`.

**Visibility is enforced in the query layer and nowhere else.** Every function
returning tasks intersects the requested filters with what the role may see, so a
scheduler asking for `REFERRAL` gets an empty page — not an error confirming the
rows exist, and not the rows. A new endpoint cannot forget it, because the only
way to obtain tasks is through a function that takes a role. `role` stands in for
a verified token claim; swapping it changes how the role is *obtained*, never how
it is *enforced*. The frontend contains no filtering of its own — hiding rows in a
browser is theatre.

---

## Extending and scaling

**A new program** from existing predicates: a YAML block. Verified, not asserted —
adding a Chronic Kidney Disease program mid-build made it appear in the UI's
dropdowns, enrolled 18 patients, and generated tasks, with no Python or TypeScript
changed. It also produced the first genuinely merged tasks, one Cardiology
appointment serving both CKD and Diabetes.

**A new criterion** ("≥2 ED visits in 12 months"): one function, one `@register`.

**A new need type** — the `lab_order` case: uncomment its block in `reference.yaml`,
add `LAB_ORDER` to the clinical role, write a ~15-line resolver, add
`{ type: lab_order, target: HbA1c, every_days: 90 }` to a tier. No migration, no
change to task generation, no API or frontend change — merging never asks what a
need *is*, and both layers are written against `need_type`/`target` rather than
the word "visit". Routing lives in the resolver because whether a gap produces a
referral, a task, or nothing is a property of the need type.

**Thousands of programs:** facts computed once per patient; rules stay in data,
moving behind an admin UI with effective dating and approval; LOINC codes replace
free-text lab names.

**Millions of patients:** patients are evaluated independently, so the loop is
embarrassingly parallel — partition by patient id across workers. Move evaluation
to a batch job and re-evaluate only patients whose EHR data changed. Stream
patients in id-ordered chunks rather than loading the population (the one place
the current code needs work). Queries are already indexed and paginated; Redis
would cache hot worklists.

---

## Principal trade-offs

| Decision | Why | Cost |
|---|---|---|
| `as_of` explicit, never `now()` | Deterministic, re-runnable for any date | One more parameter everywhere |
| Rules in YAML, projected into tables | Readable and diffable; tables ready to become the system of record | Two representations |
| Evaluate whole population, cache per run | Simple; ~20 ms for 300 patients | Loads everything into memory |
| Needs per program, tasks merged | Measurement stays per program; staff get one task | Extra join to explain a task |
| Tasks regenerated per run | Scope | No status or assignment across runs |
| Upcoming appointment always suppresses | The spec, taken literally | An appointment 11 months out suppresses |

The last two are what I would address first; both are detailed in the README.
