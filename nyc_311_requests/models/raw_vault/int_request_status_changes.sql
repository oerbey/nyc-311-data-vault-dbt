{{ config(materialized='view', schema='staging') }}

with tied_observations as (
    -- Identical values captured in multiple batches at one instant have one
    -- version; conflicting values at one instant are blocked by the preflight.
    select *
    from {{ ref('int_request_status_observations') }}
    qualify row_number() over (
        partition by request_hk, snapshot_at_utc order by batch_id
    ) = 1
), sequenced as (
    select *,
        row_number() over (partition by request_hk order by snapshot_at_utc) as sequence_number,
        lag(hashdiff) over (partition by request_hk order by snapshot_at_utc) as previous_hashdiff
    from tied_observations
)
select request_hk, request_bk, record_source, mode, snapshot_at_utc, batch_id, status, hashdiff
from sequenced
where sequence_number = 1 or hashdiff <> previous_hashdiff
