# Model of Care Engine

A clinical rules engine for a population health platform: it reads patient data, decides
which patients qualify for which clinical programs, stratifies them by risk, derives the
care each one needs, and turns unmet needs into actionable tasks routed to the right staff
role.

Built for the ecares "Model of Care" case study.

> **Status:** in progress. Setup instructions, architecture notes, and the documented
> assumption list are filled in as each phase lands.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy, SQLite
- **Frontend:** Vite + React + TypeScript
- **Rules:** YAML configuration, validated and version-hashed at load time

## Quickstart

_To be completed._

## Layout

```
data/        provided CSVs (input, committed as-is)
backend/     ingest, rules config, engine, API
frontend/    React SPA
docs/        ARCHITECTURE.md
```
