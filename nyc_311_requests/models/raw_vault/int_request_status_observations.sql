{{ config(materialized='view', schema='staging') }}

-- Only exact source text participates in hashes: no trim/case folding, numeric
-- coercion, sentinel replacement or Unicode normalization.
select
    {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }} as request_hk,
    request_bk,
    record_source,
    mode,
    snapshot_at_utc,
    batch_id,
    status,
    {{ dv_sha256(['status'], 'request_status') }} as hashdiff
from {{ ref('stg_311_request_snapshots') }}
