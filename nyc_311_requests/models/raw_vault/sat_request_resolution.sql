{{ config(
    materialized='incremental', schema='raw_vault', incremental_strategy='append',
    full_refresh=false, on_schema_change='fail',
    pre_hook=['{{ dv_resolution_preflight() }}', '{{ dv_resolution_hub_coverage() }}']
) }}

select incoming.request_hk, incoming.snapshot_at_utc, incoming.hashdiff,
       incoming.batch_id, incoming.record_source, incoming.mode,
       {% for attribute in dv_resolution_attributes() %}
       incoming.{{ attribute }},
       {% endfor %}
       current_timestamp as load_dts_utc
from {{ ref('int_request_resolution_changes') }} incoming
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} loaded
    where loaded.request_hk=incoming.request_hk and loaded.snapshot_at_utc=incoming.snapshot_at_utc
)
{% endif %}
