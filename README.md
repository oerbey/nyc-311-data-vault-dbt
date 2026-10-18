# NYC 311 Service Analytics — dbt + Data Vault 2.0

A portfolio project for an **historized NYC 311 service-request analytics warehouse**, using Python, DuckDB, dbt Core, and Data Vault 2.0.

**Status: Planning complete; data ingestion, dbt models, tests, and dashboard are not yet implemented.**

## Project documentation

- [Project plan — architecture, data ingestion, history, model contracts](docs/PROJECT_PLAN.md)
- [Data Vault architecture — Hubs, Links, Satellites, PIT and grains](docs/DATA_VAULT_ARCHITECTURE.md)
- [NYC 311 source-to-target field mapping](docs/SOURCE_DATA_DICTIONARY.md)
- [Implementation milestones and acceptance criteria](docs/IMPLEMENTATION_ROADMAP.md)
- [Instructions for AI coding agents](AGENTS.md)
- [Initial Python requirements](requirements.txt)

## Data source

[NYC Open Data — 311 Service Requests, 2020 to Present](https://data.cityofnewyork.us/d/erm2-nwe9) · [Socrata API](https://data.cityofnewyork.us/resource/erm2-nwe9.json)

The source provides request creation and closure event dates plus **current** statuses. Our pipeline will preserve *observed* status changes by taking periodic snapshots. A single download cannot reconstruct all historical 311 status transitions.

The initial development cohort will be a bounded DEP/DSNY sample from January–June 2026, with an offline synthetic fixture for reproducible CI. This is not a citywide completeness claim.

## Stack (planned)

Python 3.11+, httpx, DuckDB, dbt Core / dbt-duckdb, pytest, Ruff, GitHub Actions, and Streamlit.

## Setup (dependencies only — not yet a working pipeline)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Dependencies specify initial compatible ranges but are **not pinned and not yet installation-tested**. For the next engineering task, implement [M1 — synthetic local environment](docs/IMPLEMENTATION_ROADMAP.md#m1--reproducible-environment--synthetic-demo). Keep Socrata tokens (if used) in your local `.env`; never commit credentials or generated databases.

## Attribution and intended use

Data source: NYC Open Data, NYC311. Source field definitions are derived from the NYC 311 2020–Present data dictionary supplied by the project owner. The data dictionary is not committed to this repository. Check current official dataset metadata and terms before using real records.
