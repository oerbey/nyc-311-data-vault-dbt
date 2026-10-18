{% macro generate_schema_name(custom_schema_name, node) -%}
    {# This local warehouse uses the architecture's literal schema names.
       Use separate database files for independent developer environments. #}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
