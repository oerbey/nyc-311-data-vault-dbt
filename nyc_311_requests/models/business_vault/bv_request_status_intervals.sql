{{ config(materialized='view', schema='business_vault',
          pre_hook=['{{ bv_request_status_preflight() }}']) }}

-- depends_on: {{ ref('hub_request') }}
-- Half-open observed intervals: start <= as_of < next start. NULL end means
-- last known reported state, with no inferred freshness or City-side event time.
select request_hk,
       snapshot_at_utc as observed_from,
       lead(snapshot_at_utc) over (
           partition by request_hk order by snapshot_at_utc
       ) as next_observed_from,
       status, hashdiff, batch_id, record_source, mode, load_dts_utc
from {{ ref('sat_request_status') }}
