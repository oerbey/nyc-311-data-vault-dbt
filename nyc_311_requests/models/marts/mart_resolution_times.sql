{{ config(materialized='view', schema='marts',
          pre_hook=['{{ bv_lifecycle_preflight() }}', '{{ mart_resolution_preflight() }}']) }}

-- Reporting grain: one row per source-scoped request identity, latest loaded lifecycle.
with dated as (
    select *,
           case when created_date_time_status='valid'
                then created_at_nyc::date else null::date end as created_event_date_nyc,
           case when closed_date_time_status='valid'
                then closed_at_nyc::date else null::date end as closed_event_date_nyc
    from {{ ref('bv_request_resolution_metrics') }}
)
select *, date_trunc('month', created_event_date_nyc)::date as created_cohort_month_nyc,
       1::bigint as request_count,
       case when is_duration_valid then 1 else 0 end::bigint as duration_eligible_count,
       case when is_duration_valid then 0 else 1 end::bigint as duration_ineligible_count,
       case when created_event_date_nyc is not null then 1 else 0 end::bigint as created_event_eligible_count,
       case when closed_event_date_nyc is not null then 1 else 0 end::bigint as closed_event_eligible_count
from dated
