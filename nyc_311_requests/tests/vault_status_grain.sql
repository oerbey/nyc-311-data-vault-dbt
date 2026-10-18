select request_hk, snapshot_at_utc
from {{ ref('sat_request_status') }}
group by request_hk, snapshot_at_utc
having count(*) <> 1
