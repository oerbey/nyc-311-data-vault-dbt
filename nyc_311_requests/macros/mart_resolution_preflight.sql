{% macro mart_resolution_preflight() %}
    {# Do not publish duplicate, missing or inconsistent upstream reporting rows. #}
    with selected as (
        select request_hk, max(snapshot_at_utc) as snapshot_at_utc
        from {{ ref('sat_request_lifecycle') }} group by request_hk
    ), expected as (
        select h.request_hk, h.request_bk, h.record_source, h.mode,
               s.request_hk is not null as is_lifecycle_observed,
               s.snapshot_at_utc, s.batch_id, s.hashdiff, s.load_dts_utc,
               {% for attribute in dv_lifecycle_attributes() %}s.{{ attribute }},{% endfor %}
               coalesce(s.is_lifecycle_evaluable, false) as is_duration_evaluable,
               coalesce(s.is_lifecycle_evaluable and not s.closed_before_created, false) as is_duration_valid,
               case when s.is_lifecycle_evaluable
                    then (epoch_us(s.closed_at_utc)-epoch_us(s.created_at_utc))/3600000000.0
                    else null::double end as reported_closure_elapsed_hours,
               case when s.request_hk is null then 'lifecycle_unobserved'
                    when s.created_date_time_status='missing' then 'missing_created'
                    when s.created_date_time_status<>'valid' then 'unusable_created'
                    when s.closed_date_time_status='missing' then 'missing_closed'
                    when s.closed_date_time_status<>'valid' then 'unusable_closed'
                    when s.closed_before_created then 'closed_before_created'
                    else 'valid' end as duration_quality_status
        from {{ ref('hub_request') }} h
        left join selected v on v.request_hk=h.request_hk
        left join {{ ref('sat_request_lifecycle') }} s
          on s.request_hk=v.request_hk and s.snapshot_at_utc=v.snapshot_at_utc
    ), completed as (
        select *,
               case when is_duration_valid then reported_closure_elapsed_hours else null::double end as resolution_hours,
               case when is_duration_valid then reported_closure_elapsed_hours/24.0 else null::double end as resolution_days
        from expected
    ), actual as (select * from {{ ref('bv_request_resolution_metrics') }}),
    extra as (select * from actual except all select * from completed),
    absent as (select * from completed except all select * from actual)
    select case when exists (select 1 from extra) or exists (select 1 from absent)
        then error('Resolution mart requires complete unique latest lifecycle metrics and consistent measures/provenance; build +mart_resolution_times')
        else true end as resolution_mart_input_valid
{% endmacro %}
