{{ config(materialized='view', schema='marts', pre_hook=[
    '{{ bv_lifecycle_preflight() }}', '{{ mart_resolution_preflight() }}',
    '{{ mart_trends_preflight() }}'
]) }}

select record_source, mode, event_date_nyc,
       'lifecycle_capture'::varchar as dimension_basis,
       observed_agency_bk, observed_complaint_type, observed_borough,
       count(*) filter (where event_type='created') as created_request_count,
       count(*) filter (where event_type='closed') as closed_request_count
from {{ ref('int_request_trend_events') }}
group by record_source, mode, event_date_nyc,
         observed_agency_bk, observed_complaint_type, observed_borough
