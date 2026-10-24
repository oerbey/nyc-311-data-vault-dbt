{% macro bv_request_status_preflight() %}
    select case
        when exists (
            select 1 from {{ ref('sat_request_status') }}
            where request_hk is null or snapshot_at_utc is null or hashdiff is null
               or batch_id is null or record_source is null or mode is null or load_dts_utc is null
        ) then error('Status intervals require non-null Raw Vault keys, capture time and provenance')
        when exists (
            select 1 from {{ ref('sat_request_status') }}
            group by request_hk, snapshot_at_utc having count(*)>1
        ) then error('Status intervals require unique Raw Vault request/capture versions')
        when exists (
            select 1 from {{ ref('sat_request_status') }} s
            where s.hashdiff is distinct from {{ dv_sha256(['s.status'], 'request_status') }}
        ) then error('Status intervals require canonical Raw Vault status hashes')
        when exists (
            select 1 from {{ ref('sat_request_status') }} s
            left join {{ ref('hub_request') }} h on s.request_hk=h.request_hk
            where h.request_hk is null or s.record_source is distinct from h.record_source
               or s.mode is distinct from h.mode
               or h.request_hk is distinct from {{ dv_sha256(['h.record_source', 'h.request_bk'], 'request_hk') }}
        ) then error('Status intervals require consistent Raw Vault request Hub parents')
        else 1 end as status_interval_input_valid
{% endmacro %}
