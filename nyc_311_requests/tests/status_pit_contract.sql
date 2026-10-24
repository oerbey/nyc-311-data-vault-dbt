with calendar as ({{ pit_status_calendar() }}), expected as (
    -- Independently select the latest capture <= as-of; no interval join here.
    select h.request_hk, c.as_of_utc, h.record_source, h.mode,
           s.request_hk is not null as is_status_observed,
           s.snapshot_at_utc as status_snapshot_at_utc,
           s.load_dts_utc as status_load_dts_utc,
           s.hashdiff as status_hashdiff, s.batch_id as status_batch_id, s.status
    from {{ ref('hub_request') }} h cross join calendar c
    left join {{ ref('sat_request_status') }} s
      on s.request_hk=h.request_hk
     and s.snapshot_at_utc=(select max(v.snapshot_at_utc)
          from {{ ref('sat_request_status') }} v
          where v.request_hk=h.request_hk and v.snapshot_at_utc<=c.as_of_utc)
), actual as (select * from {{ ref('pit_request_status_asof') }}),
extra as (select * from actual except all select * from expected),
absent as (select * from expected except all select * from actual)
select request_hk from extra
union all select request_hk from absent
union all select request_hk from actual
    group by request_hk, as_of_utc having count(*)<>1
union all select request_hk from actual where status_snapshot_at_utc>as_of_utc
