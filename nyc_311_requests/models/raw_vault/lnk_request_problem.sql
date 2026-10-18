{{ config(
    materialized='incremental', schema='raw_vault', incremental_strategy='append',
    full_refresh=false, on_schema_change='fail',
    pre_hook=['{{ dv_assignment_preflight() }}', "{{ dv_assignment_parent_coverage('problem') }}"]
) }}

with first_observations as (
    select incoming.request_problem_link_hk as link_hk, incoming.request_hk,
           incoming.problem_hk, incoming.record_source, incoming.mode
    from {{ ref('int_request_assignment_observations') }} as incoming
    inner join {{ ref('hub_request') }} as r on incoming.request_hk = r.request_hk
    inner join {{ ref('hub_problem') }} as p on incoming.problem_hk = p.problem_hk
    where incoming.is_problem_link_eligible
    qualify row_number() over (
        partition by incoming.request_problem_link_hk
        order by incoming.snapshot_at_utc, incoming.batch_id
    ) = 1
)
select link_hk, request_hk, problem_hk, record_source, mode,
       current_timestamp as load_dts_utc
from first_observations as incoming
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} as loaded where loaded.link_hk = incoming.link_hk
)
{% endif %}
