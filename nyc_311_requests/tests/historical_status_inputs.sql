with status_valid as ({{ bv_request_status_preflight() }}),
hubs_valid as ({{ pit_status_preflight() }}),
bounds as ({{ pit_status_bounds() }}),
inputs_valid as ({{ historical_status_input_preflight() }})
select 'invalid historical status inputs' as issue
from status_valid,hubs_valid,bounds,inputs_valid
where status_interval_input_valid<>1 or not pit_input_valid
   or not within_bounds or not historical_status_inputs_valid
