{{ config(materialized='view', schema='business_vault',
          pre_hook=['{{ bv_lifecycle_preflight() }}']) }}

-- Latest capture, not latest warehouse load. This model has no historical as-of grain.
with latest as (
    select * from {{ ref('sat_request_lifecycle') }}
    qualify row_number() over (partition by request_hk order by snapshot_at_utc desc)=1
), measured as (
    select h.request_hk, h.request_bk, h.record_source, h.mode,
           s.request_hk is not null as is_lifecycle_observed,
           s.snapshot_at_utc, s.batch_id, s.hashdiff, s.load_dts_utc,
           {% for attribute in dv_lifecycle_attributes() %}
           s.{{ attribute }},
           {% endfor %}
           coalesce(s.is_lifecycle_evaluable, false) as is_duration_evaluable,
           coalesce(s.is_lifecycle_evaluable and not s.closed_before_created, false) as is_duration_valid,
           case when s.is_lifecycle_evaluable
                then date_diff('microsecond', s.created_at_utc, s.closed_at_utc)/3600000000.0
                else null::double end as reported_closure_elapsed_hours,
           case when s.request_hk is null then 'lifecycle_unobserved'
                when s.created_date_time_status='missing' then 'missing_created'
                when s.created_date_time_status<>'valid' then 'unusable_created'
                when s.closed_date_time_status='missing' then 'missing_closed'
                when s.closed_date_time_status<>'valid' then 'unusable_closed'
                when s.closed_before_created then 'closed_before_created'
                else 'valid' end as duration_quality_status
    from {{ ref('hub_request') }} h left join latest s using(request_hk)
)
select *,
       case when is_duration_valid then reported_closure_elapsed_hours else null::double end as resolution_hours,
       case when is_duration_valid then reported_closure_elapsed_hours/24.0 else null::double end as resolution_days
from measured
