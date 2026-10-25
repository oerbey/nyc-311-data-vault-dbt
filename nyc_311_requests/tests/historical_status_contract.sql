with expected_population as ({{ historical_status_population() }}),
actual_population as (select * from {{ ref('int_request_historical_status_population') }}),
extra as (select * from actual_population except all select * from expected_population),
absent as (select * from expected_population except all select * from actual_population),
expected as (
    select as_of_utc,record_source,mode,is_agency_observed,agency_hk,agency_bk,
           is_status_observed,status,status_class,dimension_basis,
           count(*) as request_count,
           count(*) filter (where is_status_observed) as status_observed_request_count,
           count(*) filter (where not is_status_observed) as status_unknown_request_count,
           count(*) filter (where status_class='observed_null') as status_null_request_count,
           count(*) filter (where status_class='observed_blank') as status_blank_request_count,
           min(status_snapshot_at_utc) as min_selected_status_capture_at_utc,
           max(status_snapshot_at_utc) as max_selected_status_capture_at_utc,
           min(agency_observed_from) as min_selected_agency_capture_at_utc,
           max(agency_observed_from) as max_selected_agency_capture_at_utc
    from expected_population group by all
), actual as (select * from {{ ref('mart_historical_status') }}),
extra_groups as (select * from actual except all select * from expected),
absent_groups as (select * from expected except all select * from actual)
select request_hk as issue from extra
union all select request_hk from absent
union all select record_source from extra_groups
union all select record_source from absent_groups
union all select request_hk from actual_population
 group by request_hk,as_of_utc having count(*)<>1
union all select record_source from actual where request_count<=0
 or request_count<>status_observed_request_count+status_unknown_request_count
 or status_null_request_count+status_blank_request_count>status_observed_request_count
