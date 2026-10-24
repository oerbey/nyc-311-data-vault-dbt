with expected as (
    -- Derive each endpoint independently as the earliest later retained version.
    select s.request_hk, s.snapshot_at_utc as observed_from,
           (select min(later.snapshot_at_utc) from {{ ref('sat_request_status') }} later
            where later.request_hk=s.request_hk and later.snapshot_at_utc>s.snapshot_at_utc)
               as next_observed_from,
           s.status, s.hashdiff, s.batch_id, s.record_source, s.mode, s.load_dts_utc
    from {{ ref('sat_request_status') }} s
), actual as (
    select request_hk, observed_from, next_observed_from, status,
           hashdiff, batch_id, record_source, mode, load_dts_utc
    from {{ ref('bv_request_status_intervals') }}
), unexpected as (
    select * from actual except all select * from expected
), absent as (
    select * from expected except all select * from actual
), sequenced as (
    select *, row_number() over (partition by request_hk order by observed_from) as n,
           lag(next_observed_from) over (partition by request_hk order by observed_from) as previous_end
    from actual
)
select request_hk from unexpected
union all select request_hk from absent
union all select request_hk from actual
    group by request_hk, observed_from having count(*)<>1
union all select request_hk from actual
    where next_observed_from<=observed_from
union all select request_hk from actual
    group by request_hk having count(*) filter (where next_observed_from is null)<>1
union all select request_hk from sequenced
    where n>1 and previous_end is distinct from observed_from
