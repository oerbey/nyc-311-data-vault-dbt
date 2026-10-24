with expected as (
    select * from {{ ref('int_agency_details_changes') }}
), actual as (
    select * from {{ ref('sat_agency_details') }}
), violations as (
    select a.agency_hk from actual a where not exists (
        select 1 from expected e where a.agency_hk=e.agency_hk
          and a.snapshot_at_utc=e.snapshot_at_utc and a.hashdiff=e.hashdiff
          and a.agency_name is not distinct from e.agency_name
          and a.record_source=e.record_source and a.mode=e.mode
    )
    union all
    select e.agency_hk from expected e where not exists (
        select 1 from actual a where a.agency_hk=e.agency_hk and a.snapshot_at_utc=e.snapshot_at_utc
    )
    union all
    select agency_hk from actual group by agency_hk, snapshot_at_utc having count(*)<>1
    union all
    select a.agency_hk from actual a
    where a.hashdiff <> {{ dv_sha256(['a.agency_name'], 'agency_details') }} or not exists (
        select 1 from {{ ref('int_agency_details_observations') }} o
        where o.is_agency_details_eligible and a.agency_hk=o.agency_hk
          and a.batch_id=o.batch_id and a.evidence_request_bk=o.request_bk
          and a.snapshot_at_utc=o.snapshot_at_utc and a.hashdiff=o.hashdiff
          and a.agency_name is not distinct from o.agency_name
          and a.record_source=o.record_source and a.mode=o.mode
    )
    union all
    select a.agency_hk from actual a left join {{ ref('hub_agency') }} h using(agency_hk)
    where h.agency_hk is null or a.record_source is distinct from h.record_source or a.mode is distinct from h.mode
    union all
    select agency_hk from {{ ref('int_agency_details_observations') }}
    where is_agency_details_eligible group by agency_hk, snapshot_at_utc having count(distinct hashdiff)>1
)
select * from violations
