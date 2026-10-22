{{ config(materialized='view', schema='staging') }}

-- Preserve original source text, including leading-zero ZIPs and missing values.
select {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }} as request_hk,
       request_bk, record_source, source_id, mode, requested_filter, snapshot_at_utc, batch_id,
       borough, incident_zip, community_board,
       {{ dv_sha256(['borough', 'incident_zip', 'community_board'], 'request_location') }} as hashdiff
from {{ ref('stg_311_request_snapshots') }}
