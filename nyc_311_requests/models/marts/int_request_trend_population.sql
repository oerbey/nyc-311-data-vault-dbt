{{ config(materialized='view', schema='marts', pre_hook=[
    '{{ bv_lifecycle_preflight() }}', '{{ mart_resolution_preflight() }}',
    '{{ mart_trends_preflight() }}'
]) }}

with evidence as ({{ mart_trend_evidence() }})
select m.*, e.agency as observed_agency_bk,
       e.complaint_type as observed_complaint_type, e.borough as observed_borough,
       e.batch_id as dimension_batch_id, e.snapshot_at_utc as dimension_snapshot_at_utc,
       e.batch_id is not null as is_dimension_observed,
       {{ nyc311_nonblank_text('e.agency') }} is null as has_missing_agency,
       {{ nyc311_nonblank_text('e.complaint_type') }} is null as has_missing_problem,
       {{ nyc311_nonblank_text('e.borough') }} is null as has_missing_borough
from {{ ref('mart_resolution_times') }} m
left join evidence e on e.batch_id=m.batch_id and e.unique_key=m.request_bk
  and e.record_source=m.record_source and e.mode=m.mode
  and e.snapshot_at_utc=m.snapshot_at_utc
