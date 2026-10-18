select coalesce(s.batch_id,o.batch_id) as batch_id,
       coalesce(s.request_bk,o.request_bk) as request_bk
from {{ ref('stg_311_request_snapshots') }} as s
full outer join {{ ref('int_request_assignment_observations') }} as o
    on s.batch_id=o.batch_id and s.request_bk=o.request_bk
where s.batch_id is null or o.batch_id is null
{% for col in ['snapshot_at_utc','record_source','source_id','mode','requested_filter','agency_bk','complaint_type','agency_name','descriptor'] %}
   or s.{{ col }} is distinct from o.{{ col }}
{% endfor %}
