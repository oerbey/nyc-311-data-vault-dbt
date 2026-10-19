{% macro dv_lifecycle_attributes() %}
    {{ return(['created_date', 'created_at_nyc', 'created_at_utc', 'created_date_time_status', 'created_date_parse_failed', 'closed_date', 'closed_at_nyc', 'closed_at_utc', 'closed_date_time_status', 'closed_date_parse_failed', 'due_date', 'due_at_nyc', 'due_at_utc', 'due_date_time_status', 'due_date_parse_failed', 'source_timezone', 'is_lifecycle_evaluable', 'closed_before_created', 'has_lifecycle_date_quality_issue']) }}
{% endmacro %}

{% macro dv_lifecycle_preflight() %}
    {% if flags.FULL_REFRESH %}
        {{ exceptions.raise_compiler_error('Raw Vault is insert-only; lifecycle full-refresh is blocked. Rebuild separately.') }}
    {% endif %}
    {% set existing = none %}
    {% if execute %}
        {% set existing = adapter.get_relation(database=this.database, schema='raw_vault',
                                               identifier='sat_request_lifecycle') %}
    {% endif %}
    select case
        when exists (
            select 1 from {{ ref('int_request_lifecycle_observations') }}
            where {{ nyc311_nonblank_text('request_bk') }} is null
               or {{ nyc311_nonblank_text('record_source') }} is null or snapshot_at_utc is null
        ) then error('Lifecycle requires request key, source namespace and capture time')
        when exists (
            select 1 from {{ ref('int_request_lifecycle_observations') }}
            group by request_hk, snapshot_at_utc having count(distinct hashdiff)>1
        ) then error('Conflicting lifecycle values at one capture time; lifecycle not changed')
        {% if existing is not none %}
        when exists (
            select 1 from {{ existing }} old where not exists (
                select 1 from {{ ref('int_request_lifecycle_changes') }} expected
                where old.request_hk=expected.request_hk
                  and old.snapshot_at_utc=expected.snapshot_at_utc
                  and old.hashdiff=expected.hashdiff
                  and old.record_source=expected.record_source and old.mode=expected.mode
                  {% for attribute in dv_lifecycle_attributes() %}
                  and old.{{ attribute }} is not distinct from expected.{{ attribute }}
                  {% endfor %}
            )
        ) then error('Existing lifecycle history would require rewriting; preserve Vault and rebuild separately')
        {% endif %}
        else 1 end as lifecycle_history_valid
{% endmacro %}

{% macro dv_lifecycle_hub_coverage() %}
    select case when exists (
        select 1 from {{ ref('int_request_lifecycle_changes') }} c
        left join {{ ref('hub_request') }} h on c.request_hk=h.request_hk
        where h.request_hk is null or h.record_source is distinct from c.record_source
           or h.mode is distinct from c.mode
    ) then error('Missing or inconsistent lifecycle Hub parents; build +sat_request_lifecycle')
    else 1 end as lifecycle_parent_covered
{% endmacro %}
