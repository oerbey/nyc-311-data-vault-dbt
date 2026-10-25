# Reporting contracts

Resolution-time and request-event trend slices are implemented. Agency performance,
historical status and dashboard remain separate slices. Canonical context: [PROJECT_PLAN.md](PROJECT_PLAN.md).

## Build and reporting grain

From `nyc_311_requests` after landing ingestion:

```bash
dbt build --profiles-dir . --select +mart_resolution_times
```

`marts.mart_resolution_times` is a view at **one row per source-scoped request
identity (`request_hk`)**. It publishes every row of
`business_vault.bv_request_resolution_metrics`, retaining its exact raw/typed dates,
nullable quality fields, valid durations, signed diagnostics, business key, source,
mode, selected version capture/batch/hash and original first warehouse load time.
It adds NYC event dates, creation cohort month and additive population counters.
No new hashes or report-refresh timestamp are assigned.

Each request uses its own **latest loaded lifecycle capture**. Rows can therefore
have different capture times; this is not a synchronized source snapshot or a
historical lifecycle as-of report. Safe late earlier captures do not override
newer versions. Loaded corrections/closure clearing can restate current event dates,
cohort month and duration; Raw Vault retains prior versions. Pending landing rows
cannot change the view. Replay preserves values and original provenance. Status
history remains in the bounded status PIT; a status version is not a lifecycle pointer.

## Population and time rules

The reporting population is all retained request Hub identities. It includes Hubs
with no loaded lifecycle version, observed missing values, malformed dates, DST
uncertainty and reversed endpoint pairs. `is_lifecycle_observed` distinguishes
unloaded lifecycle from an observed all-NULL tuple. Duration filtering must not
silently remove these requests from population/exclusion reporting.

Always select `record_source` and `mode` deliberately. Synthetic and live identities
with equal source request keys remain separate. The live sample is a bounded
Jan 1, 2026 DEP/DSNY extraction, captured later; the full six-month cohort has not
been extracted. Counts reflect retained development evidence, not citywide coverage,
continuous source freshness, all closure cycles or complete historical status.
Selected lifecycle batch provenance can be joined to `landing.ingestion_batch`
to inspect `requested_filter`; overlapping batches must not be summed as requests.

| Reporting field | Definition |
|---|---|
| `created_event_date_nyc` | Local NYC creation calendar date when its stored time status is valid; otherwise NULL |
| `closed_event_date_nyc` | Local NYC closure calendar date when its stored time status is valid; otherwise NULL |
| `created_cohort_month_nyc` | First day of usable creation event date's month; otherwise NULL |
| `request_count` | Always 1, including unknown/missing/invalid lifecycle rows |
| `duration_eligible_count` | 1 when `is_duration_valid`; otherwise 0 |
| `duration_ineligible_count` | 1 minus duration_eligible_count |
| `created_event_eligible_count` | 1 when creation event date is usable; otherwise 0 |
| `closed_event_eligible_count` | 1 when closure event date is usable; otherwise 0 |

Event dates come from stored naive NYC local timestamps, not the date of UTC
instants or the connection's display timezone. Invalid/ambiguous/nonexistent times
have NULL reporting dates under the conservative usable-instant policy, even if a
raw calendar date could be guessed. Original strings/typed fields and quality flags
remain inspectable. Month membership follows NYC creation dates.

UTC elapsed duration keeps M4's contract: `resolution_hours`/`resolution_days` are
populated only when creation and closure are usable and nonnegative in order.
Zero is eligible. Negative signed hours remain in `reported_closure_elapsed_hours`
while valid-duration measures are NULL. Missing/invalid endpoints are not imputed.
Days mean elapsed hours / 24. Due-date quality and reported status do not decide
eligibility. These are reported creation-to-closure durations, not response-time,
SLA attainment, proof of resolution or accumulated time across reopenings.

A usable closure date can coexist with an unusable creation or negative pair;
its `closed_event_eligible_count` is still 1 while duration eligibility is 0.
Event populations and duration populations must be reported separately. Source
status/agency performance classification belongs to subsequent slices.

## Denominators and aggregation

For any explicit source/cohort filter:

- Total retained requests = `sum(request_count)`.
- Duration denominator = `sum(duration_eligible_count)`.
- Excluded duration population = `sum(duration_ineligible_count)`; eligible + excluded = total.
- Mean/median/p90 use nullable `resolution_hours` and therefore exclude ineligible
  pairs while retaining valid zeroes. An empty eligible population yields NULL
  duration statistics, not zero performance.
- Creation/closure event counters describe their respective usable dates. One
  request can contribute to both on different dates. Do not call the creation
  population a closure denominator or sum replicated daily/as-of rows as requests.
- Capture and warehouse load time are lineage clocks, not event grouping dates.

Example latest-loaded report (synthetic population only):

```sql
select record_source, mode,
       sum(request_count) as retained_requests,
       sum(duration_eligible_count) as duration_denominator,
       sum(duration_ineligible_count) as excluded_durations,
       avg(resolution_hours) as mean_hours,
       median(resolution_hours) as median_hours,
       quantile_cont(resolution_hours, 0.9) as p90_hours
from marts.mart_resolution_times
where record_source = 'synthetic_311' and mode = 'synthetic'
group by record_source, mode;
```

Inspect exclusions without dropping their request rows:

```sql
select record_source, mode, duration_quality_status, sum(request_count) as requests
from marts.mart_resolution_times
group by record_source, mode, duration_quality_status
order by record_source, mode, duration_quality_status;
```

## Safeguards and verification

The mart's preflight reuses loaded lifecycle structural/hash/parent/timestamp guards
and independently compares the Business Vault output with latest retained Raw Vault
versions and Hub coverage. Duplicate/missing reporting rows, altered measures,
quality or lineage fail before the mart view is replaced. These are build-time
checks; rerun a normal build after external changes. Views follow successfully
loaded evidence dynamically; each dbt model commits independently. Raw Vault's
conflict/retraction/full-refresh guards remain unchanged.

Twenty-two offline integration cases and Ruff passed. Checks cover exact upstream
projection/provenance, one row per identity, namespaces, NYC midnight/month
boundaries, DST, NULL/blank/unloaded/invalid/negative/zero populations, due-only
quality, replay, closure clearing, corrections, safe late evidence, dynamic views,
corrupt upstream/Raw inputs and empty fresh builds. Independent Python calculations
verify every date and counter and the original lifecycle measures. A six-request
fixture with durations [0, 1, 3, 9, NULL, NULL] yields 4 eligible / 2 excluded,
13 total valid hours, mean 3.25, median 2 and p90 7.2. Its usable closure event
population is 5, demonstrating a different denominator.

Copied-database dependency and full builds passed 94 selected / 230 project dbt
data tests. The current retained sample yields 518 mart rows, 498 eligible durations
and 20 exclusions. Every prior base table and Business Vault output stayed unchanged;
mart replay/full-build rows and provenance also stayed unchanged. These counts
are observations of this sample, not permanent thresholds. M5 remains in progress;
request trends, agency performance, historical status and Streamlit follow.


# Request-event trends contract

Second M5 slice, verified on 2026-10-24. From `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_request_trends
```

## Models and grain

| View | Grain | Purpose |
|---|---|---|
| `marts.int_request_trend_population` | source-scoped request_hk | Every resolution-mart row plus exact lifecycle-capture context and missing-label flags |
| `marts.int_request_trend_events` | request_hk + event_type | At most one usable creation and one usable closure contribution, with original lineage |
| `marts.mart_request_trends` | record_source + mode + event_date_nyc + observed_agency_bk + observed_complaint_type + observed_borough | Separate BIGINT created_request_count and closed_request_count |

`dimension_basis` is always `lifecycle_capture`. Counts group exact nullable text;
NULL, empty, whitespace, case and Unicode are not normalized or replaced with
placeholder identities. Source/mode remain separate. There is no all-sources row.
A request can contribute on two different event dates or once to each count on the
same date. Adding creation and closure counts does **not** produce distinct requests.
Population size comes from the request-level population view, not event-row count.

## Context attribution and provenance

Agency, problem and borough come together from the **exact successful landing row
supporting the selected lifecycle version**, using batch ID, original request key,
source, mode and capture time. Stored created/closed/due strings must match that
evidence. This preserves a coherent observed tuple without joining every historical
Link or independently carrying forward mismatched dimensions. Retain landing evidence;
missing/inconsistent evidence fails the build instead of silently losing relationships.

Fields are deliberately named `observed_agency_bk`, `observed_complaint_type` and
`observed_borough`. They describe context at `dimension_snapshot_at_utc`, identified
by `dimension_batch_id`. They are neither authoritative assignment/location at the
earlier source event date nor necessarily the latest known context. If only agency,
category or borough changes while lifecycle dates stay unchanged, the selected
lifecycle version and these labels stay fixed. The fictional reassignment DEMO-002
therefore retains its February 1 DEP context in this mart, although separate Vault
assignment history later observes DSNY. A new lifecycle version selects its own
supporting context. Future agency-performance reporting needs its own attribution
contract; do not relabel these fields as event-time/current agency.

The population view preserves all resolution-mart dates, quality fields, valid and
signed duration measures and original capture/hash/batch/first-load provenance.
It adds `is_dimension_observed` and `has_missing_agency`, `has_missing_problem`,
`has_missing_borough` flags. A Hub without loaded lifecycle retains one population
row, NULL context, false is_dimension_observed and no events. Captured NULL labels
have is_dimension_observed=true; events still contribute under those NULL groups.
Missing grouping labels never suppress an otherwise usable endpoint.

## Event eligibility and reporting limits

Creation and closure dates follow the existing usable-instant policy in the
resolution mart: valid time status yields the NYC local calendar date; missing,
malformed, ambiguous/fold and nonexistent/gap times yield no endpoint contribution.
Raw strings and per-field flags remain inspectable in the population. A usable
closure contributes even when creation is missing or the pair is reversed; this
is separate from duration eligibility. Inspect `closed_before_created` and
`is_duration_valid` in the event view when reviewing those source anomalies.

Source-event dates can predate first capture and are not replaced by capture/load
clocks. No historical status or assignment is inferred for those dates. Event dates
and original cohort capture times are different clocks. UTC-midnight and NYC-month
boundaries follow local event dates independently of session timezone.

The mart uses latest loaded lifecycle evidence per request. Accepted source-date
corrections/closure clearing can move or remove earlier derived contributions;
prior Raw Vault observations remain unchanged. An earlier late version cannot
override a later capture. Identical replays/ties preserve original batch provenance.
Pending landing observations and assignment-only changes do not alter selected
lifecycle context. These are current-evidence source-event trends, not a ledger of
every closure/reopen cycle or a historical warehouse-knowledge view.

The retained population remains a scoped development sample. Creation scope does
not restrict reported closure dates to the creation window; choose any event-date
reporting window explicitly. Missing dates/group rows are not evidence of zero
citywide activity: the view has no date spine or inferred zero-coverage days.
Creation minus closure is not an observed backlog; closure/creation counts over a
date range are not a resolution rate with a shared cohort denominator. Historical
status/backlog and agency performance remain separate M5 slices.

## Queries and reconciliation

Example scoped trend, aggregating across observed context only when intentional:

```sql
select event_date_nyc, sum(created_request_count) as created_requests,
       sum(closed_request_count) as closed_requests
from marts.mart_request_trends
where record_source = 'synthetic_311' and mode = 'synthetic'
group by event_date_nyc
order by event_date_nyc;
```

For an identical source/mode/population filter, total created_request_count must
match sum(created_event_eligible_count) in int_request_trend_population; closure
counts reconcile independently to closed_event_eligible_count. The event view
provides request-level audit lineage for every counted contribution. Exclusions
remain visible even when neither endpoint has a usable date:

```sql
select record_source, mode, created_date_time_status, closed_date_time_status,
       sum(request_count) as retained_requests,
       sum(created_event_eligible_count) as usable_creation_events,
       sum(closed_event_eligible_count) as usable_closure_events
from marts.int_request_trend_population
group by all;
```

## Verification and safeguards

Preflight reuses loaded lifecycle canonical/typed guards, verifies BV and resolution
mart coverage/measures/dates/counters, and requires exactly one successful matching
landing observation for every selected loaded lifecycle version. Data tests check population,
event grain/lineage, exact context projection, aggregate grain/counts and endpoint
coverage. Guards run during builds; rerun after external/manual changes. Each dbt
model commits independently; original Raw Vault conflict/retraction/full-refresh
protections remain unchanged.

Twenty-two new offline integration cases and Ruff passed, including independent
Python reconstruction of groups/events/provenance, cross-date and same-day counts,
NYC midnight, DST, endpoint exclusions, negative pairs, missing context, exact text,
source separation, reassignment-only stability, corrections, clearing, safe late
captures, identical tie/replay, dynamic views, corrupt evidence/mart rejection,
Hub-only and empty builds. Copied-database builds passed 120 selected / 256 project
dbt data tests. The current retained sample produces 267 trend groups from 518
population rows: 518 usable creation contributions and 498 usable closure
contributions (1,016 event rows). Every prior base table, Business Vault output and
resolution-mart row stayed unchanged; trends replay/full-build results stayed
unchanged. These counts describe current evidence, not permanent thresholds.
M5 is in progress; agency performance is next.

### Regression follow-up: effectivity catalog visibility

The full suite exposed an existing concurrent first-build race in the shared Raw
Vault effectivity guard. A dbt cache entry could refer to a table not yet visible
to another DuckDB transaction. The guard now checks the visible database catalog
before reading stored history, preserving all history/retraction comparisons.
Two additional deterministic checks cover absent cached agency/problem tables;
the original implementation fails the negative control and the fix passes both.
Copied-database builds after the fix still pass all 256 dbt data tests with unchanged
prior tables/reports and stable trend replay. This fixes build reliability without
changing historical semantics, keys, load timestamps or reporting contracts.
