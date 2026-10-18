-- depends_on: {{ ref('int_request_status_changes') }}
{{ config(
    materialized='incremental', schema='raw_vault', incremental_strategy='append',
    full_refresh=false, on_schema_change='fail',
    pre_hook=['{{ dv_request_preflight() }}']
) }}

with first_observations as (
    select request_hk, request_bk, record_source, mode
    from {{ ref('int_request_status_observations') }}
    qualify row_number() over (
        partition by request_hk order by snapshot_at_utc, batch_id
    ) = 1
)
select request_hk, request_bk, record_source, mode,
       current_timestamp as load_dts_utc
from first_observations as incoming
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} as loaded where loaded.request_hk = incoming.request_hk
)
{% endif %}
