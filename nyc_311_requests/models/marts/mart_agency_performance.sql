{{ config(materialized='view', schema='marts', pre_hook=[
    '{{ bv_lifecycle_preflight() }}', '{{ mart_resolution_preflight() }}',
    '{{ mart_trends_preflight() }}', '{{ mart_agency_preflight() }}'
]) }}

-- One request enters one agency/cohort group. Cohort and context can be unknown.
with grouped as (
    select record_source, mode, observed_agency_bk, created_cohort_month_nyc,
           count(*) as retained_request_count,
           count(*) filter (where is_lifecycle_observed) as lifecycle_observed_request_count,
           count(*) filter (where not is_lifecycle_observed) as unobserved_lifecycle_count,
           sum(created_event_eligible_count)::bigint as usable_creation_date_count,
           sum(closed_event_eligible_count)::bigint as usable_closure_date_count,
           count(*) filter (where closed_date_time_status='missing') as missing_closure_date_count,
           count(*) filter (where closed_date_time_status in
               ('invalid_format','ambiguous_local_time','nonexistent_local_time')) as unusable_closure_date_count,
           sum(duration_eligible_count)::bigint as duration_eligible_count,
           sum(duration_ineligible_count)::bigint as duration_ineligible_count,
           count(*) filter (where closed_before_created) as closed_before_created_count,
           sum(resolution_hours) as total_resolution_hours,
           avg(resolution_hours) as mean_resolution_hours,
           median(resolution_hours) as median_resolution_hours,
           quantile_cont(resolution_hours, 0.9) as p90_resolution_hours,
           min(snapshot_at_utc) as min_selected_lifecycle_capture_at_utc,
           max(snapshot_at_utc) as max_selected_lifecycle_capture_at_utc
    from {{ ref('int_request_trend_population') }}
    group by record_source, mode, observed_agency_bk, created_cohort_month_nyc
)
select *, 'lifecycle_capture'::varchar as dimension_basis,
       {{ nyc311_nonblank_text('observed_agency_bk') }} is null as has_missing_agency,
       created_cohort_month_nyc is not null as is_cohort_month_known,
       usable_closure_date_count::double/retained_request_count as reported_closure_date_share,
       duration_eligible_count::double/retained_request_count as duration_coverage_share
from grouped
