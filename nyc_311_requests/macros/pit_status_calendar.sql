{% macro pit_status_calendar() %}
    {# Explicit instants are normalized in Python before emitting SQL literals.
       Never interpret a date-only/naive timestamp in the session timezone. #}
    {% set instants = var('pit_status_as_of_instants', none) %}
    {% if instants is none %}
        select distinct snapshot_at_utc as as_of_utc from {{ ref('sat_request_status') }}
    {% else %}
        {% if instants is string or instants is mapping or instants is not sequence %}
            {{ exceptions.raise_compiler_error('pit_status_as_of_instants must be a list of timezone-aware ISO timestamps') }}
        {% endif %}
        {% if instants | length > 366 %}
            {{ exceptions.raise_compiler_error('Status PIT calendar exceeds 366 instants') }}
        {% endif %}
        {% set normalized = [] %}
        {% for value in instants %}
            {% if value is not string or not modules.re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})', value) %}
                {{ exceptions.raise_compiler_error('Status PIT requires timezone-aware ISO timestamps with seconds') }}
            {% endif %}
            {% set instant = modules.datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(modules.datetime.datetime.fromisoformat('2000-01-01T00:00:00+00:00').tzinfo).isoformat() %}
            {% if instant not in normalized %}{% do normalized.append(instant) %}{% endif %}
        {% endfor %}
        {% if normalized | length == 0 %}
            select null::timestamptz as as_of_utc where false
        {% else %}
            {% for instant in normalized %}
                select '{{ instant }}'::timestamptz as as_of_utc
                {% if not loop.last %}union all{% endif %}
            {% endfor %}
        {% endif %}
    {% endif %}
{% endmacro %}

{% macro pit_status_bounds() %}
    with calendar as ({{ pit_status_calendar() }}), sizes as (
        select (select count(*) from calendar) as instants,
               (select count(*) from {{ ref('hub_request') }}) as requests
    )
    select case
        when instants > 366 then error('Status PIT calendar exceeds 366 instants')
        when instants::hugeint * requests > 1000000 then error('Status PIT grid exceeds 1000000 rows')
        else true end as within_bounds
    from sizes
{% endmacro %}

{% macro pit_status_preflight() %}
    select case when exists (
        select 1 from {{ ref('hub_request') }}
        where request_hk is null or request_bk is null or trim(request_bk)=''
           or record_source is null or mode is null or load_dts_utc is null
           or request_hk is distinct from {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }}
    ) or exists (
        select 1 from {{ ref('hub_request') }} group by request_hk having count(*)<>1
    ) then error('Status PIT requires unique canonical request Hub identities and provenance')
    else true end as pit_input_valid
{% endmacro %}
