# Data Vault 2.0 Architecture — NYC 311

**Status:** Required M3 models/checks and M4 observed status intervals and bounded PIT implemented; lifecycle metrics remain proposed. Canonical context: [PROJECT_PLAN.md](PROJECT_PLAN.md).

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
| `sat_request_resolution` | request_hk | request_hk + snapshot_at_utc | resolution_description, resolution_action_updated_date |
| `sat_request_location` | request_hk | request_hk + snapshot_at_utc | borough, incident_zip, community_board |
| `sat_agency_details` | agency_hk | agency_hk + snapshot_at_utc | agency_name |

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

## Implemented resolution decisions (2026-10-21)

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

## Implemented location decisions (2026-10-21)

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

## Implemented agency-details decisions (2026-10-22)

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

## Implemented observed status intervals (2026-10-24)

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
