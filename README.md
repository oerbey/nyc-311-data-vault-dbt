# NYC 311 Service Analytics — dbt + Data Vault 2.0

A portfolio project for an **historized NYC 311 service-request analytics warehouse**, using Python, DuckDB, dbt Core, and Data Vault 2.0.

**Status: Landing/staging and required M3 Raw Vault models are verified. M4 observed status intervals, bounded status PIT and lifecycle duration metrics are implemented. M5 resolution-time, request-trend, agency/cohort performance and historical-status marts are implemented; the Streamlit dashboard is implemented and tested; follow-up review of the updated narrow layout and keyboard alternatives remains pending.**

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



## Run the offline synthetic demo

After creating and activating `.venv` and installing the project requirements, run
from the repository root:

```bash
make demo
make demo-dashboard
```

`make demo` loads the fictional fixtures and runs the full dbt build using
`config/demo/profiles.yml`. Its default database is `data/demo/nyc_open_data.db`;
the existing project database is not the default target. A successful replay keeps
the original observations and load timestamps. The commands use the installed
packages and make no live NYC API request. You can select a separate path, e.g.
`make demo DEMO_DATABASE=/tmp/nyc311-demo/nyc_open_data.db`; use the same setting
with `make demo-dashboard`. `make demo-load` and `make demo-build` run the steps
separately. `make lint` and `make test` run the local checks.

## Streamlit dashboard

With `.venv` activated, from the repository root:

```bash
python -m streamlit run dashboard/app.py \
  --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Open the local URL printed by Streamlit. The dashboard defaults to fictional
synthetic fixtures and requires one source/mode at a time. It offers event trends,
agency/cohort durations and observed status, with separate date/cohort/as-of filters,
coverage counts, exact NULL/blank labels and scoped JSON exports. Mean/median/p90
are pooled from eligible request rows, including valid zeroes. Unknown status is
never counted as open. These are retained samples, not citywide totals or SLA metrics.

The dashboard opens `data/nyc_open_data.db` read-only and closes each connection.
`Refresh data` rereads built views; ingestion and dbt run separately. To build
missing reporting views, run from `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_request_trends \
  +mart_agency_performance +mart_historical_status
```

Set `NYC311_DASHBOARD_DATABASE` to another already-built DuckDB file when needed;
relative values resolve from the repository root. Preserve the filename when copying
an existing database because DuckDB views bind its catalog name. No live API call or
warehouse write is made by the dashboard. Close warehouse writers before opening it.
See the [dashboard contract](docs/REPORTING_CONTRACTS.md#streamlit-dashboard) for
filter scope, provenance, exclusions and verification. The initial desktop and narrow-layout review found truncated labels and a chart keyboard gap. The dashboard now supplies full selection labels, compact metric rows, per-agency details and paged chart data tables. A follow-up rendered and keyboard review of these changes remains pending.

## Historical observed status — fourth M5 slice

From `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_historical_status
```

`marts.mart_historical_status` counts retained request identities by bounded UTC
as-of instant, source/mode, last observed agency and exact reported status. The
supporting request/as-of population retains independent status/agency capture,
batch and first-load lineage. Unknown status, observed NULL and blank status are
separate; missing agency observations never end a known assignment. Only exact
`Open`, `In Progress` and `Closed` labels receive those named categories; all other
labels stay visible. This is observed-time reconstruction from retained evidence.

The default calendar contains four loaded status-change capture instants: 518
identities per instant, 2,072 population rows and 20 report groups. The population
includes identities not yet observed at earlier instants. It is not historical
citywide backlog. Agency-only captures do not add default calendar instants; use
`pit_status_as_of_instants` and rebuild `+` dependencies for another bounded calendar.
See [reporting contracts](docs/REPORTING_CONTRACTS.md#historical-observed-status)
for denominators, query examples and coverage limits. M5 remains in progress;
the dashboard and remaining visual review are described above.

## Agency/cohort performance — third M5 slice

From `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_agency_performance
```

`marts.mart_agency_performance` groups retained requests by source/mode, exact agency
observed with the selected lifecycle version, and NYC creation-cohort month. It
exposes retained/eligible/excluded counts, usable reported closure-date coverage,
duration coverage, and eligible total/mean/median/p90 hours. Unknown cohorts and
missing agency labels remain explicit. `dimension_basis='lifecycle_capture'` makes
attribution clear; the report does not establish event-time agency responsibility.

Current retained sample: four groups, 518 requests, 498 usable closure dates and
498 eligible durations. Closure-date coverage and valid-duration coverage have
separate numerators and an explicit retained-request denominator. See the
[reporting contract](docs/REPORTING_CONTRACTS.md) for definitions, interpolation,
exclusions and comparison limits. M5 remains in progress; the dashboard and remaining visual review are described above.

## Request-event trends — second M5 slice

From `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_request_trends
```

`marts.mart_request_trends` reports separate creation and closure counts by NYC
event date, source/mode and exact observed agency/problem/borough. Context comes
from the landing row supporting the selected lifecycle version, with
`dimension_basis='lifecycle_capture'`; it does not establish assignment at an earlier
event date or the latest agency. Assignment-only changes leave this context fixed.
The request population/exclusion view and event view preserve audit lineage.

Current retained sample: 518 usable creations and 498 usable closures across 267
trend groups. These are scoped source-event counts, not backlog or citywide totals.
See the [reporting contracts](docs/REPORTING_CONTRACTS.md) for attribution,
eligibility, exclusions, reconciliation queries and limitations. M5 remains in progress.

## Resolution-time mart — first M5 slice

From `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_resolution_times
```

`marts.mart_resolution_times` publishes one row per source-scoped request, using
its latest loaded lifecycle capture. It preserves dates, durations, quality flags
and original provenance, and adds usable NYC event dates, creation cohort month,
and additive request/eligible/excluded counters. Zero durations remain eligible;
missing/invalid/reversed pairs retain their population rows and NULL valid durations.

Always filter source/mode explicitly. `sum(duration_eligible_count)` is the
mean/median/p90 duration denominator; `sum(request_count)` includes exclusions.
Usable closure-date counts are a separate population. See the
[reporting contract and example queries](docs/REPORTING_CONTRACTS.md) for grain,
coverage, date interpretation, exclusion rules and verification. Current retained
sample: 518 rows, 498 eligible durations and 20 exclusions. M5 remains in progress.

## Latest lifecycle duration metrics (2026-10-24)

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +bv_request_resolution_metrics
```

`business_vault.bv_request_resolution_metrics` is a view with **one row per request
Hub identity**, selecting the lifecycle version with greatest `snapshot_at_utc`.
This is latest loaded capture state, with no historical as-of grain or current-clock
calculation. A later warehouse load of an earlier capture cannot override the latest
capture. A new latest capture, including closure-date clearing, updates the existing
view without rebuilding; landing-only changes do not. Retained historical Raw Vault
versions remain intact. For historical status use the separate bounded status PIT;
its status pointer must not be used as a lifecycle pointer.

The view preserves original request business key/source/mode and the selected Raw
Vault lifecycle row's raw strings, typed NYC/UTC dates, per-field quality flags,
`hashdiff`, `batch_id`, capture and original `load_dts_utc`. It assigns no new load
clock or hashes. A Hub with no lifecycle version has `is_lifecycle_observed=false`,
NULL lifecycle fields and no duration. The latest version's capture is not the last
unchanged landing capture or proof of source freshness.

| Field | Definition |
|---|---|
| `is_duration_evaluable` | Both creation and closure have usable UTC instants; can include negative pairs |
| `reported_closure_elapsed_hours` | Signed `(closed_at_utc - created_at_utc)` in elapsed hours; negative values retained diagnostically |
| `is_duration_valid` | Both endpoints usable and closure >= creation |
| `resolution_hours` | Signed elapsed hours only when valid; otherwise NULL |
| `resolution_days` | Valid hours / 24; elapsed 24-hour days, not calendar/business days |
| `duration_quality_status` | Priority: lifecycle_unobserved, missing_created, unusable_created, missing_closed, unusable_closed, closed_before_created, valid |

Durations use UTC instants derived under the existing **America/New_York** source
interpretation. Spring/fall transitions therefore use actual elapsed time. Invalid,
missing, ambiguous-fold and nonexistent-gap endpoints remain unresolved, never
imputed or guessed. Reversed pairs retain signed hours and their original chronology
flag; valid-duration columns are NULL rather than clamped to zero. Exactly equal
endpoints produce valid zero. Subsecond precision survives the calculation.
The summary quality field prioritizes creation issues; original per-field flags
preserve all simultaneous issues. A bad/missing due date can set the inherited
lifecycle quality flag while a creation/closure duration remains valid.

These are **reported creation-to-closure durations**, independent of reported status.
A populated closure date can coexist with an Open status; it still yields a date-based
measure. A missing closure is not evidence that a request is open. The measure does
not validate actual resolution, calculate response time, reconstruct closure/reopen
cycles, sum time in status, or implement an SLA. Due dates, resolution-action dates,
warehouse load clocks and capture times are not substituted for closure/creation.
Validity tests only endpoint usability/order; no source truth or freshness is implied.

View preflight checks all loaded lifecycle versions for unique keys/captures,
canonical hashes, required metadata, consistent Hub identities/namespace/mode and
stored timestamp/quality interpretation. It reuses the staging parsing/DST policy
against stored raw strings without reading pending landing evidence. Corruption
fails before replacing the view. Original Raw Vault conflict/retraction/full-refresh
safeguards remain unchanged; dependencies commit independently. Selecting only the
metric view uses already loaded Raw Vault data. Use normal builds for dependencies.

```sql
set TimeZone = 'UTC';
select request_bk, record_source, snapshot_at_utc, created_date, closed_date,
       duration_quality_status, reported_closure_elapsed_hours, resolution_hours
from business_vault.bv_request_resolution_metrics
where record_source = 'synthetic_311'
order by request_bk;
```

Verified offline: 34 new integration cases cover Python duration/provenance
reconstruction, zero/subsecond/negative durations, spring/fall elapsed time,
missing/blank/invalid/fold/gap endpoints, due-only quality, closure clearing,
corrections, namespaces, intraday latest ordering, replay, pending/late captures,
Hub-only/empty states, corrupt-input preflight and existing conflict/retraction
protection. Ruff passed. The copied-database dependency build passed 80 data tests;
all 216 project dbt data tests passed. The current retained sample yields 518 rows:
498 valid durations and 20 missing closures. All prior base tables and replay results
were unchanged. Counts are evidence-specific, not permanent thresholds or citywide
statistics. This completes the required M4 models/temporal checks for retained
evidence. M5 marts are next; M1 tooling and M6 CI/release tasks remain separately tracked.

## Bounded observed status PIT (2026-10-24)

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +pit_request_status_asof
```

`business_vault.pit_request_status_asof` is a view at `(request_hk, as_of_utc)`
grain. Every loaded request Hub identity crosses the chosen calendar, including
identities not yet observed at that instant. The start-inclusive/end-exclusive
interval join selects one applicable status version, preserving exact nullable
`status`, `status_hashdiff`, `status_batch_id`, `status_snapshot_at_utc` and original
`status_load_dts_utc`. `record_source` and `mode` come from the Hub even for unknown
rows. The version reference is `(request_hk, status_snapshot_at_utc)`; load time is
provenance, not the ordering key. No PIT refresh/load clock is assigned.

`is_status_observed=false` means no loaded version exists at/before the instant;
all five status/version fields are NULL. An observed NULL status has the flag true
and non-NULL version pointers. No source creation/closure date backdates status.
An open-ended last state makes no freshness claim. Filter source/mode explicitly
when reporting; the global calendar intentionally includes unknown rows across
source populations. Hub-only identities also remain unknown.

By default the calendar contains **distinct loaded status-version capture instants**,
across namespaces. It is not a daily calendar and does not include every unchanged
landing capture. Empty Raw status history yields an empty default calendar. It
never extends to the current clock automatically. To choose a smaller reporting
calendar or inspect before-first/intraday times, supply a controlled list:

```bash
dbt build --profiles-dir . --select +pit_request_status_asof \
  --vars '{pit_status_as_of_instants: ["2026-01-31T00:00:00Z", "2026-02-02T08:00:00Z", "2026-02-03T08:00:00Z"]}'
```

Each input must be an ISO timestamp with seconds and an explicit `Z` or numeric
offset (up to six fractional-second digits). Naive/date-only/invalid inputs fail;
SQL literals are emitted only after parsing/normalization. Equivalent instants are
deduplicated in UTC. `[]` explicitly creates an empty PIT; omitted/null uses the
default. Maximum input/default calendar: **366 instants**; maximum entire Hub ×
calendar grid: **1,000,000 rows**. Oversized builds fail before replacing the view,
and query-time checks also reject growth beyond these bounds. Select fewer explicit
instants when the default exceeds the limits; no rows are silently truncated.
Calendar configuration is embedded in the view: rebuild it to change configuration.

All three time columns are TIMESTAMPTZ. Offset-equivalent instants and DST crossings
use absolute instants, independently of DuckDB's display timezone. The view reflects
accepted late Raw Vault versions without rebuilding and may revise prior as-of
answers; default calendar membership can grow with Raw captures. Pending landing
rows have no effect until loaded. This is **current-evidence observed-time**
reconstruction, not historical warehouse-knowledge or authoritative NYC event history.
The two timestamps do not implement bitemporal knowledge filtering.

Preflight reuses status structural/hash/parent safeguards and additionally requires
unique canonical Hub identities/provenance, including Hubs with no status. Upstream
Raw Vault guards remain unchanged. Each dbt model commits independently. A normal
`+` build loads dependencies; selecting only the PIT uses already loaded data.
Derived views are rebuildable; Raw Vault full-refresh protections still apply.

Inspect the synthetic request's accumulated observations:

```sql
set TimeZone = 'UTC';
select h.request_bk, p.as_of_utc, p.is_status_observed, p.status,
       p.status_snapshot_at_utc, p.status_batch_id, p.status_load_dts_utc
from business_vault.pit_request_status_asof p
join raw_vault.hub_request h using (request_hk)
where p.record_source = 'synthetic_311' and h.request_bk = 'DEMO-001'
order by p.as_of_utc;
```

Verified offline: 22 PIT integration cases cover independent version selection,
unknown/NULL/blank/Unicode, reversals, exact/microsecond/intraday/DST boundaries,
namespaces, safe late arrivals, landing-only isolation, replay/load provenance,
empty/explicit/default/duplicate-offset/future calendars, invalid inputs, both
size limits (including query-time growth), corrupt inputs, Hub-only identities,
and the 366-instant boundary. Ruff passed. The copied-database build passed all
203 project dbt data tests, including an independent latest-capture comparison.
The default calendar currently has four instants and produces 2,072 rows: 527
observed and 1,545 unknown. All prior base tables and replay results were unchanged.
Counts describe retained evidence, not permanent thresholds. Lifecycle duration
metrics are next; M4 remains incomplete. No new extraction or mart is added.

## Observed status intervals — first M4 slice

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +bv_request_status_intervals
```

`business_vault.bv_request_status_intervals` is a derived view with one row per
Raw Vault status version, at `(request_hk, observed_from)` grain. `observed_from`
is exactly `sat_request_status.snapshot_at_utc`; `next_observed_from` is the next
retained capture for that source-scoped request, calculated with `LEAD`.

Use the half-open predicate:

```sql
observed_from <= as_of_time
and (next_observed_from is null or as_of_time < next_observed_from)
```

The new version applies at an exact boundary. Before the first observed capture,
there is no matching interval: status is unknown. The final interval has NULL end,
meaning last known reported state; it makes no claim about source freshness or
continuous City-side status between captures. An observed NULL status has a row
with NULL status and differs from no observation before first capture. NULL/blank,
case, Unicode and reversals are retained without classification or carry-forward
over an explicit missing value. Request creation/closure/resolution dates never
backdate these intervals. A single live capture cannot reconstruct earlier backlog.

The view preserves exact `status`, `hashdiff`, `batch_id`, `record_source`, `mode`,
and original Satellite `load_dts_utc`. It assigns no new warehouse timestamp and
changes no keys or Raw Vault rows. Boundaries and load times remain TIMESTAMPTZ;
set `TimeZone='UTC'` to inspect UTC clock values. Select source/mode explicitly when
reporting scoped synthetic/live populations.

Endpoints reflect all currently loaded Raw Vault evidence. Accepted late versions
can shorten/split a derived interval or establish an earlier first observation;
original Raw Vault versions and warehouse metadata remain fixed. The view updates
when the Satellite changes, without a separate view rebuild. Pending landing rows
have no effect until Raw Vault loading succeeds. This is observed-time as-of
reconstruction with current evidence; it is not a historical warehouse-knowledge
as-of view. Upstream conflicting captures and late evidence requiring Raw Vault
retractions remain blocked by existing guards.

The normal dependency build loads upstream models first; each dbt model commits
independently. Selecting only the interval view uses already loaded Raw Vault data.
Its preflight rejects duplicate request/capture versions, missing key/time/provenance,
invalid status hashes and missing/inconsistent request Hub parents. No version is
silently dropped. Tests require exact source coverage/provenance, positive bounded
intervals, no overlap/gaps between consecutive versions, and one unbounded latest
interval per request. Raw Vault full-refresh protections remain in force; use the
normal build command above.

Inspect the deterministic Open → Closed → Open history in UTC:

```sql
set TimeZone = 'UTC';
select h.request_bk, i.status, i.observed_from, i.next_observed_from
from business_vault.bv_request_status_intervals i
join raw_vault.hub_request h using (request_hk)
where i.record_source = 'synthetic_311' and h.request_bk = 'DEMO-001'
order by i.observed_from;
```

The fixture produces `[Feb 1 08:00, Feb 2 08:00)` Open,
`[Feb 2 08:00, Feb 3 08:00)` Closed, then Open with a NULL end. These are observed
boundaries, not authoritative source transition times.

Verification on 2026-10-24: 17 new offline integration tests cover exact/intraday
as-of boundaries, before-first capture, open-ended latest state, reversals, NULL/empty
statuses, namespaces, UTC/DST/session independence, independent endpoints/provenance,
late captures changing derived ends, dynamic view updates, replay, malformed Raw
Vault input, upstream conflict/retraction guards and an empty fresh build.
All 215 Python tests and Ruff passed. The copied-database dependency build passed
68 dbt data tests; the full build passed 195. Independent Python reconstruction
matched 520 intervals and 518 latest unbounded rows; every prior base table and
replay row stayed unchanged. Counts describe current retained evidence, not thresholds.

Only the first M4 slice is complete. Bounded `pit_request_status_asof`, lifecycle
metrics, marts, scheduling and additional API extraction remain later work.

## Agency-name Satellite

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +sat_agency_details
```

`raw_vault.sat_agency_details` records adjacent changes to exact nullable
`agency_name`, with hashdiff domain `agency_details` and unchanged SHA-256 v1
encoding. Its parent is the source-scoped agency Hub; names never define agency
identity. Grain is `(agency_hk, snapshot_at_utc)`. Rows store the name, hashdiff,
batch/source/mode, `evidence_request_bk`, and first warehouse `load_dts_utc`.
Capture/load timestamps are TIMESTAMPTZ instants. The supporting request key plus
batch identifies original landing evidence; it is not an agency identity component
and does not require a request Hub parent.

Agency names arrive on request rows. Every eligible observation for one agency at
one capture instant must agree, across requests and batches. Conflicting names
fail before Satellite insertion, including populated vs NULL, empty vs blank,
case/whitespace and Unicode differences. Agreeing ties select batch ID then exact
request key on first load. Replay retains original evidence and warehouse timestamps
even when a later-arriving identical observation sorts earlier. No majority vote,
normalization or preference for populated names is applied.

`staging.int_agency_details_observations` retains every successful source row with
`has_missing_agency`, `has_missing_agency_name` and `is_agency_details_eligible`.
Missing/blank agency keys remain flagged and contribute no Satellite row. A valid
agency with a missing category remains eligible. NULL/blank agency names are
eligible observed values, not placeholders; clearing a name or returning to an
older name creates a version. Missing names are not carried forward. An absent
agency/request contributes no observation. These request-feed labels establish
observed names, not authoritative legal names or City-side rename timestamps.
State before the first captured agency observation remains unknown.

The change view coalesces agreeing ties before adjacent comparison. Append-only
loading requires agency Hub coverage and matching business key/source/mode for all
eligible observations, blocks full refresh, and fails schema changes. Safe additive
late boundaries append; evidence requiring existing history to be changed/retracted
fails. Retain landing evidence and reconstruct/reconcile such history separately.
Each dbt model commits independently; a dependency Hub can commit before the
Satellite fails. Other request attributes do not participate in the name hash,
and all earlier Vault models and hashes remain unchanged.

Inspect synthetic agency history:

```sql
select h.agency_bk, s.snapshot_at_utc, s.agency_name,
       s.batch_id, s.evidence_request_bk
from raw_vault.sat_agency_details s
join raw_vault.hub_agency h using (agency_hk)
where h.record_source = 'synthetic_311'
order by h.agency_bk, s.snapshot_at_utc;
```

Verification on 2026-10-22: 18 new offline integration tests cover cross-request/
cross-batch coalescing and conflicts, deterministic evidence, exact names and source
namespaces, missing-key/name policy, renames/clearing/reversals, intraday/as-of
boundaries, replay, safe/unsafe late evidence, parent/schema/refresh guards,
failed/running exclusion, rollback after an injected post-insert error, and a fresh
agency-only build. All 198 Python tests and Ruff passed. The copied-database
dependency build passed 67 dbt data tests; the full build passed 185. Independent
Python reconstruction matched four agency-name versions, hashes and evidence rows:
two live and two synthetic. All prior base tables and replay metadata stayed
unchanged. Counts describe current retained evidence, not permanent thresholds.

This completes the required M3 Raw Vault models and checks. M4 begins with
`bv_request_status_intervals`, followed by bounded snapshot-as-of PIT. No Business
Vault intervals, PIT, metrics, scheduling or additional API extraction is implemented
by this slice; broader foundation/CI milestones remain tracked separately.

## Request location Satellite

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +sat_request_location
```

`raw_vault.sat_request_location` records adjacent changes to the ordered exact
`[borough, incident_zip, community_board]` tuple. Hashdiff domain is
`request_location` using the existing SHA-256 v1 encoding. All three fields remain
nullable VARCHAR: leading-zero ZIPs/board codes, NULL, empty/blank, case, whitespace,
delimiters, Unicode, and source values such as `Unspecified` remain distinct.
No trimming, numeric ZIP conversion, borough inference or source-value correction
is applied. Missing values in a captured row are recorded explicitly; fields are
not carried forward. A request absent from a capture contributes no new row.

Grain is `(request_hk, snapshot_at_utc)`. Each row stores the three source fields,
`hashdiff`, `batch_id`, `record_source`, `mode`, and first warehouse `load_dts_utc`.
Capture/load timestamps are TIMESTAMPTZ instants; set `TimeZone='UTC'` to inspect
UTC clock values. The source-scoped request hash is unchanged. Street addresses,
coordinates and additional geography are outside this contract.

The observation/change views retain successful captures and compare adjacent raw
tuples. Unchanged captures add nothing; returns to an earlier tuple produce new
versions. These are feed observations at capture time, not evidence of a physical
move or authoritative location at the earlier creation/closure event. State before
the first captured observation is unknown. Status, agency, lifecycle and resolution
changes alone do not create location versions.

Identical same-time tuples coalesce by batch ID on first load; conflicting tuples
fail before insertion. Parent Hubs must have matching source/mode. Append-only
incremental loading blocks full refresh and fails schema changes. Replay preserves
original provenance and warehouse timestamps even after an earlier-sorting identical
tie arrives. Safe additive late boundaries append; evidence requiring existing rows
or boundaries to change fails. Retain landing evidence and reconstruct/reconcile
such history in a separate database. Each dbt model commits independently, including
dependency Hubs. All earlier Vault model/hash definitions remain unchanged.

Inspect the synthetic observations:

```sql
select h.request_bk, s.snapshot_at_utc, s.borough, s.incident_zip,
       s.community_board, s.batch_id
from raw_vault.sat_request_location s
join raw_vault.hub_request h using (request_hk)
where h.record_source = 'synthetic_311'
order by h.request_bk, s.snapshot_at_utc;
```

The fixed landing fixtures keep location unchanged, yielding one version per
fictional request. Separate integration fixtures exercise corrections, clearing,
reversals and returns without changing those immutable landing batches.

Verification on 2026-10-21: 17 new offline integration tests cover all raw hash
inputs, exact text/ZIP storage, namespaces, adjacent history, missing/disappearing
observations, intraday/as-of boundaries, replay/ties, safe/unsafe late arrivals,
parent/schema/full-refresh guards, failed/running exclusion and injected post-insert
rollback. All 180 Python tests and Ruff passed. The copied-database dependency
build passed 55 dbt data tests; the full build passed 174. Independent Python
source-history/hash reconstruction matched 518 location versions. All prior base
tables and replay metadata stayed unchanged. Counts describe current evidence,
not permanent thresholds. Agency details remain the next M3 slice; Business Vault,
PIT and analytical marts remain pending.

## Request resolution Satellite

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +sat_request_resolution
```

`raw_vault.sat_request_resolution` stores adjacent changes to the ordered raw
`[resolution_description, resolution_action_updated_date]` tuple. SHA-256 v1 uses
hashdiff domain `request_resolution`; existing request/other hashes are unchanged.
Exact text is retained, including NULL, blank, case, whitespace, line breaks,
delimiters and Unicode distinctions. Returning to an older tuple creates a new
version, while unchanged captures and identical replay create none. All-null tuples
remain explicit missing observations. The existing three synthetic demo requests
have no resolution attributes, so each initially contributes one all-null version;
the integration fixtures exercise resolution changes without altering those batches.

Grain is `(request_hk, snapshot_at_utc)`. Rows retain the two raw attributes,
staging's `resolution_action_updated_at_nyc` / `_utc`, per-field time-status and
parse-failure flags, `source_timezone`, `has_resolution_date_quality_issue`,
`hashdiff`, `batch_id`, `record_source`, `mode`, and first `load_dts_utc`.
NYC local timestamps remain naive; UTC, capture and warehouse timestamps are
TIMESTAMPTZ instants. Set `TimeZone='UTC'` to inspect UTC clock values. Missing and
malformed dates remain inspectable; DST gaps/folds retain local values with NULL UTC.
The quality flag covers only malformed/DST-uncertain resolution-update dates;
missing dates are identified by their time status and do not set this flag.

Capture time orders versions. A description can change with an unchanged or
backward-moving source update date; both cases are retained. The source date is
neither a complete CDC cursor nor a guaranteed status-change/assignment timestamp.
It never backdates an observed boundary. No resolution state is inferred before
first capture, and no closure status, SLA, duration or analytical metric is calculated.
Changes to status, lifecycle dates, agency or location alone do not create versions.

The staging observation/change views read successful batches only, retain capture
provenance, and coalesce identical capture ties deterministically by batch ID.
Conflicting raw tuples at one request/capture instant fail before Satellite insertion.
Hub parents must exist with matching source/mode. Append-only incremental loading
blocks full refresh and fails schema changes. Replay preserves original provenance
and warehouse timestamps even after an earlier-sorting identical tie arrives.
Safe additive late boundaries append; evidence or parsing-policy changes requiring
existing rows/boundaries to change fail. Retain landing evidence and reconstruct
such history in a separate database. Each dbt model commits independently, so a
successful dependency Hub load can remain after a later Satellite failure.

Inspect resolution dates and capture provenance:

```sql
select h.request_bk, s.snapshot_at_utc, s.resolution_action_updated_date,
       s.resolution_action_updated_date_time_status,
       s.has_resolution_date_quality_issue, s.batch_id
from raw_vault.sat_request_resolution s
join raw_vault.hub_request h using (request_hk)
where h.record_source = 'synthetic_311'
order by h.request_bk, s.snapshot_at_utc;
```

Verification on 2026-10-21: 21 new offline integration tests cover ordered raw
hashing, adjacent changes/reversals, date/description clearing, NULL/blank/Unicode
and newline preservation, source time moving backward, DST/invalid dates, namespaces,
intraday captures, immutable replay, safe/unsafe late arrivals, conflicting ties,
parent/schema/full-refresh/interpretation guards, failed/running exclusion, and
rollback after an injected post-insert error. All 163 Python tests and Ruff passed.
The copied-database dependency build passed 60 dbt data tests and the full build
passed 164. Independent Python source-history and SHA-256 reconstruction matched
518 resolution versions; all prior base tables and replay metadata stayed unchanged.
Counts describe current retained evidence, not permanent thresholds. Location and
agency-detail Satellites, Business Vault and PIT remain pending.

## Request lifecycle Satellite

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +sat_request_lifecycle
```

`raw_vault.sat_request_lifecycle` stores adjacent observed changes to the ordered
raw tuple `[created_date, closed_date, due_date]`. Hashdiff domain is
`request_lifecycle`, using the unchanged SHA-256 v1 exact-text encoding. NULL,
empty/blank, malformed, Unicode and formatting differences remain distinct even
when two strings parse to the same instant. Clearing a closure date records a new
version; equal values returning after a different tuple also produce a new version.
Other attributes (status, agency, resolution text/dates, location) do not participate.
All-null lifecycle tuples are preserved as observed missing values.

Grain is `(request_hk, snapshot_at_utc)`. Each row stores the three original nullable
strings, their `_at_nyc` / `_at_utc` columns, per-field `*_time_status` and
`*_parse_failed`, `source_timezone`, `is_lifecycle_evaluable`, `closed_before_created`,
`has_lifecycle_date_quality_issue`, hashdiff, batch/source/mode and first
`load_dts_utc`. Local timestamps are naive; UTC and pipeline timestamps are
TIMESTAMPTZ instants. Set `TimeZone='UTC'` when inspecting UTC clock values.
Parsing and DST policy come unchanged from staging; gaps/folds keep NULL UTC,
negative chronology remains flagged, and malformed/missing observations are retained.
The lifecycle quality flag covers only these three dates, so an unrelated invalid
resolution-update date does not change this Satellite. Missing values are identified
by their per-field time status; missing dates alone do not set this quality flag.
`due_date` is an expected agency update date. No SLA or duration metric is calculated.

The two staging views `int_request_lifecycle_observations` and
`int_request_lifecycle_changes` retain capture provenance, calculate raw hashes and
coalesce identical ties by batch ID before adjacent change detection. Only successful
batches contribute. Conflicting raw date tuples at the same request/capture instant
fail before Satellite insertion. Required Hub parents must have matching source/mode.
No lifecycle state is inferred before the first retained capture; raw event dates do
not backdate the observed version.

The model appends unseen boundaries, blocks full refresh, and fails schema changes.
Replay preserves original batch provenance and warehouse timestamps, including when
an earlier-sorting identical tie arrives. Safe late boundaries append; any late
observation or parsing-policy change requiring an existing row/boundary to change
fails. Retain original landing evidence and reconstruct/reconcile such history in a
separate database. Each dbt model commits independently: dependency Hubs can commit
before a later Satellite fails. Existing request/status/assignment models and hashes
are unchanged.

Inspect closure-date clearing in the fictional history:

```sql
select h.request_bk, s.snapshot_at_utc, s.created_date, s.closed_date,
       s.due_date, s.closed_date_time_status, s.batch_id
from raw_vault.sat_request_lifecycle s
join raw_vault.hub_request h using (request_hk)
where h.record_source = 'synthetic_311' and h.request_bk = 'DEMO-001'
order by s.snapshot_at_utc;
```

Verification on 2026-10-19: 18 new network-free integration tests cover raw hashing,
creation/due corrections, closure clearing, NULL/blank distinctions, Unicode,
format-only changes, namespaces, DST/invalid dates, intraday capture, replay, safe
and unsafe late arrivals, ties, parent/schema/full-refresh guards, failed/running
exclusion and injected post-insert rollback. All 142 Python tests and Ruff passed.
The copied-database dependency build passed 67 dbt data tests; a full build passed
all 149 dbt data tests. An independent Python
source-history reconstruction matched 520 lifecycle versions; all prior base tables
and replay rows/timestamps stayed unchanged. Counts describe current evidence,
not permanent thresholds. Resolution, location and agency-detail Satellites and
Business Vault/PIT remain pending.

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
