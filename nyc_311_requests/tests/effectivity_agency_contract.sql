with expected as (
    select * from {{ ref('int_request_agency_effectivity_events') }}
), actual as (
    select * from {{ ref('sat_lnk_request_agency_effectivity') }}
), problems as (
    select a.link_hk from actual a
    left join {{ ref('lnk_request_agency') }} l on a.link_hk = l.link_hk
    where l.link_hk is null or a.request_hk is distinct from l.request_hk
       or a.record_source is distinct from l.record_source or a.mode is distinct from l.mode
       or a.hashdiff <> {{ dv_sha256(["case when a.active_flag then 'true' else 'false' end"], 'request_agency_effectivity') }}
    union all
    select a.link_hk from actual a
    where not exists (
        select 1 from expected e where a.link_hk=e.link_hk and a.observed_from=e.observed_from
          and a.active_flag=e.active_flag and a.hashdiff=e.hashdiff
          and a.request_hk=e.request_hk and a.record_source=e.record_source and a.mode=e.mode
    )
    union all
    select e.link_hk from expected e
    where not exists (
        select 1 from actual a where a.link_hk=e.link_hk and a.observed_from=e.observed_from
          and a.active_flag=e.active_flag
    )
    union all
    select link_hk from actual group by link_hk, observed_from having count(*)<>1
    union all
    select a.link_hk from actual a
    where not exists (
        -- The triggering capture can be the replacement, not the old parent pair.
        select 1 from {{ ref('int_request_assignment_observations') }} o
        where o.batch_id=a.batch_id and o.request_hk=a.request_hk
          and o.snapshot_at_utc=a.observed_from and o.record_source=a.record_source
          and o.mode=a.mode and o.is_agency_link_eligible
          and (not a.active_flag or o.request_agency_link_hk=a.link_hk)
    )
    union all
    select link_hk from (
        select *, row_number() over(partition by link_hk order by observed_from) n,
               lag(active_flag) over(partition by link_hk order by observed_from) previous
        from actual
    ) sequenced where (n=1 and not active_flag) or active_flag=previous
)
select * from problems
