{{ config(
    materialized='incremental', schema='raw_vault', incremental_strategy='append',
    full_refresh=false, on_schema_change='fail',
    pre_hook=['{{ dv_assignment_preflight() }}', '{{ dv_effectivity_preflight() }}',
              '{{ dv_effectivity_parent_coverage("problem") }}']
) }}
-- Explicit dependencies for the shared history guard, including partial builds.
-- depends_on: {{ ref('int_request_agency_effectivity_events') }}
-- depends_on: {{ ref('int_request_problem_effectivity_events') }}
-- depends_on: {{ ref('lnk_request_problem') }}
select incoming.link_hk, incoming.request_hk, incoming.observed_from,
       incoming.active_flag, incoming.hashdiff, incoming.batch_id,
       incoming.record_source, incoming.mode, current_timestamp as load_dts_utc
from {{ ref('int_request_problem_effectivity_events') }} incoming
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} loaded
    where loaded.link_hk = incoming.link_hk and loaded.observed_from = incoming.observed_from
)
{% endif %}
