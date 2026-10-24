# Resolution-time reporting contract

Implemented first M5 slice, verified on 2026-10-24. Other marts and the dashboard
remain separate slices. Canonical context: [PROJECT_PLAN.md](PROJECT_PLAN.md).

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
