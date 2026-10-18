-- A published observation must belong to its successful, matching capture.
select r.batch_id, r.unique_key
from {{ source('landing', 'request_snapshots') }} as r
left join {{ source('landing', 'ingestion_batch') }} as b
    on r.batch_id = b.batch_id
where b.batch_id is null
   or b.status <> 'success'
   or r.record_source <> b.record_source
   or r.snapshot_at_utc <> b.snapshot_at_utc
