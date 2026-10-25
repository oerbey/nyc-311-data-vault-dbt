{{ config(materialized='view', schema='marts') }}

-- One contribution per usable endpoint, not one per Satellite version/capture.
{% for kind in ['created', 'closed'] %}
select request_hk, request_bk, record_source, mode,
       '{{ kind }}'::varchar as event_type, {{ kind }}_event_date_nyc as event_date_nyc,
       observed_agency_bk, observed_complaint_type, observed_borough,
       dimension_batch_id, dimension_snapshot_at_utc,
       snapshot_at_utc, batch_id, hashdiff, load_dts_utc,
       is_duration_valid, closed_before_created
from {{ ref('int_request_trend_population') }}
where {{ kind }}_event_eligible_count=1
{% if not loop.last %}union all{% endif %}
{% endfor %}
