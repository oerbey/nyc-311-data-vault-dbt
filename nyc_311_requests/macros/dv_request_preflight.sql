{% macro dv_request_preflight() %}
    {% if flags.FULL_REFRESH %}
        {{ exceptions.raise_compiler_error(
            'Raw Vault is insert-only; --full-refresh is blocked. Rebuild in a separate database.'
        ) }}
    {% endif %}
    {% set existing_sat = none %}
    {% if execute %}
        {% set existing_sat = adapter.get_relation(
            database=this.database, schema='raw_vault', identifier='sat_request_status'
        ) %}
    {% endif %}
    select case
        when exists (
            select 1 from {{ ref('int_request_status_observations') }}
            where {{ nyc311_nonblank_text('request_bk') }} is null
               or {{ nyc311_nonblank_text('record_source') }} is null
               or snapshot_at_utc is null
        ) then error('Raw Vault requires a request key, source namespace and capture time')
        when exists (
            select 1 from {{ ref('int_request_status_observations') }}
            group by request_hk, snapshot_at_utc
            having count(distinct hashdiff) > 1
        ) then error('Conflicting request statuses at one capture time; Vault not changed')
        {% if existing_sat is not none %}
        when exists (
            select 1 from {{ existing_sat }} as old
            where not exists (
                select 1 from {{ ref('int_request_status_changes') }} as expected
                where old.request_hk = expected.request_hk
                  and old.snapshot_at_utc = expected.snapshot_at_utc
                  and old.hashdiff = expected.hashdiff
                  and old.status is not distinct from expected.status
                  and old.record_source = expected.record_source
            )
        ) then error('Existing status history would require rewriting; preserve Vault and rebuild separately')
        {% endif %}
        else 1 end as request_history_valid
{% endmacro %}

{% macro dv_request_hub_coverage() %}
    select case when exists (
        select 1 from {{ ref('int_request_status_changes') }} as c
        left join {{ ref('hub_request') }} as h on c.request_hk = h.request_hk
        where h.request_hk is null
    ) then error('Missing request Hub keys; build +sat_request_status to load dependencies')
    else 1 end as request_hub_covered
{% endmacro %}
