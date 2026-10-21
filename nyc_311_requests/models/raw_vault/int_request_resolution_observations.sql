{{ config(materialized='view', schema='staging') }}

-- Source update time is an attribute, never the observed ordering or CDC cursor.
-- Preserve exact raw text, including NULL/blank, formatting and Unicode distinctions.
select {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }} as request_hk,
       request_bk, record_source, source_id, mode, requested_filter, snapshot_at_utc, batch_id,
       {% for attribute in dv_resolution_attributes() if attribute != 'has_resolution_date_quality_issue' %}
       {{ attribute }},
       {% endfor %}
       (resolution_action_updated_date_time_status in
           ('invalid_format', 'ambiguous_local_time', 'nonexistent_local_time'))
           as has_resolution_date_quality_issue,
       {{ dv_sha256(['resolution_description', 'resolution_action_updated_date'], 'request_resolution') }} as hashdiff
from {{ ref('stg_311_request_snapshots') }}
