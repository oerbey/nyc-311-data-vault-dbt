{{ config(materialized='view', schema='staging') }}

{{ dv_effectivity_events('agency') }}
