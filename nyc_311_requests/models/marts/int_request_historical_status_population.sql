{{ config(materialized='view', schema='marts', pre_hook=[
    '{{ bv_request_status_preflight() }}', '{{ pit_status_preflight() }}',
    '{{ pit_status_bounds() }}', '{{ historical_status_input_preflight() }}'
]) }}

{{ historical_status_population() }}
