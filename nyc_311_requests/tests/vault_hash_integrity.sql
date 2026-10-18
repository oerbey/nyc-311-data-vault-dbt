select request_hk from {{ ref('hub_request') }}
where request_hk <> {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }}
union all
select request_hk from {{ ref('sat_request_status') }}
where hashdiff <> {{ dv_sha256(['status'], 'request_status') }}
