select request_hk, snapshot_at_utc
from {{ ref('int_request_status_observations') }}
group by request_hk, snapshot_at_utc
having count(distinct hashdiff) > 1
