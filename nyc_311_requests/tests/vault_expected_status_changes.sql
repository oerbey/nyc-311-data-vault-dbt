with expected as (
    select request_hk, snapshot_at_utc, hashdiff, status, record_source
    from {{ ref('int_request_status_changes') }}
), actual as (
    select request_hk, snapshot_at_utc, hashdiff, status, record_source
    from {{ ref('sat_request_status') }}
)
select coalesce(e.request_hk, a.request_hk) as request_hk
from expected as e
full outer join actual as a
    on e.request_hk = a.request_hk and e.snapshot_at_utc = a.snapshot_at_utc
where e.request_hk is null or a.request_hk is null
   or e.hashdiff is distinct from a.hashdiff
   or e.status is distinct from a.status
   or e.record_source is distinct from a.record_source
