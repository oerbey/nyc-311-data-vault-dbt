{% macro nyc311_nonblank_text(expression) %}
    nullif(regexp_replace({{ expression }}, '^\s+|\s+$', '', 'g'), '')
{% endmacro %}

{% macro nyc311_timestamp_fields() %}
    {{ return([
        ('created_date', 'created_at_nyc', 'created_at_utc'),
        ('closed_date', 'closed_at_nyc', 'closed_at_utc'),
        ('due_date', 'due_at_nyc', 'due_at_utc'),
        ('resolution_action_updated_date', 'resolution_action_updated_at_nyc', 'resolution_action_updated_at_utc')
    ]) }}
{% endmacro %}

{% macro nyc311_parse_timestamp(expression) %}
    -- Source timestamps are floating NYC local times. Offset-bearing strings are
    -- flagged rather than interpreted with the connection's timezone.
    case
        when regexp_full_match(
            {{ nyc311_nonblank_text(expression) }},
            '[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?'
        ) then try_cast({{ nyc311_nonblank_text(expression) }} as timestamp)
        else null::timestamp
    end
{% endmacro %}

{% macro nyc311_timestamp_status(raw, local, candidate) %}
    case
        when {{ nyc311_nonblank_text(raw) }} is null then 'missing'
        when {{ local }} is null then 'invalid_format'
        when timezone('America/New_York', {{ candidate }}) <> {{ local }}
            then 'nonexistent_local_time'
        when timezone('America/New_York', {{ candidate }} - interval '1 hour') = {{ local }}
          or timezone('America/New_York', {{ candidate }} + interval '1 hour') = {{ local }}
            then 'ambiguous_local_time'
        else 'valid'
    end
{% endmacro %}
