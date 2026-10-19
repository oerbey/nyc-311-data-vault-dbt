{{ config(materialized='view', schema='staging') }}

with tied as (
    select * from {{ ref('int_request_lifecycle_observations') }}
    qualify row_number() over (
        partition by request_hk, snapshot_at_utc order by batch_id
    )=1
), sequenced as (
    select *, row_number() over (partition by request_hk order by snapshot_at_utc) as sequence_number,
           lag(hashdiff) over (partition by request_hk order by snapshot_at_utc) as previous_hashdiff
    from tied
)
select * exclude (sequence_number, previous_hashdiff)
from sequenced where sequence_number=1 or hashdiff<>previous_hashdiff
