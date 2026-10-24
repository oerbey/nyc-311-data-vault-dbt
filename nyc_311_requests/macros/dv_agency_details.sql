{% macro dv_agency_details_preflight() %}
    {% if flags.FULL_REFRESH %}
        {{ exceptions.raise_compiler_error('Raw Vault is insert-only; agency-details full-refresh is blocked. Rebuild separately.') }}
    {% endif %}
    {% set existing = none %}
    {% if execute %}
        {% set existing = adapter.get_relation(database=this.database, schema='raw_vault',
                                               identifier='sat_agency_details') %}
    {% endif %}
    select case
        when exists (
            select 1 from {{ ref('int_agency_details_observations') }}
            where is_agency_details_eligible and (
                {{ nyc311_nonblank_text('request_bk') }} is null
                or {{ nyc311_nonblank_text('record_source') }} is null
                or agency_hk is null or snapshot_at_utc is null
            )
        ) then error('Agency details require agency key, request evidence, source namespace and capture time')
        when exists (
            select 1 from {{ ref('int_agency_details_observations') }}
            where is_agency_details_eligible
            group by agency_hk, snapshot_at_utc having count(distinct hashdiff)>1
        ) then error('Conflicting agency names at one agency/capture instant; agency details not changed')
        {% if existing is not none %}
        when exists (
            select 1 from {{ existing }} old where not exists (
                select 1 from {{ ref('int_agency_details_changes') }} expected
                where old.agency_hk=expected.agency_hk
                  and old.snapshot_at_utc=expected.snapshot_at_utc
                  and old.hashdiff=expected.hashdiff
                  and old.agency_name is not distinct from expected.agency_name
                  and old.record_source=expected.record_source and old.mode=expected.mode
            )
        ) then error('Existing agency-details history would require rewriting; preserve Vault and rebuild separately')
        {% endif %}
        else 1 end as agency_details_history_valid
{% endmacro %}

{% macro dv_agency_details_hub_coverage() %}
    select case when exists (
        select 1 from {{ ref('int_agency_details_observations') }} c
        left join {{ ref('hub_agency') }} h on c.agency_hk=h.agency_hk
        where c.is_agency_details_eligible and (
            h.agency_hk is null or h.agency_bk is distinct from c.agency_bk
            or h.record_source is distinct from c.record_source or h.mode is distinct from c.mode
        )
    ) then error('Missing or inconsistent agency-details Hub parents; build +sat_agency_details')
    else 1 end as agency_details_parent_covered
{% endmacro %}
