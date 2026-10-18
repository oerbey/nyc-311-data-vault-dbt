{{ config(
    materialized='incremental', schema='raw_vault', incremental_strategy='append',
    full_refresh=false, on_schema_change='fail',
    pre_hook=['{{ dv_assignment_preflight() }}']
) }}

with first_observations as (
    select agency_hk, agency_bk, record_source, mode
    from {{ ref('int_request_assignment_observations') }}
    where is_agency_link_eligible
    qualify row_number() over (
        partition by agency_hk order by snapshot_at_utc, batch_id
    ) = 1
)
select agency_hk, agency_bk, record_source, mode, current_timestamp as load_dts_utc
from first_observations as incoming
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} as loaded where loaded.agency_hk = incoming.agency_hk
)
{% endif %}
