select s.request_hk, s.snapshot_at_utc
from {{ ref('sat_request_status') }} as s
left join {{ ref('int_request_status_observations') }} as o
    on s.request_hk = o.request_hk
   and s.snapshot_at_utc = o.snapshot_at_utc
   and s.batch_id = o.batch_id
where o.batch_id is null
   or s.status is distinct from o.status
   or s.hashdiff is distinct from o.hashdiff
   or s.mode is distinct from o.mode
   or s.record_source is distinct from o.record_source
