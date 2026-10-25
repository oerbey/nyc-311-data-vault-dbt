# NYC 311 — Implementation Roadmap

**Status:** Required M3 and M4 models/temporal checks complete for retained evidence. M5 resolution-time, request-trend and agency/cohort performance marts verified; historical status/dashboard remain pending. Foundation/CI tasks remain separately tracked.

The full implementation contract is in [PROJECT_PLAN.md](PROJECT_PLAN.md). AI agents must follow [../AGENTS.md](../AGENTS.md).

## M0 — Design documentation
- [x] README with entry links and scope.
- [x] Canonical project plan and milestone acceptance criteria.
- [x] Source-to-target data dictionary.
- [x] Data Vault Hub, Link, Satellite, effectivity and PIT design.
- [x] AGENTS.md and requirements.txt.
**Acceptance:** these documents exist in repository and links resolve; no claims of pipeline readiness.

## M1 — Reproducible environment + synthetic demo
- [x] Confirm Python 3.11+ setup; verify installation in a clean macOS virtual environment.
- [x] Pin validated package versions/lockfile after compatibility smoke tests.
- [ ] Create dbt-duckdb project, profile example, .env.example, Makefile and local DuckDB database.
- [x] Build deterministic synthetic request snapshots, including a status reversion and agency reassignment.
- [x] Extend .gitignore for local DuckDB/WAL files, outputs, logs, dbt target, extracts (notebook outputs must still be cleared before commit).
**Acceptance:** a new user can start the offline synthetic project without API tokens; repeat runs are deterministic.

Environment slice verified on 2026-10-18: macOS ARM64, Python 3.13.5 in `.venv`; requirements installed; `pip check`, dbt CLI/duckdb adapter discovery, JupyterLab CLI, and in-memory DuckDB smoke test passed. Exact resolved versions are in `requirements-lock.txt`; full dbt execution and synthetic demo remain pending. The connection notebook passed schema/syntax validation, Ruff, and offline mocked DataFrame/error/retry checks. It preserves the user's broader date filter and all requested fields for an in-memory sample only, with a 1,000-row default limit and deterministic tie ordering. Live requests and live schema validation are deferred to the user's run; M2 items remain unchecked.

Exploratory SQL slice verified on 2026-10-18: `run_sql_queries.ipynb` reuses the API notebook, registers its bounded DataFrame, and executes `create_311_service_request.sql` into `data/nyc_open_data.db`. The replaceable sample table includes `snapshot_at_utc` and `record_source`; it is outside the planned historical landing/Vault pipeline. Notebook schema/syntax and an offline mocked GET-to-DuckDB run passed from both repository and notebook directories, including persistence, replacement and failed-fetch stale-data protection. No live API call or project database write was performed during verification. M1/M2 warehouse milestones remain pending.

## M2 — Ingestion and staging
- [x] Implement offline landing schema, transactional batch audit, immutable observations, and replay protection.
- [x] Declare dbt landing sources and provide an offline walkthrough with verified synthetic history.
- [x] Validate live API field names against Socrata metadata (all 15 landing fields/types confirmed).
- [x] Implement an optional `SOCRATA_APP_TOKEN`-aware client and pagination.
- [x] Define a configurable bounded cohort and a repeatable full-cohort refresh command (manual execution; scheduling remains future work).
- [x] Extend live batch audit with diagnostic progress checkpoints, duplicate rejection, count reconciliation and atomic publication.
- [ ] Implement resumable extraction/checkpoint recovery if required; failed/interrupted batches currently restart under new IDs.
- [x] Implement the successful-batch staging view with raw-value preservation, typed local/UTC timestamps and date/lifecycle quality flags.
- [x] Implement reusable canonical SHA-256 v1 text key/hashdiff macros with namespace/domain separation and independent byte-encoding tests.
- [x] Test null/blank/malformed source dates, distinct/duplicate keys, snapshot preservation, failed-batch exclusion and NYC DST boundaries.
**Acceptance:** bounded source extracts or synthetic batches are reproducible; every loaded row has provenance and a capture timestamp; no silent partial batches.

Offline landing slice verified on 2026-10-18: 27 Python checks passed against temporary databases, including replay immutability, source preservation, injected partial-insert rollback, existing-table preservation, CLI execution, and notebook execution from root/notebook directories with network connections blocked. Ruff passed for ingestion, tests and notebook cells. dbt 1.11.16 / duckdb adapter 1.10.1 recognized two landing sources and passed all nine source tests against a temporary synthetic database. The existing unused `models.nyc_311_requests.example` configuration produces a warning in the isolated dbt check. The landing contract adds audit `record_source` for provenance-aware replay. Failed/running IDs require new IDs; automatic checkpoint recovery, API page reconciliation and live schema validation remain unimplemented, so the broader M2 audit task remains unchecked.

Live landing slice verified on 2026-10-18: all 15 source fields/types matched live metadata. Count-only returned 302,762 rows for the planned January–June DEP/DSNY scope and 515 for January 1. A complete one-day extract passed multi-page retrieval and before/fetched/after reconciliation with 515 committed rows. The full six-month extraction was not run. Sixty-nine Python tests passed offline, including schema drift, retries/rate limits, malformed responses, duplicate keys, changing counts, size guards, transaction rollback, existing-data preservation, CLI dispatch and the offline walkthrough. Ruff passed. All eleven dbt source and publication/count checks passed against the verified live extraction. API validation and pagination are implemented; staging and Vault modeling remain pending. No scheduler or automatic resume is implemented.

Staging slice verified on 2026-10-18: `staging.stg_311_request_snapshots` is a view over successful landing batches, retaining source strings, leading-zero IDs/ZIPs, all modes and observation history. NYC local times are parsed independently from observation time; UTC is produced only for unambiguous valid source times. DST gaps/folds are flagged and have NULL UTC values. Negative lifecycle order is flagged without dropping rows. All 85 Python checks passed, including 16 staging integration scenarios; all 26 staging dbt tests passed against a copy of the existing landing database. Ruff passed. The project uses literal custom schema names and separate database files for independent environments. Canonical Vault hashing and Vault load timestamps are intentionally pending. Existing dbt example-model warnings remain unrelated to staging.

## M3 — Raw Vault
- [x] Implement source-scoped `hub_request`.
- [x] Implement source-scoped hub_agency and hub_problem with exact-text canonical keys.
- [x] Implement append-only lnk_request_agency and lnk_request_problem for eligible ever-observed pairs.
- [x] Implement insert-only `sat_request_status` using adjacent capture changes and replay-safe first-load timestamps.
- [x] Implement insert-only lifecycle Satellite with raw dates and typed quality provenance.
- [x] Implement insert-only resolution Satellite with exact text, raw/typed update dates and quality provenance.
- [x] Implement insert-only location Satellite with exact borough/ZIP/community-board history.
- [x] Implement insert-only agency-details Satellite with cross-request agreement and stable evidence.
- [x] Implement insert-only effectivity for changing Links.
- [x] Verify request/status replay, reversals, NULL transitions, safe late arrivals, conflicting captures and full-refresh protection.
- [x] Verify Link identity under reassignment, return, late evidence, incomplete keys and replay.
- [x] Verify activation/deactivation history, returns, independent missing-key timelines, replay and late-history safeguards.
- [x] Add request/status relationships, uniqueness, provenance, canonical hash and expected change-history tests.
- [x] Verify all required Raw Vault models, immutable replay and temporal edge cases.
**Acceptance:** no existing business keys or unmodified Satellites duplicate on replay; changed relationships and states remain historically reconstructible.

Request/status slice verified on 2026-10-18: 12 new offline integration tests independently reproduce UTF-8 SHA-256 encoding, isolate synthetic/live request identities, preserve leading zeroes and NULL/empty distinctions, retain Open → Closed → Open, keep original warehouse timestamps/provenance on replay, append safe late boundaries, and reject ties/backfills/full-refresh operations requiring historical mutation. The dependency build passes all 58 dbt data tests against a copy of the current database. The current data produces 518 request Hub identities and 520 status versions. Only the first M3 slice is complete; other Hubs/Links/Satellites, effectivity and M4 remain pending. Backfills requiring retraction need a separately validated reconstruction, not an in-place update.

Assignment Hub/Link slice verified on 2026-10-18: the observation view preserves successful rows and flags incomplete keys without fabricating unknown identities. Hubs/Links retain exact source text, namespaces and first-load metadata. New checks cover canonical hashes, identity/parent grain, eligible-source coverage, observation provenance, capture conflicts and parent namespaces. All 109 Python tests, including 12 assignment integration cases, and Ruff passed. A copied-database build passed 94 selected dbt tests and matched 4 agencies, 31 problems, and 519 Links per type; existing request/status rows remained unchanged. Project test selection is now buildable so status-only and staging-only commands work without unbuilt assignment relations. Full refresh is blocked. Effectivity and descriptive Satellites remain pending.

Assignment effectivity slice verified on 2026-10-18: both Link Satellites preserve
initial activations, explicit replacements and returns. Missing/failed/absent
observations cannot deactivate relationships. Fifteen new integration cases cover
independent eligibility, exact keys, intraday boundaries, as-of reconstruction,
source separation, immutable replay, additive late evidence, conflicting ties,
missing Link parents, unsafe historical rewrites and full-refresh rejection.
All 124 Python tests and Ruff passed. The copied-database build passed 114 dbt
data tests with 520 source-derived events per type; all six existing Vault tables
and effectivity replay metadata stayed unchanged. Descriptive Satellites remain
pending, so M3 is not complete. No API extraction or Business Vault model was added.

Lifecycle slice verified on 2026-10-19: the Satellite preserves exact raw
creation/closure/due tuples and staging-derived typed dates/quality flags.
Eighteen new offline integration cases cover adjacent changes, clearing/reversals,
all raw hash inputs, source namespaces, DST/malformed/missing dates, intraday captures,
replay/ties, late evidence, parent/schema/refresh guards, exclusion of failed/running
legacy rows and rollback after an injected post-insert error. All 142 Python tests
and Ruff passed. A copied-database dependency build passed 67 dbt data tests; the full build passed
149 dbt data tests. Independent reconstruction matched 520 source-derived lifecycle versions; all prior base tables and replay
metadata were unchanged. Descriptive Satellites remain pending; M3 is not complete.
No API extraction, duration metric or Business Vault model was added.

Resolution slice verified on 2026-10-21: exact description/update-date tuples
create adjacent observed versions, including description changes without source
date advancement, backwards dates, clearing and reversals. Raw strings and staging
NYC/UTC/date-quality semantics remain intact. Twenty-one offline integration cases
cover namespaces, encoding, preservation, capture order, replay, ties, late evidence,
parent/schema/refresh/interpretation guards, failed/running exclusion and injected
post-insert rollback. All 163 Python tests and Ruff passed. Copied-database builds
passed 60 selected and 164 full-project dbt data tests. Independent Python history
and hashes matched 518 source-derived resolution versions; all prior base tables
and replay metadata remained unchanged. Location/agency Satellites remain pending,
so M3 is not complete. No API extraction or Business Vault metric was added.

Location slice verified on 2026-10-21: exact raw borough/ZIP/community-board tuples
create adjacent observed versions with explicit missing values and leading-zero ZIP
preservation. Seventeen offline integration cases cover all raw hash inputs,
source namespaces, text/type preservation, corrections/clearing/reversals, intraday
as-of boundaries, replay/ties, safe/unsafe late evidence, parent/schema/refresh
guards, failed/running exclusion and injected post-insert rollback. All 180 Python
tests and Ruff passed. Copied-database builds passed 55 selected and 174 full-project
dbt data tests. Independent Python history/hashes matched 518 source-derived
versions; all prior base tables and replay metadata remained unchanged. Agency
details remain pending, so M3 is not complete. No API extraction or Business Vault
model was added.

Agency-details slice / M3 verified on 2026-10-22: source-scoped agency-name
versions coalesce agreeing observations across requests/batches, select deterministic
supporting evidence, preserve exact nullable names, and reject conflicting capture
ties. Missing keys stay flagged/excluded; valid agencies with missing names or
categories remain eligible. Eighteen new offline tests cover names, namespaces,
provenance, renames/clearing/reversals, intraday/as-of boundaries, replay, late evidence,
parent/schema/refresh guards, failed/running exclusion, rollback and a fresh build
requiring only the agency Hub. All 198 Python tests and Ruff passed. Copied-database
builds passed 67 selected and 185 full-project dbt data tests; independent Python
history/hash/evidence reconstruction matched four versions. All prior base tables
and replay metadata were unchanged. Required M3 models/checks are complete for
retained synthetic/bounded-live evidence. No M4/PIT/metric or API extraction was
added; M1/M6 foundation/CI tasks remain separately tracked.

## M4 — Business Vault / temporal semantics
- [x] Build bv_request_status_intervals with LEAD and non-overlapping observed validity.
- [x] Build bounded pit_request_status_asof.
- [x] Build request lifecycle duration metrics with source local-time conversion.
- [x] Verify status-interval before-first/exact-boundary/intraday/late-capture behavior and timezone independence.
- [x] Extend temporal and DST checks to bounded PIT.
- [x] Extend temporal and DST checks to lifecycle metrics.
**Acceptance:** as-of results match deterministic fixtures; do not pretend to reconstruct pre-capture status history.

Observed status intervals verified on 2026-10-24: the Business Vault view projects
all Raw Vault status versions into start-inclusive/end-exclusive intervals, preserving
raw values and first-load provenance. Last known rows have NULL ends; state before
first capture is unknown. Accepted late versions revise derived ends without Raw
Vault mutation. Seventeen offline cases cover as-of/reversal/NULL semantics,
source separation, dynamic updates, independent endpoints/provenance, UTC/DST,
replay, corrupt-input guards, upstream conflict/retraction protection and an empty
fresh build. All 215 Python tests and Ruff passed. Copied-database builds passed
68 selected and 195 full-project dbt data tests, matching 520 intervals and 518
latest unbounded rows. All prior base tables and replay data stayed unchanged.
Bounded PIT and lifecycle metrics remain pending, so M4 is not complete.


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

Lifecycle metrics / M4 verified on 2026-10-24: latest-capture source-date durations
retain signed diagnostic hours and expose valid hours/24-hour days only for usable,
nonnegative creation/closure pairs. Unknown/missing/invalid/DST-uncertain values
remain explicit; due-date quality and status do not gate durations. Thirty-four new
offline cases and Ruff passed. The copied-database build passed 80 selected / 216
full-project dbt data tests. Independent Python reconstruction matched 518 rows
(498 valid, 20 missing closure). Every prior base table and replay result remained
unchanged. Required M4 models/temporal checks are complete for retained evidence;
historical lifecycle as-of metrics, response-time/SLA analysis and marts are outside
this slice. No new extraction was performed. M1/M6 tasks remain separately tracked.

## M5 — Marts + dashboard
- [x] mart_request_trends with separate created/closed event counts and explicit lifecycle-capture context.
- [x] mart_agency_performance with explicit closure-date/duration coverage and eligible mean/median/p90.
- [x] mart_resolution_times with request-grain latest lifecycle dates, valid durations and exclusions.
- [ ] mart_historical_status.
- [ ] Streamlit dashboard for volumes, response performance and *observed* backlog.
- [x] Document resolution-time mart grain, denominators, event-date eligibility and population coverage.
- [x] Document request-trend event/population grains, context attribution, exclusions and coverage.
- [x] Document agency/cohort attribution, unknown cohorts, count partitions, coverage fractions and quantile rules.
- [ ] Extend denominator/grain/population contracts to historical status and dashboard.
**Acceptance:** plots reproduce tested synthetic aggregates and never imply filtered data are whole-city totals.

First M5 slice verified on 2026-10-24: `marts.mart_resolution_times` preserves
latest lifecycle metrics and every request/exclusion row, adding usable NYC event
dates, creation cohort month and additive population counters. Twenty-two offline
cases and Ruff passed. A scoped fixture independently verifies 4 eligible / 2
excluded durations, 13 total hours, mean 3.25, median 2 and p90 7.2. Copied-database
builds passed 94 selected / 230 project dbt data tests and matched 518 request rows
(498 eligible, 20 excluded). All prior base tables/BV outputs and replay provenance
were unchanged. [Reporting contracts](REPORTING_CONTRACTS.md) define date/denominator/
coverage limits. M5 remains in progress; request trends are the next slice.

Second M5 slice verified on 2026-10-24: request trends retain source/mode and exact
observed context from selected lifecycle evidence, with separate NYC creation/closure
contributions and request-level audit/exclusions. Twenty-two offline cases and Ruff
passed. Copied builds passed 120 selected / 256 full-project dbt tests; independent
Python reconstruction matched 267 groups, 518 creation and 498 closure contributions.
Prior base tables, BV outputs, resolution mart and replay provenance remained
unchanged. Context is lifecycle_capture, not historical/current assignment; source
event trends are not backlog or citywide totals. M5 remains in progress; agency
performance is next. See [reporting contracts](REPORTING_CONTRACTS.md).

Third M5 slice verified on 2026-10-25: agency/cohort performance retains source/mode,
exact lifecycle-capture agency and nullable NYC creation month. Explicit retained/
closure/duration counts explain coverage fractions and eligible total/mean/median/p90;
unknown cohorts/labels and ineligible requests stay counted. Twenty-two offline cases
and Ruff passed. Copied builds passed 121 selected / 275 project dbt tests; independent
Python reconstruction matched four groups, 518 requests and 498 eligible durations.
Prior base/BV/mart data and new-mart replay remained unchanged. Attribution is not
current/event-time responsibility; coverage is not Closed status or an event-period
resolution rate. M5 remains in progress; historical status is next. See
[reporting contracts](REPORTING_CONTRACTS.md).

## M6 — Public portfolio quality
- [ ] CI pipeline runs lint/pytest/dbt build in offline demo mode.
- [ ] Publish dbt docs/lineage, architecture diagram, screenshots, example SQL and known limitations.
- [ ] Test fresh clone setup and document all commands actually executed.
- [ ] Add concise, verifiable CV bullet and source attribution.
**Acceptance:** a fresh environment can reproduce the synthetic project without credentials; CI passes.

## Delivery rules
1. Ship the simplest correct vertical slice first: request Hub + status Satellite + as-of test.
2. Prioritize correctness over cloud/tool proliferation.
3. Never claim that 311 snapshots provide event-by-event historical status history.
4. Update boxes only after code is implemented and tests were run; report what actually passed.
