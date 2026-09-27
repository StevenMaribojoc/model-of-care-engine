# Demo scripts

Small helpers for walking through the system live. Not part of the application.

## Show that the engine re-derives from source data

```bash
./run.sh                      # in one terminal, leave it running
./demo/simulate-visit.sh      # in another
```

Picks the most overdue PCP task, appends one completed visit to
`data/encounters.csv`, restarts the API so it re-ingests, and prints the patient
before and after. The task closes itself, because tasks are derived from
clinical facts rather than stored and ticked off by hand.

```
./demo/simulate-visit.sh P0081   # or name a patient
./demo/reset.sh                  # restore the CSV and restart
```

`reset.sh` restores from git rather than a backup copy, so the baseline is
always the shipped dataset. `simulate-visit.sh` refuses to run on an already
modified CSV, so two demos cannot stack.

## Add a whole program without touching code

```bash
./demo/add-program.sh
```

Appends a Chronic Kidney Disease program to `backend/config/programs.yaml` and
the code set it needs to `reference.yaml`, then reloads. Prints before and
after: the program count, the rules version hash, how many patients enrolled,
and how many tasks are now driven by more than one program.

That last number is the interesting one. It goes from 0 to 2, because the new
program wants Cardiology for patients the diabetes program also wants seen --
so one appointment serves both, which is what the needs/tasks split exists for.
The two programs in the original spec target different specialties, so nothing
merges until a third one overlaps.

```bash
./demo/reset.sh
```

## Keep run history across restarts

```bash
MOC_REBUILD_ON_STARTUP=false ./run.sh
```

On a later boot this keeps the existing database, so every engine run created so
far stays queryable and any past `as_of` reads a stored result rather than
recomputing one.
