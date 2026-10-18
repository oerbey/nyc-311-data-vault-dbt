{{ config(materialized='view', schema='staging') }}

{% set timestamps = nyc311_timestamp_fields() %}
with successful_observations as (
    select
        r.batch_id,
        r.snapshot_at_utc,
        r.record_source,
        b.source_id,
        b.mode,
        b.requested_filter,
        r.unique_key,
        r.created_date,
        r.closed_date,
        r.agency,
        r.agency_name,
        r.complaint_type,
        r.descriptor,
        r.status,
        r.due_date,
        r.resolution_action_updated_date,
        r.resolution_description,
        r.borough,
        r.incident_zip,
        r.community_board,
        r.open_data_channel_type
    from {{ source('landing', 'request_snapshots') }} as r
    inner join {{ source('landing', 'ingestion_batch') }} as b
        on r.batch_id = b.batch_id
    where b.status = 'success'
), parsed as (
    select
        *,
        {% for raw, local, utc in timestamps %}
        {{ nyc311_parse_timestamp(raw) }} as {{ local }}{{ ',' if not loop.last }}
        {% endfor %}
    from successful_observations
), candidates as (
    select
        *,
        {% for raw, local, utc in timestamps %}
        timezone('America/New_York', {{ local }}) as _{{ utc }}{{ ',' if not loop.last }}
        {% endfor %}
    from parsed
), classified as (
    select
        *,
        {% for raw, local, utc in timestamps %}
        {{ nyc311_timestamp_status(raw, local, '_' ~ utc) }} as {{ raw }}_time_status{{ ',' if not loop.last }}
        {% endfor %}
    from candidates
), resolved as (
    select
        * exclude ({% for raw, local, utc in timestamps %}_{{ utc }}{{ ',' if not loop.last }}{% endfor %}),
        {% for raw, local, utc in timestamps %}
        case when {{ raw }}_time_status = 'valid' then _{{ utc }}
             else null::timestamptz end as {{ utc }},
        ({{ raw }}_time_status = 'invalid_format') as {{ raw }}_parse_failed{{ ',' if not loop.last }}
        {% endfor %}
    from classified
)
select
    *,
    unique_key as request_bk,
    agency as agency_bk,
    open_data_channel_type as channel_type,
    'America/New_York' as source_timezone,
    (
        {{ nyc311_nonblank_text('unique_key') }} is null
        or {{ nyc311_nonblank_text('agency') }} is null
        or {{ nyc311_nonblank_text('complaint_type') }} is null
        or {{ nyc311_nonblank_text('status') }} is null
        or created_date_time_status = 'missing'
    ) as has_missing_required_fields,
    (
        {% for raw, local, utc in timestamps %}
        {{ raw }}_time_status in ('invalid_format', 'ambiguous_local_time', 'nonexistent_local_time')
        {{ 'or' if not loop.last }}
        {% endfor %}
    ) as has_date_quality_issue,
    (created_at_utc is not null and closed_at_utc is not null) as is_lifecycle_evaluable,
    case when created_at_utc is not null and closed_at_utc is not null
         then closed_at_utc < created_at_utc
         else null::boolean end as closed_before_created
from resolved
