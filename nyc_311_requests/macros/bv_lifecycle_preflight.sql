{% macro bv_lifecycle_preflight() %}
    {# Validate loaded evidence only; pending landing data is not metric input. #}
    {% set fields = nyc311_timestamp_fields()[:3] %}
    with parsed as (
        select *,
        {% for raw, local, utc in fields %}
            {{ nyc311_parse_timestamp(raw) }} as _{{ local }}{{ ',' if not loop.last }}
        {% endfor %}
        from {{ ref('sat_request_lifecycle') }}
    ), candidates as (
        select *,
        {% for raw, local, utc in fields %}
            timezone('America/New_York', _{{ local }}) as _{{ utc }}{{ ',' if not loop.last }}
        {% endfor %}
        from parsed
    ), classified as (
        select *,
        {% for raw, local, utc in fields %}
            {{ nyc311_timestamp_status(raw, '_' ~ local, '_' ~ utc) }} as _{{ raw }}_time_status{{ ',' if not loop.last }}
        {% endfor %}
        from candidates
    )
    select case
        when exists (
            select 1 from {{ ref('hub_request') }}
            where request_hk is null or {{ nyc311_nonblank_text('request_bk') }} is null
               or {{ nyc311_nonblank_text('record_source') }} is null
               or mode is null or load_dts_utc is null
               or request_hk is distinct from {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }}
        ) or exists (
            select 1 from {{ ref('hub_request') }} group by request_hk having count(*)<>1
        ) then error('Lifecycle metrics require unique canonical request Hub identities and provenance')
        when exists (
            select 1 from {{ ref('sat_request_lifecycle') }}
            where request_hk is null or snapshot_at_utc is null or hashdiff is null
               or batch_id is null or record_source is null or mode is null or load_dts_utc is null
        ) then error('Lifecycle metrics require non-null Raw Vault keys, capture and provenance')
        when exists (
            select 1 from {{ ref('sat_request_lifecycle') }}
            group by request_hk, snapshot_at_utc having count(*)<>1
        ) then error('Lifecycle metrics require unique Raw Vault request/capture versions')
        when exists (
            select 1 from {{ ref('sat_request_lifecycle') }}
            where hashdiff is distinct from {{ dv_sha256(['created_date', 'closed_date', 'due_date'], 'request_lifecycle') }}
        ) then error('Lifecycle metrics require canonical Raw Vault lifecycle hashes')
        when exists (
            select 1 from {{ ref('sat_request_lifecycle') }} s
            left join {{ ref('hub_request') }} h using(request_hk)
            where h.request_hk is null or s.record_source is distinct from h.record_source
               or s.mode is distinct from h.mode
        ) then error('Lifecycle metrics require consistent request Hub parents')
        when exists (
            select 1 from classified
            where source_timezone is distinct from 'America/New_York'
            {% for raw, local, utc in fields %}
               or {{ local }} is distinct from _{{ local }}
               or {{ raw }}_time_status is distinct from _{{ raw }}_time_status
               or {{ raw }}_parse_failed is distinct from (_{{ raw }}_time_status='invalid_format')
               or {{ utc }} is distinct from
                  (case when _{{ raw }}_time_status='valid' then _{{ utc }} else null::timestamptz end)
            {% endfor %}
               or is_lifecycle_evaluable is distinct from (created_at_utc is not null and closed_at_utc is not null)
               or closed_before_created is distinct from
                  (case when created_at_utc is not null and closed_at_utc is not null
                        then closed_at_utc<created_at_utc else null::boolean end)
               or has_lifecycle_date_quality_issue is distinct from
                  (created_date_time_status in ('invalid_format','ambiguous_local_time','nonexistent_local_time')
                   or closed_date_time_status in ('invalid_format','ambiguous_local_time','nonexistent_local_time')
                   or due_date_time_status in ('invalid_format','ambiguous_local_time','nonexistent_local_time'))
        ) then error('Lifecycle metrics require consistent stored timestamp interpretation and quality flags')
        else true end as lifecycle_metric_input_valid
{% endmacro %}
