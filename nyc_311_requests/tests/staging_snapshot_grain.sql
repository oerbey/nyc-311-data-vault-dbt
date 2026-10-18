select batch_id, unique_key
from {{ ref('stg_311_request_snapshots') }}
group by batch_id, unique_key
having count(*) <> 1
