# NYC 311 Service Analytics — dbt + Data Vault 2.0

A portfolio project for an **historized NYC 311 service-request analytics warehouse**, using Python, DuckDB, dbt Core, and Data Vault 2.0.

**Status: Offline and bounded live landing ingestion, batch audit, deterministic fixtures, and dbt source checks are implemented and verified. Typed staging, canonical hashing, hub_request and sat_request_status are implemented; agency/problem Hubs and request-assignment Links are also implemented; assignment effectivity is implemented; remaining Satellites and dashboard remain pending.**

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

## Observed assignment effectivity

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . \
  --select +sat_lnk_request_agency_effectivity +sat_lnk_request_problem_effectivity
```

This adds `raw_vault.sat_lnk_request_agency_effectivity` and
`raw_vault.sat_lnk_request_problem_effectivity`. Each stores insert-only events at
`(link_hk, observed_from)` grain, with `request_hk`, `active_flag`, `hashdiff`,
`batch_id`, `record_source`, `mode`, and first warehouse `load_dts_utc`.
`observed_from` is the triggering capture's timezone-aware `snapshot_at_utc`,
not a City-side assignment event time. Initial eligible evidence activates a Link.
An explicit eligible replacement records old-Link `false` and new-Link `true`
at that capture. A return reactivates the original Link with a new event;
unchanged captures add nothing. Before first eligible evidence, assignment is unknown.

Agency and problem histories are independent. Missing agency cannot change either;
a known agency with a missing category can change agency effectivity while problem
remains last-known. Such incomplete captures remain flagged in staging. Last-known
problem and agency may therefore temporarily disagree; neither is a verified
current assignment. Failed/running batches, empty captures, absent requests,
and disappearance from an agency-filtered cohort never deactivate a Link.
Only successful, eligible, explicit replacement evidence does so.

Identical capture ties choose batch ID order initially; existing provenance and
warehouse timestamps remain fixed if an earlier-sorting identical batch arrives.
Deactivation lineage points to the replacement batch, not the original activation.
The two staging event views compare adjacent eligible captures per request and
produce the expected full retained timeline. Hashdiffs reuse SHA-256 v1 text
encoding with a single canonical `true`/`false` string and separate domains
`request_agency_effectivity` / `request_problem_effectivity`; existing hashes are unchanged.

Full refresh is blocked and schema changes fail. Missing/inconsistent Link parents
fail before insertion. Both effectivity models check already-stored history for
both relationship types: safe additive late events append, while evidence requiring
retraction or alteration of an existing event fails. Reconstruct such history in a
separate database; no automated reconciliation is provided. Retain original landing
evidence. Each dbt model commits independently, including dependency Hubs/Links;
this command is not an atomic transaction over the graph.

Inspect the fictional reassignment (false before true at a shared boundary):

```sql
select h.request_bk, a.agency_bk, e.observed_from, e.active_flag, e.batch_id
from raw_vault.sat_lnk_request_agency_effectivity e
join raw_vault.hub_request h using (request_hk)
join raw_vault.lnk_request_agency l using (link_hk, request_hk)
join raw_vault.hub_agency a using (agency_hk)
where h.record_source = 'synthetic_311' and h.request_bk = 'DEMO-002'
order by e.observed_from, e.active_flag;
```

The retained fixtures show DEP active February 1, DEP inactive February 2, and
DSNY active February 2. February 3 adds no event. This is observed history, with
no inferred transition time between captures. Business Vault intervals, assignment
reporting, lifecycle/resolution/location/agency Satellites and PIT remain later work.

Verification on 2026-10-18: all 124 Python tests (15 new effectivity tests) and Ruff
passed. Builds in a copy and the project database passed 114 dbt data tests, deriving
520 events per relationship type from retained source evidence. The six existing
Vault tables were unchanged. Replay preserved effectivity rows, batch provenance
and warehouse timestamps. Counts describe the current dataset, not permanent thresholds.

## Agency/problem identities and request Links

From `nyc_311_requests`, after a landing load:

```bash
dbt build --profiles-dir . --select +lnk_request_agency +lnk_request_problem
```

The leading `+` includes staging and all parent Hubs. This adds append-only `raw_vault.hub_agency`, `hub_problem`, `lnk_request_agency`, and `lnk_request_problem`. Each Link means its parent pair was **observed at least once**. It has no active flag or assignment event date. Returning to an earlier pair reuses the same Link; missing values and disappearance never deactivate it. The effectivity Satellites above track observed activation and replacement.

Agency identity is `(record_source, exact agency_bk)`; problem identity is `(record_source, exact agency_bk, complaint_type)`. The existing SHA-256 v1 contract uses domains `agency_hk` and `problem_hk`. Link hashes encode the ordered parent hashes `[request_hk, agency_hk]` / `[request_hk, problem_hk]` under domains `lnk_request_agency` / `lnk_request_problem`. The display business key `problem_bk` is `dv2:v1:problem_bk:2:` plus the existing canonical text encodings of agency and complaint type. Names/descriptors do not affect identity. Original case, whitespace, Unicode, delimiters and leading zeroes are retained; whitespace checks detect missing fields without normalizing valid keys. Existing request/status hash definitions are unchanged.

`staging.int_request_assignment_observations` retains every successful observation with batch/capture/source/filter provenance and eligibility flags. `has_missing_agency` and `has_missing_problem` independently describe the original fields. Missing agency makes both relationship types ineligible. A valid agency with a missing problem still contributes the agency Hub/Link. Missing problem values contribute no problem Hub/Link. Ineligible hashes/business-key encodings are NULL; no unknown members are fabricated. Inspect incomplete records with:

```sql
select batch_id, request_bk, agency_bk, complaint_type,
       has_missing_agency, has_missing_problem,
       is_agency_link_eligible, is_problem_link_eligible
from staging.int_request_assignment_observations
where has_missing_agency or has_missing_problem;
```

Every assignment model checks the full retained input for conflicting eligible agency/problem assignments at the same request/capture instant before inserting. Identical ties coalesce, while incomplete ties remain visible and do not invent an alternative assignment. Each Link also checks parent coverage and consistent source/mode metadata; missing parents fail rather than silently losing relationships. Full refresh is blocked and schema changes fail. New identities/pairs are chosen by capture time then batch ID on first load; their source/mode and warehouse load timestamps never reset on replay. Batch/capture lineage remains in the observation view. Earlier evidence can add a previously unseen pair without removing an existing one. Individual dbt models commit independently.

The project uses `flags.indirect_selection: buildable` so partial commands such as the existing status-only build do not run relationship tests against assignment models outside that build. Directly selecting a test still requires its relations to exist. All new models use the existing literal schemas and database.

Verification covers reassignment and return, incomplete keys and ties, exact/Unicode key encoding, namespaces, name/descriptor changes, safe late relationship evidence, replay, missing parents and refresh protection. All 109 Python tests (including 12 assignment integration cases) and Ruff passed. A build against the current database copy passed 94 selected dbt tests and matched eligible source tuples: **4 agencies, 31 problems, 519 agency Links, 519 problem Links**. Existing request/status rows and first-load metadata were unchanged. No API extraction or automatic assignment deactivation is performed by this slice.

## Request identity and status Raw Vault

After landing ingestion, run from the repository root:

```bash
cd nyc_311_requests
dbt build --profiles-dir . --select +sat_request_status
```

The leading `+` builds staging, hash preparation, and Hub dependencies before loading the status Satellite. This creates insert-only `raw_vault.hub_request` and `raw_vault.sat_request_status`. Both use the existing database and literal schema names. No network request is needed. Run loads sequentially and close other processes using DuckDB.

The Hub has one identity per **(`record_source`, `request_bk`)**. The original business key remains exact text; the stable source namespace prevents a synthetic key from colliding with a live key. The status Satellite has one version per **(`request_hk`, `snapshot_at_utc`)** at each observed status change. It retains raw `status`, `hashdiff`, `batch_id`, `record_source`, `mode`, and the first warehouse `load_dts_utc`. Capture time and load time have distinct meanings. Original timestamps and provenance remain unchanged on replay.

`dv_sha256(expressions, domain)` uses SHA-256 with an explicit v1 text encoding. The prefix is `dv2:v1:<domain>:<field-count>:`. Each field is `N;` for SQL NULL, or `S<UTF-8-byte-length>:<exact-text>;` otherwise. Field order is significant. Request hashes use domain `request_hk` with source namespace then request key; status hashdiffs use domain `request_status` with only the raw status. Hashes are lowercase hexadecimal. Empty strings, NULL, literal sentinels, delimiter-containing values, case, whitespace, leading zeroes and Unicode code points remain distinct. No trim, case fold, numeric coercion or Unicode normalization is applied. The macro is a text-serialization contract; future typed attributes need explicitly canonical text expressions before hashing. Changing the encoding/namespace requires a separately validated migration, not an in-place refresh.

Change detection compares adjacent observations ordered by capture time, so Open → Closed → Open produces three versions even though the first/last hashdiff matches. Unchanged later captures do not create status versions. NULL status is preserved as an observed missing value, not inferred to mean Open/Closed. Identical same-time observations across batches coalesce to one boundary; initial tie selection uses batch ID order. Existing Satellite provenance is never switched to a later arriving identical batch.

Preflight checks stop ambiguous same-time statuses before either Vault table loads. Safe late captures can append missing boundaries; a late capture that would remove/change an already stored boundary is rejected. No historical row is silently updated or deleted. Such backfills require reconstruction in a separate database and explicit reconciliation; automatic historical retraction is not implemented. Retain the landing observations used to derive existing history. `--full-refresh` is blocked for Raw Vault models, and schema changes fail instead of replacing existing structures. The Hub and Satellite are separate dbt transactions; if a later model fails unexpectedly, successfully inserted Hub identities remain valid and the load can be retried.

Internal views `staging.int_request_status_observations` and `staging.int_request_status_changes` prepare hashes and expected transitions. dbt tests check canonical hashes, namespace/grain uniqueness, parent/batch relationships, exact change coverage and original observation provenance. The offline Python tests independently reproduce hashes and exercise sequential changes, replay, NULL/empty transitions, namespace collisions, leading-zero keys, safe/unsafe late captures, missing Hub parents and full-refresh rejection.

Verification: 12 new integration tests plus the previous 85 Python checks cover this slice. The dependency build passes 58 dbt data tests. Against the current landing data, the result is 518 request identities (515 live, three synthetic) and 520 status versions (515 live, five synthetic). The full six-month live cohort has still not been downloaded. Other Hubs/Links/Satellites, effectivity, PIT and analytical marts remain future work.

## dbt staging

After a synthetic or live landing load, run from the repository root:

```bash
cd nyc_311_requests
dbt build --profiles-dir . --select stg_311_request_snapshots
```

This creates the view `staging.stg_311_request_snapshots`. It reads successful batches only and preserves one row per `(batch_id, unique_key)`, every original source value, and capture provenance. Both live and synthetic observations remain available through `mode`, `source_id`, `record_source`, and `requested_filter`; filter the intended population explicitly. Repeated observations and status reversals are preserved. `request_bk` and `agency_bk` are aliases of the original text keys; ZIP codes remain text. No Vault hashes or `load_dts_utc` are assigned.

Each source date string remains under its original name. Derived `created_at_nyc`, `closed_at_nyc`, `due_at_nyc`, and `resolution_action_updated_at_nyc` are local wall-clock TIMESTAMP values. Their `_utc` counterparts are timezone-aware instants under the explicit **America/New_York** interpretation. UTC columns are TIMESTAMPTZ: clients may display them in the session timezone; use `SET TimeZone='UTC'` when inspecting UTC clock values.

The parser accepts `YYYY-MM-DDTHH:MM:SS` or a space separator, with optional 1–6 fractional digits and surrounding ASCII whitespace. Offset-bearing strings, date-only values, impossible calendar dates, and invalid clock values are flagged; raw strings are preserved. Empty/whitespace-only values count as missing. For each original date field, `<field>_time_status` is one of:

| Status | Parsed local timestamp | UTC instant |
|---|---|---|
| `valid` | Available | Available |
| `missing` | NULL | NULL |
| `invalid_format` | NULL | NULL |
| `ambiguous_local_time` | Available | NULL |
| `nonexistent_local_time` | Available | NULL |

NYC spring-forward gaps and fall-back repeated times are never guessed. UTC conversion requires a unique valid local time. DST classification uses round-trip conversion and the adjacent one-hour alternatives for the dataset's modern NYC timestamps. `<field>_parse_failed` distinguishes format/calendar errors from missing values and DST issues.

`has_missing_required_fields` flags empty request/agency/problem/status or a missing creation date. `has_date_quality_issue` flags any malformed or DST-uncertain date; an optional missing closure/due/resolution date alone is not an error. `is_lifecycle_evaluable` requires both creation and closure UTC instants. `closed_before_created` flags reversed order and is NULL when either instant is unavailable. Quality flags retain rows for review; dbt tests verify structure and preservation rather than rejecting all source anomalies. `due_date` remains an expected agency update date, not a closure SLA.

The schema-name macro uses literal configured schemas (`staging`) instead of the default target prefix (`main_staging`). Models without a custom schema retain `target.schema`. This is a local warehouse convention: independent development environments should use separate database files.

Staging verification covers source preservation, composite grain, failed/running-batch exclusion, raw text/leading zeroes, malformed/null/blank dates, negative lifecycles, NYC DST gaps/folds and intervals across DST, and session-timezone independence. The full offline suite passes 85 Python checks; the staging build passes 26 dbt data tests. No API request is required to build staging.

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
