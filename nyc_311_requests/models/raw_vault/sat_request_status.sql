{{ config(
    materialized='incremental', schema='raw_vault', incremental_strategy='append',
    full_refresh=false, on_schema_change='fail',
    pre_hook=['{{ dv_request_preflight() }}', '{{ dv_request_hub_coverage() }}']
) }}

select incoming.request_hk, incoming.snapshot_at_utc, incoming.status, incoming.hashdiff,
       incoming.batch_id, incoming.record_source, incoming.mode,
       current_timestamp as load_dts_utc
from {{ ref('int_request_status_changes') }} as incoming
inner join {{ ref('hub_request') }} as hub on incoming.request_hk = hub.request_hk
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} as loaded
    where loaded.request_hk = incoming.request_hk
      and loaded.snapshot_at_utc = incoming.snapshot_at_utc
)
{% endif %}
