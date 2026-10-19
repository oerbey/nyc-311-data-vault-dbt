{{ config(materialized='view', schema='staging') }}

-- Hash raw text only. Date parsing/quality policy belongs to staging and does not
-- normalize NULL, blank, formatting, case or Unicode before change detection.
select {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }} as request_hk,
       request_bk, record_source, source_id, mode, requested_filter, snapshot_at_utc, batch_id,
       {% for attribute in dv_lifecycle_attributes() if attribute != 'has_lifecycle_date_quality_issue' %}
       {{ attribute }},
       {% endfor %}
       (created_date_time_status in ('invalid_format', 'ambiguous_local_time', 'nonexistent_local_time')
        or closed_date_time_status in ('invalid_format', 'ambiguous_local_time', 'nonexistent_local_time')
        or due_date_time_status in ('invalid_format', 'ambiguous_local_time', 'nonexistent_local_time'))
           as has_lifecycle_date_quality_issue,
       {{ dv_sha256(['created_date', 'closed_date', 'due_date'], 'request_lifecycle') }} as hashdiff
from {{ ref('stg_311_request_snapshots') }}
