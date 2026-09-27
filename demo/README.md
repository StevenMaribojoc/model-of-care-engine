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

## Keep run history across restarts

```bash
MOC_REBUILD_ON_STARTUP=false ./run.sh
```

On a later boot this keeps the existing database, so every engine run created so
far stays queryable and any past `as_of` reads a stored result rather than
recomputing one.
