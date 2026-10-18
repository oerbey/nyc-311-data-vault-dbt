# NYC 311 Service Analytics — dbt + Data Vault 2.0

A portfolio project for an **historized NYC 311 service-request analytics warehouse**, using Python, DuckDB, dbt Core, and Data Vault 2.0.

**Status: Offline and bounded live landing ingestion, batch audit, deterministic fixtures, and dbt source checks are implemented and verified. Staging, Data Vault models, and dashboard remain pending.**

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

## Live NYC 311 landing ingestion

Activate `.venv` and run from the repository root. First validate the source schema and inspect the count; this command does not open or write a database:

```bash
python -m ingestion.landing --mode live --count-only
```

The default cohort is DEP/DSNY requests created on or after January 1, 2026 and before July 1, 2026. The live preflight on 2026-10-18 returned 302,762 rows; counts can change. The default safety guard is 500,000 rows. A cohort above the guard fails in full; it is never silently truncated. Narrow the dates or deliberately increase `--max-rows` after checking the count.

For a small complete first capture:

```bash
python -m ingestion.landing --mode live \
  --start-date 2026-01-01 --end-date 2026-01-02 \
  --page-size 200 --max-rows 5000
```

For the complete planned cohort, use `python -m ingestion.landing --mode live`. Both commands write to `data/nyc_open_data.db`; `--database /tmp/nyc311-live.db` selects a separate file. `--end-date` is exclusive and `--agencies DEP DSNY` selects agency codes. Page sizes must be 1–5,000. Close other DuckDB connections before loading or running dbt.

An optional `SOCRATA_APP_TOKEN` is read from the environment or repository `.env` (`override=False`) and sent only as an `X-App-Token` header. Copy `.env.example` to `.env` if needed; public reads also work without a token. Tokens, API response bodies, and request headers are never included in CLI/audit errors. The adapter uses fixed HTTPS endpoints and refuses redirects. See [Socrata application tokens](https://dev.socrata.com/docs/app-tokens.html).

Each live run receives a new UUID batch ID; `--batch-id` can supply an unused ID. A used live ID fails before networking, regardless of its status. Run the same cohort again under a new ID to append another observation. The existing offline fixture replay behavior is unchanged. The adapter never imports the exploratory API table or invokes an API notebook.

Before pagination, all 15 selected fields and their metadata types are validated. The extractor requests only landing fields, orders by `unique_key ASC`, advances offsets by actual returned rows, and fetches through an empty terminal page. It rejects malformed records, unexpected fields and duplicate identifiers within/across pages. HTTP timeouts/transport errors, 429 and 5xx responses receive up to three attempts with bounded backoff. `Retry-After` seconds/HTTP dates are honored for waits up to 30 seconds; longer waits stop the batch for a later retry. Other HTTP errors and schema mismatches fail immediately.

A `running` audit is persisted before networking. Successful page responses checkpoint fetched row/page counts without exposing snapshot rows. `page_count` includes the terminal empty page, but excludes metadata/count requests and retry attempts. New nullable `expected_rows_before` / `expected_rows_after` audit columns are added automatically to existing databases. Both counts must equal fetched rows before publication. Oversized, incomplete, changing or failed extracts retain a failed audit with zero accepted rows and no snapshot rows. Progress checkpoints are diagnostic, not resumable downloads; restart with a new ID. Hard termination can leave `running` audits, which downstream models must exclude.

For successful live runs, `started_at_utc` begins the capture, `snapshot_at_utc` marks the end of retrieval/reconciliation, and `finished_at_utc` marks publication. While running, snapshot time is provisional. Source event timestamps remain text; no Vault load timestamp or historical status before the first capture is inferred. Counts and unique keys detect common extraction inconsistencies but cannot prove transactional point-in-time consistency or detect every same-count source edit. Agency filtering defines current cohort membership; disappearance is not proof of deletion or reassignment. Tracking an individual request after it moves outside the agency filter requires a later tracked-ID design.

Run `python -m pytest -q tests` for the network-free adapter and landing tests. After a load, run `dbt test --profiles-dir . --select source:landing` from `nyc_311_requests`. dbt checks both source constraints and publication/count consistency.

Verification: all 15 fields were confirmed against live metadata; a complete January 1 DEP/DSNY cohort loaded 515 records across three data pages plus a terminal empty page, with before/after counts both 515. Sixty-nine automated Python checks, eleven dbt source/publication tests, and Ruff passed. No full six-month extract was downloaded during this verification.

## Offline landing zone

From the repository root, with `.venv` activated:

```bash
python -m ingestion.landing --mode synthetic
python -m pytest -q tests/test_landing.py
jupyter lab output/jupyter-notebook/landing_zone_walkthrough.ipynb
```

The command creates `landing.ingestion_batch` and `landing.request_snapshots` in `data/nyc_open_data.db`, then loads three fixed fictional batches: three successful audits and nine observations. The default path is anchored to the Python module, not the working directory. Use `--database /tmp/nyc311-demo.db` for a separate disposable database. The walkthrough uses the same loader and can use `NYC311_DEMO_DATABASE` to select a disposable file. Close other DuckDB connections before running ingestion or dbt.

The demo includes Open → Closed → Open, an agency reassignment, an unchanged request, and null source fields. Identical replay preserves original audit timestamps and row counts. Reusing a successful batch ID with changed content or metadata cannot overwrite prior observations; a new daily batch retains unchanged observations. Failed or interrupted IDs require a new ID for retry.

The loader accepts the planned 15 source fields as text/null and rejects unknown fields, non-text values, missing identifiers and duplicate identifiers. Missing optional fields become SQL NULL; blank strings and malformed source date strings remain unchanged for later staging validation. It persists no addresses or precise coordinates. Batch metadata must be valid before an audit can be created. Record validation failures are audited with zero committed rows; transaction failures roll back all snapshot rows and retain a safe failure summary. `rejected_rows` counts invalid records, not all uncommitted records; an insertion failure can have zero rejected rows. `page_count` is zero for offline batches. Empty supplied batches are recorded successfully with zero observations.

The checksum is SHA-256 of canonical source records sorted by request identifier, with missing fields normalized to null. Replay also compares source identity, provenance, mode, filter, capture time, and page count. `record_source` is included in the audit table as well as each snapshot to make provenance part of replay identity. Synthetic source/provenance is `synthetic_311`. Batch status moves from `running` to `success` or `failed`; snapshot rows and the success update commit in one transaction. Successfully loaded snapshot rows are immutable through the loader.

The dbt project declares both tables as sources. After loading, from the repository root:

```bash
cd nyc_311_requests
dbt test --profiles-dir . --select source:landing
```

Downstream models must join snapshots to their audit row and filter `status = 'success'`; select the intended mode/source explicitly when live batches are introduced. The landing layer preserves evidence, not typed staging or historical Vault versions. `snapshot_at_utc` is observation time; source event dates and future Vault `load_dts_utc` have separate meanings.

Verification: 27 Python checks, notebook execution from both working directories with network connections blocked, Ruff, and nine dbt source tests passed using temporary databases. No live API extraction was performed. The existing exploratory API table is not migrated or replaced by this loader.

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

To create a local table using that API connection, run [the SQL runner notebook](output/jupyter-notebook/run_sql_queries.ipynb) from top to bottom. It runs the API notebook, registers its DataFrame as `api_service_requests`, and executes [create_311_service_request.sql](sql_queries/create_311_service_request.sql) in the same DuckDB connection. The result is `"311_service_requests"` in `data/nyc_open_data.db`, with the selected source columns plus observation time and source provenance. Rerunning replaces this exploratory sample table; the default is 1,000 rows, not a complete cohort or historical warehouse. The SQL file depends on the runner's registered DataFrame. Keep the resulting database and detailed notebook outputs local.

The public SODA2 request can use an optional `SOCRATA_APP_TOKEN` environment variable or local `.env` entry. Never commit credentials, extracts, or notebook outputs containing detailed locations. Clear notebook outputs before committing. Remaining foundation work is tracked under [M1](docs/IMPLEMENTATION_ROADMAP.md#m1--reproducible-environment--synthetic-demo).

## Attribution and intended use

Data source: NYC Open Data, NYC311. Source field definitions are derived from the NYC 311 2020–Present data dictionary supplied by the project owner. The data dictionary is not committed to this repository. Check current official dataset metadata and terms before using real records.
