{{ config(materialized='view', schema='staging') }}

-- Retain every successful request observation, including unusable agency keys.
-- A missing name is an observed value, not an eligibility filter.
select batch_id, snapshot_at_utc, record_source, source_id, mode, requested_filter,
       request_bk, agency_bk, agency_hk, agency_name, has_missing_agency,
       ({{ nyc311_nonblank_text('agency_name') }} is null) as has_missing_agency_name,
       is_agency_link_eligible as is_agency_details_eligible,
       {{ dv_sha256(['agency_name'], 'agency_details') }} as hashdiff
from {{ ref('int_request_assignment_observations') }}
