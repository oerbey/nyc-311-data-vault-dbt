# Reporting contracts

All four reporting marts and the Streamlit dashboard are implemented.
Automated checks pass; follow-up review of the revised layout and chart-data tables remains pending. Canonical context: [PROJECT_PLAN.md](PROJECT_PLAN.md).

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


# Agency/cohort performance contract

Third M5 slice, verified on 2026-10-25. From `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_agency_performance
```

## Grain, attribution and retained population

`marts.mart_agency_performance` is a view at **record_source + mode + exact nullable
observed_agency_bk + nullable created_cohort_month_nyc** grain. It groups one row per
request from `int_request_trend_population`, independently of problem/borough or
number of historical Links/versions. Source namespaces stay separate. NULL, empty,
whitespace, case and Unicode agency labels remain distinct; `has_missing_agency`
flags absence without changing grouping text. There are no fabricated agency members.

`dimension_basis='lifecycle_capture'` retains the trends mart's explicit attribution:
agency is the label in the successful landing row supporting the selected lifecycle
version. It is not necessarily the latest agency, the agency responsible at creation/
closure, or a causal explanation for the duration. Assignment-only changes leave
this context fixed. A new lifecycle version can restate both agency and cohort.
Every retained request belongs to one group; excluded durations are still counted.

Creation cohorts use usable **NYC local creation month**, not closure, capture,
warehouse-load or current month. Unknown/invalid/DST-uncertain creation has a NULL
cohort with `is_cohort_month_known=false`; these rows remain in denominators. A Hub
with no loaded lifecycle joins the unknown cohort/context group and is counted in
`unobserved_lifecycle_count`. Captured missing dates differ from unloaded lifecycle.
Per-request quality and original capture/hash/batch/load lineage remain inspectable
in the existing population view. The aggregate creates no new clock or hashes.

The underlying state is latest **loaded** lifecycle capture per request, not a
uniform extract/as-of snapshot. `min_selected_lifecycle_capture_at_utc` and
`max_selected_lifecycle_capture_at_utc` describe the range of those selected version
captures. They are not report times, earliest-ever evidence, coverage boundaries,
last successful refresh or proof of source freshness. Unchanged later captures do
not create new lifecycle versions. Source/mode/sample limits from the preceding
contracts apply; this is a descriptive report over retained development evidence.

## Counts, partitions and rates

| Field | Definition |
|---|---|
| `retained_request_count` | All retained request identities in the agency/cohort group |
| `lifecycle_observed_request_count` | Requests with a selected loaded lifecycle version |
| `unobserved_lifecycle_count` | Requests without loaded lifecycle |
| `usable_creation_date_count` | Requests with usable creation date |
| `usable_closure_date_count` | Requests with usable reported closure date, independently of creation/order/status |
| `missing_closure_date_count` | Observed closure time status is missing, including blank source text |
| `unusable_closure_date_count` | Observed closure has invalid format, ambiguous/fold or nonexistent/gap time |
| `duration_eligible_count` | Usable, nonnegative creation/closure pairs, including valid zeroes |
| `duration_ineligible_count` | All remaining requests |
| `closed_before_created_count` | Observed reversed pairs, retained diagnostically |

Partitions must reconcile:

- observed lifecycle + unobserved lifecycle = retained requests;
- usable closure + missing closure + unusable closure + unobserved lifecycle = retained requests;
- duration eligible + duration ineligible = retained requests.

`reported_closure_date_share = usable_closure_date_count / retained_request_count`.
This is **coverage of usable reported closure dates**, not a reported Closed-status
share, verified resolved fraction, SLA attainment or a ratio of closure-period
counts to creation-period counts. A reversed pair contributes to closure-date
coverage and the chronology flag, but not duration eligibility. Unknown creation
can coexist with a usable closure, producing closure-date share 1 and duration
coverage 0 in an unknown cohort. Missing closure is not evidence of Open status.

`duration_coverage_share = duration_eligible_count / retained_request_count` makes
selection into duration statistics visible. Both fractions lie in [0,1], with
duration coverage <= closure-date coverage. Nonempty groups with no usable closure
have closure-date share 0, while their duration statistics remain NULL. Empty
populations produce no group; no synthetic zero-sized cohorts are generated.

## Eligible duration statistics

`total_resolution_hours`, `mean_resolution_hours`, `median_resolution_hours` and
`p90_resolution_hours` use only nullable valid `resolution_hours` from M4. Zeroes
are eligible; missing/invalid/DST-uncertain/reversed pairs are excluded. Signed
negative diagnostics stay in the population. Due-date quality and reported status
do not gate this source-date measure. Durations are elapsed UTC hours under the
existing America/New_York interpretation, not naive local-time subtraction.

Median and p90 use continuous linear interpolation: sort n eligible values and
interpolate at position `(n-1)*p` between adjacent values, for p=0.5/0.9. A singleton
has that value for both percentiles. With no eligible durations, **all four duration
statistics, including total, are NULL**, rather than a zero-performance result.
The denominator is exposed as duration_eligible_count. These measure reported
creation-to-closure elapsed time, not response time, work effort or closure cycles.

Example scoped cohort report:

```sql
select observed_agency_bk, created_cohort_month_nyc, retained_request_count,
       usable_closure_date_count, reported_closure_date_share,
       duration_eligible_count, duration_ineligible_count, duration_coverage_share,
       mean_resolution_hours, median_resolution_hours, p90_resolution_hours
from marts.mart_agency_performance
where record_source = 'synthetic_311' and mode = 'synthetic'
order by observed_agency_bk, created_cohort_month_nyc;
```

When pooling groups, recompute fractions from summed numerators/denominators and
mean from summed total hours / summed eligible counts. Do not average group shares
or medians/p90s. Recompute pooled quantiles from the request-level population.
Agencies/cohorts can differ in captured context, case mix, age, source filtering and
capture timing; comparisons do not establish causal agency efficiency rankings.

## Verification and rollout

Preflight reuses existing loaded lifecycle, BV/resolution and landing-evidence
checks. It independently verifies complete unique population coverage, exact context,
cohort, measures, counters and original lineage before aggregating. Duplicate/missing
rows or altered population data fail before replacing the view. Existing Raw Vault
hash, replay, full-refresh and late-history safeguards remain unchanged. Each dbt
model commits independently. Builds check external/manual changes; views reflect
accepted loaded evidence dynamically. Pending landing data and assignment-only
changes do not override the selected lifecycle context. Safe earlier captures do
not override newer ones; corrections/clearing can restate current aggregates.

Twenty-two offline integration cases and Ruff passed. Independent Python grouping,
math.fsum means/totals and interpolated quantiles check every count, fraction,
statistic and selected-capture bound. Cases cover namespaces, exact/missing labels,
unknown cohorts/lifecycle, zero/negative/invalid dates, due-only quality, NYC midnight,
DST, case-mix aggregation, corrections/cohort movement, assignment-only stability,
late evidence, replay/ties, dynamic views, input drift rejection and empty builds.
The six-request fixture [0,1,3,9,NULL,negative] produces 5/6 closure-date coverage,
4/6 duration coverage, 13 total eligible hours, mean 3.25, median 2 and p90 7.2.
An ineligible-only group has NULL statistics; a zero-only group has eligible zeroes.

Copied-database builds passed 121 selected / 275 project dbt tests. Independent
reconstruction matched four agency/cohort groups covering 518 retained requests,
498 usable closure dates and 498 eligible durations. All prior base tables and
Business Vault/mart outputs stayed unchanged, with stable new-mart replay/full-build
results. Counts describe this retained sample, not permanent thresholds. M5 remains
in progress; historical status is the next slice, followed by the dashboard.


## Historical observed status

Fourth M5 slice, verified on 2026-10-25. From `nyc_311_requests`:

```bash
dbt build --profiles-dir . --select +mart_historical_status
# Example explicitly bounded calendar (UTC; equivalent offset instants deduplicate):
dbt build --profiles-dir . --select +mart_historical_status \
  --vars '{pit_status_as_of_instants: ["2026-02-01T08:00:00Z", "2026-02-02T08:00:00Z", "2026-02-03T08:00:00Z"]}'
```

### Grain and population

`int_request_historical_status_population` is a view at request_hk + as_of_utc.
It includes every retained source-scoped Hub identity at every PIT instant, including
identities first observed later and Hub-only identities. This eventual retained
population is not the number of requests known to the pipeline or created at an
earlier instant. Unknown coverage must stay visible in historical reporting.

`mart_historical_status` is a view at UTC as_of_utc + record_source + mode +
is_agency_observed + agency_hk/agency_bk + is_status_observed + exact nullable status.
Derived status_class and dimension_basis=observed_asof are consistent with this
grain. One identity enters one group per instant. Unknown agency is a NULL group
with false observation flag, not a fabricated Hub member. Exact case, whitespace,
Unicode and source namespace survive; do not merge source/mode populations implicitly.

### Independent clocks and agency attribution

Status is the last loaded captured status version <= as_of_utc, using half-open
intervals. Before first status capture it is unknown. Captured NULL status remains
observed and is not carried over from an older non-NULL value. A loaded Open → Closed
→ Open reversal is retained. Source created/closed dates never backdate status.

Agency is the last loaded eligible agency activation <= as_of_utc, also half-open.
An explicit replacement ends the previous agency at the replacement capture; a
return restores the original identity. Missing/blank agency, missing categories,
failed/partial captures and disappearance never end the last known assignment.
Agency is unknown before its first activation. Agency and status captures can differ,
including known agency with unknown status and observed status with unknown agency.
Agency attribution differs intentionally from the earlier marts' lifecycle_capture
context. It is last *observed* assignment, not responsibility at a source event date.

The population preserves all PIT status pointers and adds request_bk, agency_link_hk,
agency_hk/BK, is_agency_observed, agency_observed_from, agency_batch_id,
agency_effectivity_hashdiff and agency_load_dts_utc. The latter is the activation
row's original warehouse load time. No new warehouse/report clock is added.
Report min/max selected capture fields describe member evidence, not simultaneous
extraction, complete coverage or continuous source freshness.

### Exact status mapping

| Condition | status_class |
|---|---|
| No captured status version | unobserved |
| Captured NULL | observed_null |
| Captured empty or ASCII space/tab/newline/carriage-return/vertical-tab/form-feed only | observed_blank |
| Exact `Open` | open |
| Exact `In Progress` | in_progress |
| Exact `Closed` | closed |
| Every other captured value, including case/padded variants | other |

Raw status is always part of the grain, even within blank/other categories. This
small reporting map is not an exhaustive NYC taxonomy. Never classify every
non-Closed value as open, infer Closed from a closure date, or add missing categories
as zero-valued groups. Observed named statuses are not continuous source-state or
universal backlog/SLA claims.

### Counters and denominators

- request_count counts all retained identities in the group.
- status_observed_request_count + status_unknown_request_count = request_count.
- status_null_request_count and status_blank_request_count are disjoint subsets of
  observed status, not unknown status.
- A named class count is SUM(request_count) filtered to that class **at one instant**.
- Status coverage uses SUM(status_observed_request_count) / SUM(request_count),
  within one instant and explicit source/mode/agency scope. Include the unknown
  group when reporting total retained scope. No denominator means NULL, not zero.
- Open share among observed rows must state its observed denominator; excluding
  NULL/blank/other rows changes the denominator and requires an explicit label.
- Counts across different instants repeat identities. Never sum them as unique
  requests, events, inflow/outflow or elapsed-time workload. Do not average group
  shares; pool compatible numerators/denominators within the same instant.

### Calendar and coverage

The shared PIT defaults to distinct **loaded status-version capture instants**,
not every unchanged landing capture, agency change, daily boundary or current time.
Agency-only changes are reflected when evaluating a selected later instant but do
not themselves create a default instant. Use explicit timezone-aware
pit_status_as_of_instants and rebuild + dependencies for desired instants. Empty
calendar yields no rows; equivalent instants deduplicate. The shared 366-instant /
1,000,000-grid limits apply at build and PIT query time. A saved PIT not matching the
requested calendar/population fails downstream publication; view-only builds do
not silently reconfigure its calendar.

These are current-evidence observed-time answers. Safe additive late Raw versions
can revise answers; landing-only changes cannot. This is not warehouse-knowledge
as-of, a log of every source transition, proof of continuing status, or retrospective
citywide history. Existing filtered sample and off-cohort reassignment limits apply.

### Reconciliation and inspection

```sql
-- Keep one row per instant and source namespace; never add across these dates.
SELECT as_of_utc, record_source, mode,
       sum(request_count) AS retained_requests,
       sum(status_observed_request_count) AS observed_status,
       sum(status_unknown_request_count) AS unknown_status,
       sum(status_observed_request_count)::DOUBLE / nullif(sum(request_count),0)
           AS status_coverage_share
FROM marts.mart_historical_status
WHERE record_source='synthetic_311' AND mode='synthetic'
GROUP BY ALL ORDER BY as_of_utc;

-- Inspect independent status/assignment lineages for the reassigned fixture.
SELECT as_of_utc, status, is_status_observed, status_snapshot_at_utc,
       agency_bk, is_agency_observed, agency_observed_from, agency_batch_id
FROM marts.int_request_historical_status_population
WHERE record_source='synthetic_311' AND mode='synthetic' AND request_bk='DEMO-002'
ORDER BY as_of_utc;
```

### Verification

Twenty-five network-free integration cases independently reconstruct latest status,
active Links and every original pointer from Raw Vault, then reproduce grouped
counts/capture bounds. They cover boundary microseconds, DST, unknown/NULL/blank/
other, source namespaces, agency-only reassignments/returns, missing assignments,
Hub-only identities, empty/default/explicit calendars, accepted late evidence,
replay, canonical/parent/event corruption, population drift and blocked Raw full
refresh. Copied builds passed 135 selected / 296 full-project dbt tests. The current
sample has 20 groups at four instants over 518 identities (2,072 population rows;
527 observed and 1,545 unknown status rows across instants). These are sample
observations, not permanent thresholds. Every previous base/BV/mart output and
new-mart replay stayed unchanged. M5 remains in progress; Streamlit is next.


## Streamlit dashboard

Implemented on 2026-10-25 in `dashboard/app.py`, using the reporting contracts above.
From the repository root with `.venv` activated:

```bash
python -m streamlit run dashboard/app.py \
  --server.address 127.0.0.1 --browser.gatherUsageStats false
python -m pytest -q tests/test_dashboard.py
```

The default database is repository `data/nyc_open_data.db`, independent of cwd.
NYC311_DASHBOARD_DATABASE selects another existing, already-built file; relative
paths resolve against the repository. Preserve the basename when copying an existing
DuckDB file because stored views bind the catalog. The tested runtime uses Streamlit
1.66, Altair 6.3 and pandas 2.3.3; direct requirements and the existing lockfile cover
these dependencies. The command binds only loopback; no publication is performed.

### Reader flow and filter scope

One dataset selector chooses exactly one record_source/mode pair. Fictional
synthetic fixtures are the default when present. Live evidence is explicitly a
bounded sample. No implicit combined-source view is offered. Each view has its own
controls; changing a view/source starts that view's available defaults. Refresh
rereads built views and reset restores current-view defaults. Neither extracts
records nor invokes dbt. No request-bearing filters are stored in URLs.

| View | Local filters | Evidence and comparisons |
|---|---|---|
| Event trends | Exact lifecycle-capture agency/problem/borough; inclusive NYC event dates | Separate creation/closure totals, dated bars and selected-dimension comparison; context population/date exclusions remain visible |
| Agency & duration | Exact lifecycle-capture agency; NYC creation months including unknown | Retained/eligible/excluded counts; pooled median; mean/median/p90 comparison; agency coverage/count table and duration-quality counts |
| Observed status | UTC PIT instant; exact last-observed agency including unknown | Retained/observed/unknown counts and coverage; exact status composition by agency; observed/unknown coverage points at retained instants |

Multiselect defaults include all exact values. Clearing a selection means **no
rows**, never all rows. NULL is a separate option; non-NULL labels are JSON-quoted
and escaped, preserving blank, whitespace, delimiters, Unicode and literal sentinel
strings. Unknown cohort month is explicit. An incomplete date range asks for its
second endpoint rather than broadening scope. Missing/unbuilt/locked databases
show an actionable build/connection message; no source or synthetic fallback runs.
Empty population, no usable dates, empty configured PIT and no eligible durations
have distinct visible states. The application never silently truncates oversized
query projections; the row cap is 1,000,000 per projection.

### Metric and chart rules

Event counts sum additive mart contributions within selected context/date scope.
A request can appear once as creation and once as closure. Missing endpoints remain
in the context-population caption **across all event dates** because they cannot be
assigned to a selected date range. Date gaps are not filled with zeroes. Bars show
only retained event-date groups; this is not inflow/backlog or citywide coverage.

Duration summaries use request-grain resolution data with its matching verified
lifecycle context. Eligible rows supply hours; zero is retained. Mean, median and
linear p90 are recomputed over the pooled selected request population, never by
averaging subgroup statistics. Unknown/ineligible requests stay in retained and
excluded counts. No eligible rows gives unavailable statistics, not zero. Agency
comparison order is nominal, not a causal ranking. Closure-date share and duration
coverage both use retained count, with independent usable-closure/eligible-pair
numerators. A negative pair can have a usable closure date. Source durations do
not measure first response, SLA attainment or the sum of reopening cycles.

Status composition and headline counts use **one selected instant**. Coverage is
observed status / retained identities at that instant; an empty denominator is
unavailable. NULL/blank observed status belongs to observed coverage, not unknown.
The population includes eventual retained identities before their first capture.
Known assignment can coexist with unknown status and vice versa. Agency filters
follow assignment at each instant, so their selected population can change. The
coverage timeline uses actual UTC instants and isolated points, never interpolated
lines or fabricated daily history. Do not sum its repeated identities across time.
The default PIT calendar still excludes agency-only captures not in status versions.

### Provenance, exports and read boundary

`dashboard/data.py` contains four explicit parameterized projections: population,
trends, agency and status. Population joins request-grain mart_resolution_times to
int_request_trend_population; the other projections use the three aggregate marts.
Each rerun reads all four within one DuckDB read-only transaction, sets UTC display,
and closes the connection. Results/connections have no persistent dashboard cache.
Namespace values are bound parameters. No live API, Raw Vault write, hash, warehouse
load timestamp or inferred freshness clock is added.

Data and definitions expands the exact filtered aggregate rows, their definitions,
source/mode, SQL and parameter values. JSON downloads carry those same scoped rows,
filters and definitions with explicit SQL provenance. Duration exports additionally
include pooled agency chart/table values, the request-population query and the
calculation method. No individual request identifiers/rows, source descriptions,
street addresses or precise coordinates are displayed/exported. JSON preserves
NULL and exact labels; dates serialize as ISO and capture timestamps retain
microsecond precision. Reproduce by rerunning the recorded query with its namespace, applying its recorded view-local filters, and
using the documented pooling method for duration statistics.

### Verification and remaining review

Twenty-six network-free checks run against a temporary synthetic warehouse and
Streamlit AppTest. They verify exact source projections, UTC timestamps, namespace
isolation, bound SQL values, exact NULL/blank/Unicode filters, event range/exclusions,
pooled [0,1,3,9] statistics (mean 3.25, median 2, p90 7.2), unknown cohorts, status
reversals/agency attribution, per-instant denominators, empty/error states, controls,
refresh/reset and scoped exports including pooled duration lineage. Database file
hash checks and subsequent write-open checks verify that dashboard reads leave no
persistent writer/connection. Network connections are blocked during view reruns.

The copied project database passes all 296 dbt checks. AppTest and independent
queries reconcile synthetic (3 retained, 1 eligible) and live (515 retained, 497
eligible) populations and latest status coverage; every prior base/BV/mart output
remains unchanged. Chart specs retain the intended point-only coverage timeline.
A loopback preview starts successfully. Actual browser/native tools reported no
available backend, so rendered desktop/narrow readability, hover and keyboard
review were **not performed**. Keep that M5 checklist item open. Automated app/spec
checks do not substitute for a visual review. M6 offline CI/release work is separate.


### Visual review follow-up (2026-10-28)

The user reviewed the initial dashboard at desktop and narrow widths. Desktop
charts and legends, filter interactions, coverage, exports and empty states were
reported readable or working. Narrow selection chips and the retained-status metric
label were truncated; the duration table required horizontal scrolling. Individual
chart marks had no keyboard focus path in the rendered accessibility tree.

Each multiselect now provides a wrapped, full-text list of selected values. Filters
are stacked, metric rows use at most two columns, and the status metric label is
shorter. The duration view offers a two-column per-agency detail table alongside
the existing summary. Each chart has an adjacent expandable, paged static table
containing the same filtered, aggregated values; pages contain up to 25 rows and
all rows remain available. These alternatives do not make chart marks focusable.

Twenty-nine offline Streamlit checks passed, including filtered chart-table totals,
narrow agency-detail values and access to the final page of a 53-row chart table.
The actual revised UI, screen-reader semantics and keyboard navigation have not yet
been reviewed in a browser because the browser/native backend remained unavailable.
The user-reported review applies to the original layout. Keep the follow-up check
open until the updated UI has been inspected.
