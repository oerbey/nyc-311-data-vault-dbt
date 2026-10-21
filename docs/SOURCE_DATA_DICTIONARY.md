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
