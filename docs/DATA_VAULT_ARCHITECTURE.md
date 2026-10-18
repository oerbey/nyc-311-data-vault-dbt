# Data Vault 2.0 Architecture — NYC 311

**Status:** Request/agency/problem Hubs, request-assignment Links and status Satellite implemented; effectivity and remaining Satellites remain proposed. Canonical context: [PROJECT_PLAN.md](PROJECT_PLAN.md).

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

When a request is reassigned, preserve both Links and the observed active/inactive transitions. For a complete successfully captured **cohort**, disappearance or reassignment of a link can be distinguished from a partial extract; never infer deactivation from a failed/partial batch. A new link's first observation begins its effectivity, not the actual assignment timestamp.

### Satellites

| Satellite | Parent | Grain | Tracked attributes |
|---|---|---|---|
| `sat_request_status` | request_hk | request_hk + observed_from | status |
| `sat_request_lifecycle` | request_hk | request_hk + observed_from | created_date, closed_date, due_date |
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
- Reassignment creates new link and deactivates prior link when a **complete** snapshot proves it.
- Null/invalid date handling; DST cases; zero-length/negative closure interval quarantine.
- Row-count reconciliation, grouping without fan-out, and source cohort definition reported in marts.

## Design decisions to revisit

1. Make `descriptor` a dependent problem attribute initially; promote to its own Hub only with stable identity and meaningful relationships.
2. Limit geo attributes to borough/ZIP/community board; street-level geolocation not necessary for portfolio.
3. Build an offline synthetic demo first and validate full snapshot history across at least three captures.
