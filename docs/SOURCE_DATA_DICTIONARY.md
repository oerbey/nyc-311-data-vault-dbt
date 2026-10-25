# NYC 311 Source-to-Target Dictionary

**Source:** [NYC 311 2020–Present](https://data.cityofnewyork.us/d/erm2-nwe9) · [Socrata API](https://data.cityofnewyork.us/resource/erm2-nwe9.json). Source definitions cross-checked against the user-supplied Excel dictionary `311_ServiceRequest_2020-present_DataDictionary_Updated_2025.xlsx` (not committed).

## Critical dataset facts
- Each source row represents **one Service Request** (Unique Key).
- Daily refresh; published values can change after creation, usually with a publication buffer.
- This is not an event-by-event historical change feed.
- Display labels **Problem**, **Problem Detail** correspond to API **`complaint_type`**, **`descriptor`**. Preserve API field names in extraction SQL.
- The spreadsheet's **Dataset Revision History** describes schema/documentation revisions—not per-request state history.
- `due_date` = when responding agency is expected to **update** a service request; not guaranteed closure deadline.
- `resolution_action_updated_date` = last reported resolution-action update; not necessarily every status transition.

## Proposed source-to-target map

| Excel label | API field | Normalized staging field | Target | Semantics |
|---|---|---|---|---|
| Unique Key | unique_key | request_bk | hub_request | Original text; identity is scoped by record_source, not a customer-visible SR # |
| Created Date | created_date | created_at_nyc | sat_request_lifecycle | Creation event |
| Closed Date | closed_date | closed_at_nyc | sat_request_lifecycle | Closure event, nullable |
| Agency | agency | agency_bk | hub_agency, lnk_request_agency | Responding agency, possibly changes |
| Agency Name | agency_name | agency_name | sat_agency_details | Descriptive name |
| Problem | complaint_type | complaint_type | hub_problem (with agency) | Agency-scoped category |
| Problem Detail | descriptor | descriptor | staging; optional problem detail Satellite | Further detail |
| Additional Details | additional_details | additional_details | Deferred | Field available in newer schema; may be verbose |
| Status | status | status | sat_request_status | Current reported status at each snapshot |
| Due Date | due_date | due_at_nyc | sat_request_lifecycle | Expected update date |
| Resolution Action Updated Date | resolution_action_updated_date | resolution_action_updated_at_nyc | sat_request_resolution | Last action update |
| Resolution Description | resolution_description | resolution_description | sat_request_resolution | Text/last action |
| Borough | borough | borough | sat_request_location | Geography |
| Incident Zip | incident_zip | incident_zip | sat_request_location | Postal code; keep text |
| Community Board | community_board | community_board | sat_request_location | Geographic classification |
| Open Data Channel Type | open_data_channel_type | channel_type | Optional sat_request_submission | Submission channel |
| BBL | bbl | bbl | Deferred | Potential future property/business entity |
| Latitude / Longitude | latitude / longitude | — | Excluded MVP | Not needed for borough-level analytics |
| Street Address | incident_address | — | Excluded MVP | Privacy/data-minimization |

**Review source metadata before coding:** Field optionality, descriptions, and schema may change. Validate the live Socrata schema in M2 and document differences. The Excel dictionary is a reference, not the extracted records.

## Live landing validation (2026-10-18)

The API metadata at `/api/views/erm2-nwe9.json` confirmed all 15 planned landing fields. `created_date`, `closed_date`, `due_date` and `resolution_action_updated_date` report `calendar_date`; the remaining selected fields report `text`. The adapter accepts floating source timestamp metadata (`calendar_date` / `floating_timestamp`) and preserves the JSON date strings. Missing required metadata fields or changed semantic types fail the extraction without column substitution. Missing optional values within individual records remain NULL. This verification covers the landing selection, not every exploratory notebook field such as `descriptor_2`.

## Timestamp mapping

| Column | Meaning | Do not confuse with |
|---|---|---|
| created_date | Source-submitted event | Ingestion time |
| closed_date | Source-recorded closed event | Date status was *first observed* closed |
| due_date | Expected agency update | Closure SLA |
| resolution_action_updated_date | Last action update | Guaranteed CDC timestamp |
| snapshot_at_utc | Extract capture | Actual City-side transition time |
| load_dts_utc | First persisted Vault version load | Snapshot time |

Use an explicit `America/New_York` interpretation for the source floating dates, subject to validation. Normalize event timestamps consistently and preserve raw source strings for debugging.

## Implemented staging contract

The staging view preserves the original 15 source columns and adds batch metadata, text aliases `request_bk` / `agency_bk`, `source_timezone`, and typed time columns. `created_date` maps to `created_at_nyc` / `created_at_utc`; closure and due dates map similarly. Resolution updates map to `resolution_action_updated_at_nyc` / `_utc`. `_nyc` is a naive wall-clock TIMESTAMP; `_utc` is a TIMESTAMPTZ instant.

Every original date has `<field>_time_status` (valid, missing, invalid_format, ambiguous_local_time, nonexistent_local_time) and `<field>_parse_failed`. Missing and DST uncertainty differ from parse failure. UTC conversion uses America/New_York and leaves ambiguous/nonexistent local times unresolved. Missing required attributes, date quality issues and reversed lifecycle order are flagged while retaining rows. Both lifecycle endpoints must have usable UTC instants before `closed_before_created` can be evaluated; otherwise it is NULL. No source time is confused with `snapshot_at_utc`, and no Vault load timestamp is created.

## Implemented request/status Vault mapping

`request_hk` hashes [record_source, original request_bk] under domain `request_hk`; this isolates synthetic and live source namespaces. `hashdiff` hashes only the exact source `status` under domain `request_status`. V1 canonical text preserves NULL/empty/case/whitespace/Unicode distinctions with UTF-8 byte lengths. `sat_request_status` stores observed transitions by capture time, raw status, batch/source/mode provenance and independent first-load warehouse timestamps. Same-time conflicting states are rejected. No historical status is inferred before the first observation; no source event dates participate in the status hashdiff or observed ordering.

## Implemented assignment mapping

`agency_bk` and `complaint_type` remain exact source text. The agency hash uses `[record_source, agency_bk]`; the problem hash uses `[record_source, agency_bk, complaint_type]`. Domains are `agency_hk` and `problem_hk`. `problem_bk` stores the existing v1 canonical text encoding of the two raw components with prefix `dv2:v1:problem_bk:2:`. Agency names and descriptors do not define identity.

Links hash the ordered parent hashes under domains `lnk_request_agency` and `lnk_request_problem`. Incomplete original keys remain visible with missing/eligibility flags; missing agency makes both Links ineligible, while missing problem suppresses only problem structures when the agency is known. Links mean ever observed, not currently active. Batch/capture evidence is retained in the assignment observation view; stored source/mode/load timestamps survive replay. No event-time reassignment or deactivation is inferred.

## Implemented effectivity mapping

`request_agency_link_hk` / `request_problem_link_hk` map to the corresponding
Link effectivity Satellite's `link_hk`; successful eligible captures map
`snapshot_at_utc` to timezone-aware `observed_from`. Each initial assignment
produces `active_flag=true`; an explicit replacement produces previous-Link false
and replacement-Link true. `batch_id` identifies that triggering replacement
capture for both rows. Source/mode are retained and `load_dts_utc` records first
warehouse insertion. Boolean hashdiffs encode a single lowercase text value
`true`/`false` under domains `request_agency_effectivity` and
`request_problem_effectivity` using unchanged SHA-256 v1.

Missing fields, absent requests and failed/running batches produce no effectivity
change. Agency and problem eligibility apply independently, so last-known problem
can lag a newly observed agency when the category is missing. These are capture-time
observations, not authoritative source assignment dates. No source event dates,
agency names or descriptors determine relationship effectivity.

## Implemented lifecycle mapping

`created_date`, `closed_date`, `due_date` remain nullable raw text in
`sat_request_lifecycle`, in that order under hashdiff domain `request_lifecycle`.
Each includes staging's parsed `created_at_nyc` / `created_at_utc` (and corresponding
closed/due columns), per-raw-field time status and parse-failure flag. The Satellite
also preserves timezone, lifecycle-evaluable/closed-before-created flags, and a
lifecycle-only date quality flag covering malformed/DST-uncertain values in these
three fields. Missing dates remain explicit through their time status. Resolution
update quality does not affect this tuple or flag.

Successful source capture maps to observed `snapshot_at_utc`; warehouse insertion
maps to independent `load_dts_utc`. Raw strings drive change detection, so normalized
format equivalence never hides an observation. A NULL closure after a populated
closure is a new version, not deletion of earlier evidence. Optional/missing/bad
values are retained, and no status transition, closure duration or universal SLA
is inferred from them. Source-scoped request hashes remain unchanged.

## Implemented resolution mapping

The ordered exact raw tuple `[resolution_description, resolution_action_updated_date]`
uses hashdiff domain `request_resolution` in `sat_request_resolution`. Nullable text,
whitespace, line breaks, Unicode and date formatting remain unchanged. Both fields
can change independently or clear to NULL. Adjacent reversals remain separate
capture-time versions; no cross-history deduplication is performed.

The source date also maps to staging's `resolution_action_updated_at_nyc` / `_utc`,
`resolution_action_updated_date_time_status` and `*_parse_failed`. The Satellite
retains these values plus timezone and `has_resolution_date_quality_issue` for
malformed/DST-uncertain resolution dates only. Missing source dates remain explicit;
other source date errors do not affect this flag. Only unambiguous valid NYC local
times yield UTC. Pipeline `snapshot_at_utc` orders observed history, independently
of source update and first warehouse `load_dts_utc`. Update dates may repeat or move
backward and are not interpreted as a complete CDC/status transition log.
Source-scoped request keys and all earlier hash definitions remain unchanged.

## Implemented location mapping

`sat_request_location` stores the ordered exact raw tuple
`[borough, incident_zip, community_board]` under SHA-256 v1 domain `request_location`.
All are nullable VARCHAR. ZIP/community-board leading zeroes, case, whitespace,
Unicode, delimiters, NULL and blank values survive unchanged. Unknown/unspecified
source strings remain source evidence rather than being remapped or rejected.
Missing attributes in a captured request produce observed versions, not carry-forward;
absence of the request itself produces no new observation.

Successful source `snapshot_at_utc` orders adjacent versions independently of
warehouse first `load_dts_utc`. Source event dates do not backdate geography.
Reversals and returning tuples are retained; unchanged successive tuples collapse.
No street address, precise coordinates or additional geography is introduced.
Source-scoped request keys and existing hash definitions remain unchanged.

## Implemented agency-details mapping

Exact nullable `agency_name` maps to `sat_agency_details` under hashdiff domain
`agency_details`. Its parent `agency_hk` remains the unchanged hash of source
namespace plus exact `agency_bk`. Name case/whitespace/Unicode, NULL and blank are
preserved; names and descriptors never change agency identity.

Only valid nonblank agency keys contribute versions. Missing agency rows remain
flagged in the observation view; a missing category does not suppress an agency
name. NULL/blank names remain eligible observed values, with no carry-forward or
populated-value preference. At one agency/capture, all request/batch observations
must agree; conflicts fail. Initial evidence selection is batch ID then request key.
`evidence_request_bk` plus stored batch identifies a supporting landing row and
survives replay unchanged. It is not a request Hub relationship. Adjacent capture
changes preserve renames, clearing and returns, with source-scoped identities and
separate first warehouse load time. These feed labels do not establish legal agency
names or source-side rename event dates.

## Implemented observed status interval mapping

`sat_request_status.snapshot_at_utc` maps directly to
`business_vault.bv_request_status_intervals.observed_from`. The next loaded capture
for that request maps to exclusive `next_observed_from`; the latest version has a
NULL end. Exact nullable status/hashdiff and original batch/source/mode/load timestamp
are projected unchanged. No source event dates or new hash inputs are introduced.

As-of matching uses start-inclusive/end-exclusive boundaries, with no row before
first capture. An observed NULL/blank status remains a version and never inherits
a previous non-missing status. Latest open-ended means last known reported state,
not source freshness. Late Raw Vault evidence revises derived endpoints automatically;
warehouse-knowledge-time filtering and bounded PIT are separate future features.
Pending landing observations cannot change intervals until Raw Vault loading succeeds.

## Sample source parameters

```python
params = {
    "$select": (
        "unique_key,created_date,closed_date,agency,agency_name,"
        "complaint_type,descriptor,status,due_date,"
        "resolution_action_updated_date,resolution_description,"
        "borough,incident_zip,community_board,open_data_channel_type"
    ),
    "$where": (
        "created_date >= '2026-01-01T00:00:00' AND "
        "created_date < '2026-07-01T00:00:00' AND "
        "agency IN ('DEP','DSNY')"
    ),
    "$order": "unique_key",
    "$limit": 5000,
    "$offset": 0,
}
```

This is an **illustrative** first-page request, not a complete ingestion algorithm. Implement pagination, consistent cohort refresh, completeness audit, retries and explicit source schema verification. A Socrata app token is optional for public reads; keep it only in local environment variables.

## Implemented bounded status PIT mapping

The PIT calendar maps to timezone-aware `as_of_utc`. Default membership is distinct
loaded `sat_request_status.snapshot_at_utc`, not all landing captures or source
event dates; explicit timezone-aware instants can replace it. Maximum 366 instants
and 1,000,000 Hub/calendar rows apply at build and query time.
`hub_request` supplies namespace/mode and one row per identity/calendar instant.
The interval containing the instant supplies `status_snapshot_at_utc`,
`status_load_dts_utc`, `status_hashdiff`, `status_batch_id`, and exact nullable
`status`. The first two reference capture and original first warehouse load,
respectively; they are never interchangeable. Unknown before first capture (or a
Hub with no status version) has `is_status_observed=false` and NULL status fields.
Observed NULL status has the flag true and retains its version provenance.
No new hashes, event-time interpretation or warehouse refresh timestamp is introduced.
See the architecture's bounded PIT section for configuration and current-evidence
late-arrival semantics; this is not historical warehouse-knowledge reconstruction.

## Implemented lifecycle duration metric mapping

Latest `sat_request_lifecycle.snapshot_at_utc` per request supplies all raw/typed
lifecycle fields and original capture/hash/batch/load provenance to
`business_vault.bv_request_resolution_metrics`. Request Hub supplies identity,
namespace/mode and a row even when lifecycle is not loaded. No event date backdates
capture and no warehouse-load ordering or new metric refresh clock is introduced.
`reported_closure_elapsed_hours` subtracts usable `created_at_utc` from
`closed_at_utc` as signed elapsed hours. Nonnegative usable pairs populate
`resolution_hours` and `resolution_days` (hours / 24); unusable/reversed pairs have
NULL valid-duration measures. Original per-field and chronology flags are preserved;
`duration_quality_status` prioritizes unknown, creation issues, closure issues,
negative order, then valid. Observed missing values differ from unloaded lifecycle.
Due-date quality does not gate a valid creation/closure pair. This latest-capture
source-date metric is independent of status, not an actual resolution guarantee,
response-time, SLA or historical lifecycle as-of measure. See architecture for
precise grain, validity limits, DST policy and examples.

## Implemented resolution-time mart mapping

`bv_request_resolution_metrics` maps unchanged into `marts.mart_resolution_times`,
preserving all raw/typed attributes, signed/valid durations and original lineage.
When the respective stored time status is valid, naive `created_at_nyc` /
`closed_at_nyc` map to NYC `created_event_date_nyc` / `closed_event_date_nyc` DATEs;
otherwise reporting dates are NULL. Creation's month start is
`created_cohort_month_nyc`. UTC date casts are not equivalent near local midnight.
`request_count=1` retains all Hub identities; duration eligible/ineligible counters
partition that population by existing `is_duration_valid`. Each event eligibility
counter depends only on its own usable date, independently of duration order or
status. Counters are BIGINT; no source fields or hashes are added. See
[REPORTING_CONTRACTS.md](REPORTING_CONTRACTS.md) for denominator/scope semantics.

## Implemented request-trend mapping

Selected lifecycle `batch_id` + original request key + source/mode/capture identify
one successful landing row whose raw created/closed/due strings must agree. Its
exact agency/complaint_type/borough map to observed_agency_bk /
observed_complaint_type / observed_borough, with dimension batch/capture lineage.
No event-time/current assignment or new business identity is inferred. Unloaded
lifecycle is distinct from captured missing context through is_dimension_observed;
missing flags inspect whitespace without changing grouping text.
Each usable created_event_date_nyc / closed_event_date_nyc maps to one event-view
contribution with type created/closed. Aggregation groups source, mode, local event
date and observed context into separate BIGINT counts, labelled lifecycle_capture.
No report clock, hash or source date interpretation is added. The population keeps
excluded endpoints; closure-date eligibility remains independent of duration order.
See [REPORTING_CONTRACTS.md](REPORTING_CONTRACTS.md) for attribution and scope limits.
