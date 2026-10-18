{% macro dv_assignment_preflight() %}
    {% if flags.FULL_REFRESH %}
        {{ exceptions.raise_compiler_error(
            'Raw Vault is insert-only; assignment --full-refresh is blocked. Rebuild separately.'
        ) }}
    {% endif %}
    select case
        when exists (
            select 1 from {{ ref('int_request_assignment_observations') }}
            where {{ nyc311_nonblank_text('request_bk') }} is null
               or {{ nyc311_nonblank_text('record_source') }} is null
               or snapshot_at_utc is null
        ) then error('Assignment observations require request key, source namespace and capture time')
        when exists (
            select 1 from {{ ref('int_request_assignment_observations') }}
            group by request_hk, snapshot_at_utc
            having count(distinct agency_hk) filter (where is_agency_link_eligible) > 1
                or count(distinct problem_hk) filter (where is_problem_link_eligible) > 1
        ) then error('Conflicting complete assignments at one capture time; assignment Vault not changed')
        else 1 end as assignments_valid
{% endmacro %}

{% macro dv_assignment_parent_coverage(kind) %}
    {% if kind not in ['agency', 'problem'] %}
        {{ exceptions.raise_compiler_error('Assignment parent kind must be agency or problem') }}
    {% endif %}
    select case when exists (
        select 1 from {{ ref('int_request_assignment_observations') }} as o
        left join {{ ref('hub_request') }} as r on o.request_hk = r.request_hk
        left join {{ ref('hub_' ~ kind) }} as p on o.{{ kind }}_hk = p.{{ kind }}_hk
        where o.is_{{ kind }}_link_eligible and (
            r.request_hk is null or p.{{ kind }}_hk is null
            or r.record_source is distinct from o.record_source
            or p.record_source is distinct from o.record_source
            or r.mode is distinct from o.mode or p.mode is distinct from o.mode
        )
    ) then error('Missing or inconsistent assignment Hub parents; build Links with + dependencies')
    else 1 end as assignment_parents_covered
{% endmacro %}
