{{ config(
    materialized='incremental', schema='raw_vault', incremental_strategy='append',
    full_refresh=false, on_schema_change='fail',
    pre_hook=['{{ dv_agency_details_preflight() }}', '{{ dv_agency_details_hub_coverage() }}']
) }}

select incoming.agency_hk, incoming.snapshot_at_utc, incoming.agency_name, incoming.hashdiff,
       incoming.batch_id, incoming.record_source, incoming.mode,
       incoming.request_bk as evidence_request_bk, current_timestamp as load_dts_utc
from {{ ref('int_agency_details_changes') }} incoming
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} loaded
    where loaded.agency_hk=incoming.agency_hk and loaded.snapshot_at_utc=incoming.snapshot_at_utc
)
{% endif %}
