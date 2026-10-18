-- Preserve the exact successful-observation population and original source fields.
{% set fields = [
    'snapshot_at_utc', 'record_source', 'created_date', 'closed_date', 'agency',
    'agency_name', 'complaint_type', 'descriptor', 'status', 'due_date',
    'resolution_action_updated_date', 'resolution_description', 'borough',
    'incident_zip', 'community_board', 'open_data_channel_type'
] %}
with expected as (
    select r.*, b.mode, b.source_id, b.requested_filter
    from {{ source('landing', 'request_snapshots') }} as r
    inner join {{ source('landing', 'ingestion_batch') }} as b
        on r.batch_id = b.batch_id
    where b.status = 'success'
)
select coalesce(e.batch_id, s.batch_id) as batch_id,
       coalesce(e.unique_key, s.unique_key) as unique_key
from expected as e
full outer join {{ ref('stg_311_request_snapshots') }} as s
    on e.batch_id = s.batch_id and e.unique_key = s.unique_key
where e.batch_id is null or s.batch_id is null
    {% for field in fields + ['mode', 'source_id', 'requested_filter'] %}
    or e.{{ field }} is distinct from s.{{ field }}
    {% endfor %}
