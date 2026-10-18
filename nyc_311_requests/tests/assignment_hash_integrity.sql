select agency_hk as invalid_hash from {{ ref('hub_agency') }}
where agency_hk <> {{ dv_sha256(['record_source','agency_bk'],'agency_hk') }}
union all
select problem_hk from {{ ref('hub_problem') }}
where problem_hk <> {{ dv_sha256(['record_source','agency_bk','complaint_type'],'problem_hk') }}
   or problem_bk <> 'dv2:v1:problem_bk:2:' || {{ dv_encode_text('agency_bk') }} || {{ dv_encode_text('complaint_type') }}
union all
select link_hk from {{ ref('lnk_request_agency') }}
where link_hk <> {{ dv_sha256(['request_hk','agency_hk'],'lnk_request_agency') }}
union all
select link_hk from {{ ref('lnk_request_problem') }}
where link_hk <> {{ dv_sha256(['request_hk','problem_hk'],'lnk_request_problem') }}
