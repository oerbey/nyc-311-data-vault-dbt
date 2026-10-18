{% macro dv_encode_text(expression) -%}
    case when {{ expression }} is null then 'N;'
    else 'S' || cast(octet_length(encode(cast({{ expression }} as varchar))) as varchar)
         || ':' || cast({{ expression }} as varchar) || ';' end
{%- endmacro %}

{% macro dv_sha256(expressions, domain) -%}
    {%- if expressions | length == 0 -%}
        {{ exceptions.raise_compiler_error('dv_sha256 requires at least one expression') }}
    {%- endif -%}
    sha256('dv2:v1:{{ domain | replace("'", "''") }}:{{ expressions | length }}:'
        {% for expression in expressions %} || {{ dv_encode_text(expression) }}{% endfor %})
{%- endmacro %}
