# Data Vault 2.0 Architecture — NYC 311

**Status:** Request/agency/problem Hubs, request-assignment Links and status Satellite implemented; assignment effectivity and lifecycle Satellite implemented; resolution/location/agency Satellites remain proposed. Canonical context: [PROJECT_PLAN.md](PROJECT_PLAN.md).

## Modeling principles

- Hub = stable business identity; Link = relationship; Satellite = historized descriptive state.
- Data is a daily refreshed **current-state** feed, not a full authoritative status-change ledger.
- Retain `snapshot_at_utc` (captured state), `load_dts_utc` (first warehouse load) and NYC event dates separately.
- Hubs/Links are insert-once, Raw Vault Satellites insert-only. Business Vault calculates observed validity intervals. Use stable SHA-256 keys with tested canonicalization.

## Entity and relationship design

```text
HUB_AGENCY <-- LNK_REQUEST_AGENCY --> HUB_REQUEST <-- LNK_REQUEST_PROBLEM --> HUB_PROBLEM
    |                   |                 |                    |
SAT_AGENCY_DETAILS  SAT_LNK_AGENCY     SAT_REQUEST_STATUS     SAT_LNK_PROBLEM_EFFECTIVITY
                        EFFECTIVITY     SAT_REQUEST_LIFECYCLE
                                        SAT_REQUEST_RESOLUTION
                                        SAT_REQUEST_LOCATION

RAW VAULT -> BV_REQUEST_STATUS_INTERVALS -> PIT_REQUEST_STATUS_ASOF -> MART_HISTORICAL_STATUS
          -> BV_REQUEST_RESOLUTION_METRICS -> MART_RESOLUTION_TIMES
```

### Hubs

| Model | Business key | Fields | Grain |
|---|---|---|---|
| `hub_request` | (`record_source`, `unique_key`) | request_hk, request_bk, load_dts_utc, record_source, mode | One per source namespace + original request key |
| `hub_agency` | (`record_source`, `agency`) | agency_hk, agency_bk, load_dts_utc, record_source, mode | One per namespace + exact agency code |
| `hub_problem` | (`record_source`, `agency`, `complaint_type`) | problem_hk, problem_bk, agency_bk, complaint_type, load_dts_utc, record_source, mode | One per namespace + agency-scoped problem category |

`unique_key` is a public dataset identifier, not the customer-facing service request number. Composite problem identity is deliberate and should be revisited if authoritative taxonomy keys become available.

### Links and relationship effectivity

| Model | Grain | Fields |
|---|---|---|
| `lnk_request_agency` | One per (request_hk, agency_hk) | link_hk, request_hk, agency_hk, load_dts_utc, record_source |
| `lnk_request_problem` | One per (request_hk, problem_hk) | link_hk, request_hk, problem_hk, load_dts_utc, record_source |
| `sat_lnk_request_agency_effectivity` | Link + observed effective version | link_hk, observed_from, active_flag, hashdiff, load_dts_utc, batch_id |
| `sat_lnk_request_problem_effectivity` | Link + observed effective version | link_hk, observed_from, active_flag, hashdiff, load_dts_utc, batch_id |

When a request is reassigned, preserve both Links and the observed active/inactive transitions. Only an explicit eligible replacement in a successful batch deactivates a previously observed Link. Disappearance, including from a complete filtered cohort, never establishes deactivation. A new link's first observation begins its effectivity, not the actual assignment timestamp.

### Satellites

| Satellite | Parent | Grain | Tracked attributes |
|---|---|---|---|
| `sat_request_status` | request_hk | request_hk + observed_from | status |
| `sat_request_lifecycle` | request_hk | request_hk + snapshot_at_utc | created_date, closed_date, due_date |
| `sat_request_resolution` | request_hk | request_hk + observed_from | resolution_description, resolution_action_updated_date |
| `sat_request_location` | request_hk | request_hk + observed_from | borough, incident_zip, community_board |
| `sat_agency_details` | agency_hk | agency_hk + observed_from | agency_name |

All Satellites include `hashdiff`, `load_dts_utc`, `record_source`, `batch_id`. Track version transitions by comparing **adjacent consecutive snapshots** ordered by capture time (including if historical values recur), not only `DISTINCT` hashdiff across all time. Two snapshots with identical changed attributes should not create a new version. Where captures tie, require stable batch sequence and fail conflicting same-timestamp records.

A generic Hub for request status is unnecessary; status is a changing attribute. Do not place a changing agency code only inside `sat_request_status` instead of preserving its Link relationships.

## Implemented request/status decisions (2026-10-18)

The request Hub deliberately scopes identity to (`record_source`, original `unique_key`) so synthetic and live records cannot merge. This refines the earlier source-key-only proposal without changing the source request grain. `request_bk` retains the exact original string. Agency/problem identities now use the same source namespace convention, as described below.

The reusable hash contract is `dv2:v1:<domain>:<arity>:` followed by ordered fields encoded as `N;` for NULL or `S<UTF-8-byte-length>:<exact-text>;` for non-null text. Lowercase SHA-256 hashes use domain `request_hk` for [record_source, request_bk] and `request_status` for [status]. Canonicalization preserves exact strings, including case, whitespace, empty vs NULL, leading zeroes and Unicode code points. Typed expressions must be rendered canonically before using the text macro. There is no magic NULL sentinel or delimiter substitution.

The Satellite uses (`request_hk`, `snapshot_at_utc`) as version grain. It compares adjacent capture states, retaining reversals and NULL transitions, and suppresses unchanged observations. Same-time identical observations coalesce deterministically by batch ID when first loaded; conflicting status hashes at one instant fail preflight. Existing batch provenance and `load_dts_utc` stay fixed on replay. Warehouse load timestamps are assigned at the first insertion transaction, independently of capture/source timestamps.

Both models use append-only incremental loading with full-refresh disabled and explicitly rejected. Preflight compares already stored versions with the full expected adjacent history. A safe late version can append; a backfill that would retract or alter a stored boundary is blocked before Hub/Satellite mutation. Reconcile such changes in a separate database instead of deleting Raw Vault rows. Landing retention is necessary to validate existing history. The two dbt models commit independently; this slice does not provide one transaction across the entire dependency graph. No Business Vault intervals or PIT model is implemented yet.

## Implemented assignment identity decisions (2026-10-18)

Agency Hubs hash `[record_source, agency_bk]` under domain `agency_hk`; problem Hubs hash `[record_source, agency_bk, complaint_type]` under `problem_hk`. Exact source strings remain the business-key components. This refines the earlier unscoped proposals to keep synthetic/live identities separate. `problem_bk` is the canonical text serialization with prefix `dv2:v1:problem_bk:2:` and the two encoded source components. Names and descriptors belong to later descriptive Satellites.

The agency Link hashes `[request_hk, agency_hk]` under domain `lnk_request_agency`; the problem Link hashes `[request_hk, problem_hk]` under `lnk_request_problem`. Each stores one ever-observed pair with source, mode and first warehouse load time. No active flag, source assignment date or effectivity boundary is inferred. Earlier captures can add new pairs; a return to an old pair does not create another Link identity. The underlying observation view preserves every successful batch and capture for future effectivity.

Missing agency suppresses agency/problem identities and both Links for that observation. With agency present and problem missing, only the agency Hub/Link is eligible. Both original fields and missing/eligibility flags remain visible in `staging.int_request_assignment_observations`; no unknown member is created. Conflicting complete keys are checked per relationship at each request/capture instant before all new assignment models insert. Missing attributes are not an alternative assignment. Links require existing parent Hubs with matching namespace/mode. Request/status models and hashes are unchanged.

Assignment models are append-only, block full refresh, fail schema changes and preserve warehouse metadata on replay. Each model commits independently. Project `indirect_selection: buildable` prevents partial upstream builds from selecting tests whose downstream assignment relations are outside the selection. Effectivity, descriptive Satellites and current-assignment reporting remain pending.

## Implemented assignment effectivity (2026-10-18)

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

## Implemented lifecycle decisions (2026-10-19)

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

## Business Vault examples

```sql
select
    request_hk,
    status,
    snapshot_at_utc as observed_from,
    lead(snapshot_at_utc) over (
        partition by request_hk order by snapshot_at_utc
    ) as next_observed_from
from {{ ref('sat_request_status') }}
```

As-of validity uses `observed_from <= as_of_ts AND (as_of_ts < next_observed_from OR next_observed_from IS NULL)`. Before the first observed snapshot, status is **unknown** (not assumed to be Pending).

- `pit_request_status_asof`: one per request_hk + bounded as-of timestamp; contains the applicable Satellite version reference. Prefer a controlled set of snapshot dates/times.
- `bv_request_resolution_metrics`: one per request at selected as-of timestamp if querying historical state, or one per current request for latest metrics; never mix both grains without identifying as-of.
- `mart_request_trends`: created events and closed events based on respective event dates. An agency assigned **today** should not be presented as the authoritative historical agency at the earlier event date unless source evidence supports it.
- `mart_historical_status`: observed backlog of **sampled cohort** and coverage metrics since snapshot collection began, not an exact citywide historical open count.

## Testing checklist

- Hub PK and BK uniqueness; Link PK, relationships; Satellite version keys.
- Deterministic SHA-256 and unambiguous NULL/string encoding.
- Status sequence Closed → Open → Closed is preserved.
- Replaying unchanged records inserts no extra versions.
- Late-arriving capture reorders historical intervals and PIT.
- As-of boundary and before-first-snapshot expectations.
- Reassignment creates new link and deactivates prior link when successful eligible evidence explicitly identifies a replacement.
- Null/invalid date handling; DST cases; zero-length/negative closure interval quarantine.
- Row-count reconciliation, grouping without fan-out, and source cohort definition reported in marts.

## Design decisions to revisit

1. Make `descriptor` a dependent problem attribute initially; promote to its own Hub only with stable identity and meaningful relationships.
2. Limit geo attributes to borough/ZIP/community board; street-level geolocation not necessary for portfolio.
3. Build an offline synthetic demo first and validate full snapshot history across at least three captures.
