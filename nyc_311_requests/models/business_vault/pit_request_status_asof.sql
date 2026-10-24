{{ config(materialized='view', schema='business_vault', pre_hook=[
    '{{ bv_request_status_preflight() }}',
    '{{ pit_status_preflight() }}',
    '{{ pit_status_bounds() }}'
]) }}

with calendar as ({{ pit_status_calendar() }}),
-- Also enforce limits at query time as the underlying Raw Vault grows.
bounds as ({{ pit_status_bounds() }})
select h.request_hk, c.as_of_utc, h.record_source, h.mode,
       i.request_hk is not null as is_status_observed,
       i.observed_from as status_snapshot_at_utc,
       i.load_dts_utc as status_load_dts_utc,
       i.hashdiff as status_hashdiff, i.batch_id as status_batch_id, i.status
from {{ ref('hub_request') }} h
cross join calendar c
cross join bounds b
left join {{ ref('bv_request_status_intervals') }} i
    on i.request_hk=h.request_hk and i.observed_from<=c.as_of_utc
   and (i.next_observed_from is null or c.as_of_utc<i.next_observed_from)
where b.within_bounds
