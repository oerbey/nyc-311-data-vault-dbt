# NYC 311 Service Analytics — dbt + Data Vault 2.0

A portfolio project for an **historized NYC 311 service-request analytics warehouse**, using Python, DuckDB, dbt Core, and Data Vault 2.0.

**Status: Local Python environment and an unexecuted API connection notebook are available. Warehouse ingestion, dbt models, synthetic fixtures, and dashboard are not yet implemented.**

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

## Setup (environment and connection notebook)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Launch from the repository root:
jupyter lab output/jupyter-notebook/nyc_311_api_connection.ipynb
```

Validated on macOS ARM64 with Python 3.13.5: dependency installation, `pip check`, dbt CLI/adapter discovery, JupyterLab CLI, and an in-memory DuckDB query passed. [requirements-lock.txt](requirements-lock.txt) records this environment's exact resolved versions; use `python -m pip install -r requirements-lock.txt` to install those versions. Other platforms have not been tested. Full dbt project execution remains pending M1.

Open the [API connection notebook](output/jupyter-notebook/nyc_311_api_connection.ipynb) with the `.venv/bin/python` kernel and run its cells in order. The request cell loads your selected fields into `df`, using your `created_date > 2024-10-18T10:17:14` filter and a configurable 1,000-row default limit. It is a connection-test sample, outside the planned warehouse cohort; it does not paginate or load warehouse tables. The API request is left for you to run; live connectivity and selected field availability have not been verified. Notebook validation, lint, and mocked response/error/retry checks passed offline.

To create a local table using that API connection, run [the SQL runner notebook](output/jupyter-notebook/run_sql_queries.ipynb) from top to bottom. It runs the API notebook, registers its DataFrame as `api_service_requests`, and executes [create_311_service_request.sql](create_311_service_request.sql) in the same DuckDB connection. The result is `"311_service_requests"` in `data/nyc_open_data.db`, with the selected source columns plus observation time and source provenance. Rerunning replaces this exploratory sample table; the default is 1,000 rows, not a complete cohort or historical warehouse. The SQL file depends on the runner's registered DataFrame. Keep the resulting database and detailed notebook outputs local.

The public SODA2 request can use an optional `SOCRATA_APP_TOKEN` environment variable or local `.env` entry. Never commit credentials, extracts, or notebook outputs containing detailed locations. Clear notebook outputs before committing. Remaining foundation work is tracked under [M1](docs/IMPLEMENTATION_ROADMAP.md#m1--reproducible-environment--synthetic-demo).

## Attribution and intended use

Data source: NYC Open Data, NYC311. Source field definitions are derived from the NYC 311 2020–Present data dictionary supplied by the project owner. The data dictionary is not committed to this repository. Check current official dataset metadata and terms before using real records.
