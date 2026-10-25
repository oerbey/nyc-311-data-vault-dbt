{{ config(materialized='view', schema='marts', pre_hook=[
    '{{ bv_request_status_preflight() }}', '{{ pit_status_preflight() }}',
    '{{ pit_status_bounds() }}', '{{ historical_status_input_preflight() }}',
    '{{ historical_status_population_preflight() }}'
]) }}

-- One identity contributes to exactly one group at each bounded UTC instant.
select as_of_utc,record_source,mode,is_agency_observed,agency_hk,agency_bk,
       is_status_observed,status,status_class,dimension_basis,
       count(*) as request_count,
       count(*) filter (where is_status_observed) as status_observed_request_count,
       count(*) filter (where not is_status_observed) as status_unknown_request_count,
       count(*) filter (where status_class='observed_null') as status_null_request_count,
       count(*) filter (where status_class='observed_blank') as status_blank_request_count,
       min(status_snapshot_at_utc) as min_selected_status_capture_at_utc,
       max(status_snapshot_at_utc) as max_selected_status_capture_at_utc,
       min(agency_observed_from) as min_selected_agency_capture_at_utc,
       max(agency_observed_from) as max_selected_agency_capture_at_utc
from {{ ref('int_request_historical_status_population') }}
group by as_of_utc,record_source,mode,is_agency_observed,agency_hk,agency_bk,
         is_status_observed,status,status_class,dimension_basis
