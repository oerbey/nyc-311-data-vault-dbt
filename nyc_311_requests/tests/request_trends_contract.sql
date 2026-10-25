with p as (select * from {{ ref('int_request_trend_population') }}),
events as (select * from {{ ref('int_request_trend_events') }}),
expected_events as (
    {% for kind in ['created', 'closed'] %}
    select request_hk, request_bk, record_source, mode, '{{ kind }}'::varchar as event_type,
           {{ kind }}_event_date_nyc as event_date_nyc,
           observed_agency_bk, observed_complaint_type, observed_borough,
           dimension_batch_id, dimension_snapshot_at_utc,
           snapshot_at_utc, batch_id, hashdiff, load_dts_utc, is_duration_valid, closed_before_created
    from p where {{ kind }}_event_date_nyc is not null
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
), extra_events as (select * from events except all select * from expected_events),
absent_events as (select * from expected_events except all select * from events),
expected as (
    select record_source, mode, event_date_nyc, 'lifecycle_capture'::varchar as dimension_basis,
           observed_agency_bk, observed_complaint_type, observed_borough,
           sum((event_type='created')::bigint)::bigint as created_request_count,
           sum((event_type='closed')::bigint)::bigint as closed_request_count
    from expected_events group by all
), actual as (select * from {{ ref('mart_request_trends') }}),
extra as (select * from actual except all select * from expected),
absent as (select * from expected except all select * from actual),
evidence as ({{ mart_trend_evidence() }}),
original as (
    select * exclude (observed_agency_bk, observed_complaint_type, observed_borough,
                      dimension_batch_id, dimension_snapshot_at_utc, is_dimension_observed,
                      has_missing_agency, has_missing_problem, has_missing_borough) from p
), extra_population as (select * from original except all select * from {{ ref('mart_resolution_times') }}),
absent_population as (select * from {{ ref('mart_resolution_times') }} except all select * from original)
select record_source from extra
union all select record_source from absent
union all select record_source from extra_events
union all select record_source from absent_events
union all select record_source from extra_population
union all select record_source from absent_population
union all select record_source from p group by record_source, request_hk having count(*)<>1
union all select record_source from events group by record_source, request_hk, event_type having count(*)<>1
union all select record_source from actual
    group by record_source, mode, event_date_nyc, observed_agency_bk, observed_complaint_type, observed_borough
    having count(*)<>1
union all select record_source from actual
    where created_request_count<0 or closed_request_count<0 or created_request_count+closed_request_count=0
union all select p.record_source from p
    left join evidence e on e.batch_id=p.batch_id and e.unique_key=p.request_bk
    where p.is_dimension_observed is distinct from (e.batch_id is not null)
       or p.observed_agency_bk is distinct from e.agency
       or p.observed_complaint_type is distinct from e.complaint_type
       or p.observed_borough is distinct from e.borough
       or p.dimension_batch_id is distinct from e.batch_id
       or p.dimension_snapshot_at_utc is distinct from e.snapshot_at_utc
       or p.has_missing_agency is distinct from ({{ nyc311_nonblank_text('e.agency') }} is null)
       or p.has_missing_problem is distinct from ({{ nyc311_nonblank_text('e.complaint_type') }} is null)
       or p.has_missing_borough is distinct from ({{ nyc311_nonblank_text('e.borough') }} is null)
