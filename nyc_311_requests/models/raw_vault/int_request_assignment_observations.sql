{{ config(materialized='view', schema='staging') }}

with flagged as (
    select
        batch_id, snapshot_at_utc, record_source, source_id, mode, requested_filter,
        request_bk, agency_bk, complaint_type, agency_name, descriptor,
        {{ dv_sha256(['record_source', 'request_bk'], 'request_hk') }} as request_hk,
        ({{ nyc311_nonblank_text('agency_bk') }} is null) as has_missing_agency,
        ({{ nyc311_nonblank_text('complaint_type') }} is null) as has_missing_problem
    from {{ ref('stg_311_request_snapshots') }}
), eligible as (
    select *,
        (not has_missing_agency) as is_agency_link_eligible,
        (not has_missing_agency and not has_missing_problem) as is_problem_link_eligible
    from flagged
), hashed as (
    select *,
        case when is_agency_link_eligible
            then {{ dv_sha256(['record_source', 'agency_bk'], 'agency_hk') }}
            else null::varchar end as agency_hk,
        case when is_problem_link_eligible
            then {{ dv_sha256(['record_source', 'agency_bk', 'complaint_type'], 'problem_hk') }}
            else null::varchar end as problem_hk,
        case when is_problem_link_eligible
            then 'dv2:v1:problem_bk:2:' || {{ dv_encode_text('agency_bk') }}
                 || {{ dv_encode_text('complaint_type') }}
            else null::varchar end as problem_bk
    from eligible
)
select *,
    case when is_agency_link_eligible
        then {{ dv_sha256(['request_hk', 'agency_hk'], 'lnk_request_agency') }}
        else null::varchar end as request_agency_link_hk,
    case when is_problem_link_eligible
        then {{ dv_sha256(['request_hk', 'problem_hk'], 'lnk_request_problem') }}
        else null::varchar end as request_problem_link_hk
from hashed
