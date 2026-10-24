with latest as (
    select s.* from {{ ref('sat_request_lifecycle') }} s
    where not exists (
        select 1 from {{ ref('sat_request_lifecycle') }} later
        where later.request_hk=s.request_hk and later.snapshot_at_utc>s.snapshot_at_utc
    )
), expected as (
    select h.request_hk, h.request_bk, h.record_source, h.mode,
           s.request_hk is not null as is_lifecycle_observed,
           s.snapshot_at_utc, s.batch_id, s.hashdiff, s.load_dts_utc,
           {% for attribute in dv_lifecycle_attributes() %}
           s.{{ attribute }},
           {% endfor %}
           coalesce(s.created_at_utc is not null and s.closed_at_utc is not null, false) as evaluable,
           coalesce(s.closed_at_utc>=s.created_at_utc, false) as valid,
           case when s.created_at_utc is not null and s.closed_at_utc is not null
                then (epoch_us(s.closed_at_utc)-epoch_us(s.created_at_utc))/3600000000.0
                else null::double end as elapsed
    from {{ ref('hub_request') }} h left join latest s using(request_hk)
), actual as (select * from {{ ref('bv_request_resolution_metrics') }}),
originals as (
    select request_hk, request_bk, record_source, mode, is_lifecycle_observed,
           snapshot_at_utc, batch_id, hashdiff, load_dts_utc,
           {% for attribute in dv_lifecycle_attributes() %}{{ attribute }},{% endfor %}
           is_duration_evaluable as evaluable, is_duration_valid as valid,
           reported_closure_elapsed_hours as elapsed from actual
), extra as (select * from originals except all select * from expected),
absent as (select * from expected except all select * from originals)
select request_hk from extra
union all select request_hk from absent
union all select request_hk from actual group by request_hk having count(*)<>1
union all select request_hk from actual
where resolution_hours is distinct from (case when is_duration_valid then reported_closure_elapsed_hours else null end)
   or resolution_days is distinct from (case when is_duration_valid then reported_closure_elapsed_hours/24.0 else null end)
   or duration_quality_status is distinct from
      (case when not is_lifecycle_observed then 'lifecycle_unobserved'
            when created_date_time_status='missing' then 'missing_created'
            when created_date_time_status<>'valid' then 'unusable_created'
            when closed_date_time_status='missing' then 'missing_closed'
            when closed_date_time_status<>'valid' then 'unusable_closed'
            when closed_before_created then 'closed_before_created'
            else 'valid' end)
