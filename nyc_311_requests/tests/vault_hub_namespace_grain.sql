select record_source, request_bk
from {{ ref('hub_request') }}
group by record_source, request_bk
having count(*) <> 1
