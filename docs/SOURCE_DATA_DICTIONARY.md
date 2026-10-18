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
| Unique Key | unique_key | request_bk | hub_request | Public source request identifier, not customer-visible SR # |
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
