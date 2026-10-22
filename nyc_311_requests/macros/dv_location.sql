{% macro dv_location_attributes() %}
    {{ return(['borough', 'incident_zip', 'community_board']) }}
{% endmacro %}

{% macro dv_location_preflight() %}
    {% if flags.FULL_REFRESH %}
        {{ exceptions.raise_compiler_error('Raw Vault is insert-only; location full-refresh is blocked. Rebuild separately.') }}
    {% endif %}
    {% set existing = none %}
    {% if execute %}
        {% set existing = adapter.get_relation(database=this.database, schema='raw_vault',
                                               identifier='sat_request_location') %}
    {% endif %}
    select case
        when exists (
            select 1 from {{ ref('int_request_location_observations') }}
            where {{ nyc311_nonblank_text('request_bk') }} is null
               or {{ nyc311_nonblank_text('record_source') }} is null or snapshot_at_utc is null
        ) then error('Location requires request key, source namespace and capture time')
        when exists (
            select 1 from {{ ref('int_request_location_observations') }}
            group by request_hk, snapshot_at_utc having count(distinct hashdiff)>1
        ) then error('Conflicting location values at one capture time; location not changed')
        {% if existing is not none %}
        when exists (
            select 1 from {{ existing }} old where not exists (
                select 1 from {{ ref('int_request_location_changes') }} expected
                where old.request_hk=expected.request_hk
                  and old.snapshot_at_utc=expected.snapshot_at_utc
                  and old.hashdiff=expected.hashdiff
                  and old.record_source=expected.record_source and old.mode=expected.mode
                  {% for attribute in dv_location_attributes() %}
                  and old.{{ attribute }} is not distinct from expected.{{ attribute }}
                  {% endfor %}
            )
        ) then error('Existing location history would require rewriting; preserve Vault and rebuild separately')
        {% endif %}
        else 1 end as location_history_valid
{% endmacro %}

{% macro dv_location_hub_coverage() %}
    select case when exists (
        select 1 from {{ ref('int_request_location_changes') }} c
        left join {{ ref('hub_request') }} h on c.request_hk=h.request_hk
        where h.request_hk is null or h.record_source is distinct from c.record_source
           or h.mode is distinct from c.mode
    ) then error('Missing or inconsistent location Hub parents; build +sat_request_location')
    else 1 end as location_parent_covered
{% endmacro %}
