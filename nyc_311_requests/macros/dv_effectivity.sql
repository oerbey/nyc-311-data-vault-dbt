{% macro dv_effectivity_events(kind) %}
with tied as (
    select request_hk, request_{{ kind }}_link_hk as link_hk,
           snapshot_at_utc as observed_from, batch_id, record_source, mode
    from {{ ref('int_request_assignment_observations') }}
    where is_{{ kind }}_link_eligible
    qualify row_number() over (
        partition by request_hk, snapshot_at_utc order by batch_id
    ) = 1
), sequenced as (
    select *, lag(link_hk) over (
        partition by request_hk order by observed_from
    ) as previous_link_hk
    from tied
), changes as (
    select * from sequenced where link_hk is distinct from previous_link_hk
), events as (
    select request_hk, link_hk, observed_from, true as active_flag,
           batch_id, record_source, mode from changes
    union all
    -- Only an explicit eligible replacement ends the previous relationship.
    select request_hk, previous_link_hk as link_hk, observed_from, false as active_flag,
           batch_id, record_source, mode from changes where previous_link_hk is not null
)
select *, {{ dv_sha256(["case when active_flag then 'true' else 'false' end"],
                      'request_' ~ kind ~ '_effectivity') }} as hashdiff
from events
{% endmacro %}

{% macro dv_effectivity_preflight() %}
    {% if flags.FULL_REFRESH %}
        {{ exceptions.raise_compiler_error('Raw Vault is insert-only; effectivity full-refresh is blocked. Rebuild separately.') }}
    {% endif %}
    select case when false then 0
    {% for kind in ['agency', 'problem'] %}
        {% set existing = none %}
        {% if execute %}
            {% set existing = adapter.get_relation(database=this.database, schema='raw_vault',
                identifier='sat_lnk_request_' ~ kind ~ '_effectivity') %}
        {% endif %}
        {% if existing is not none %}
        when exists (
            select 1 from {{ existing }} old
            where not exists (
                select 1 from {{ ref('int_request_' ~ kind ~ '_effectivity_events') }} expected
                where old.link_hk = expected.link_hk
                  and old.request_hk = expected.request_hk
                  and old.observed_from = expected.observed_from
                  and old.active_flag = expected.active_flag
                  and old.hashdiff = expected.hashdiff
                  and old.record_source = expected.record_source
                  and old.mode = expected.mode
            )
        ) then error('Existing assignment effectivity would require rewriting; preserve Vault and rebuild separately')
        {% endif %}
    {% endfor %}
    else 1 end as effectivity_history_valid
{% endmacro %}

{% macro dv_effectivity_parent_coverage(kind) %}
    select case when exists (
        select 1 from {{ ref('int_request_' ~ kind ~ '_effectivity_events') }} e
        left join {{ ref('lnk_request_' ~ kind) }} l on e.link_hk = l.link_hk
        where l.link_hk is null or l.request_hk is distinct from e.request_hk
           or l.record_source is distinct from e.record_source or l.mode is distinct from e.mode
    ) then error('Missing or inconsistent effectivity Link parents; build with + dependencies')
    else 1 end as effectivity_parents_covered
{% endmacro %}
