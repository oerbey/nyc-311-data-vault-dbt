with population as ({{ mart_agency_population_expected() }}),
expected as (
    select record_source, mode, observed_agency_bk, created_cohort_month_nyc,
           count(*) as retained_request_count,
           sum(is_lifecycle_observed::bigint)::bigint as lifecycle_observed_request_count,
           sum((not is_lifecycle_observed)::bigint)::bigint as unobserved_lifecycle_count,
           sum((created_event_date_nyc is not null)::bigint)::bigint as usable_creation_date_count,
           sum((closed_event_date_nyc is not null)::bigint)::bigint as usable_closure_date_count,
           count(*) filter (where closed_date_time_status='missing') as missing_closure_date_count,
           count(*) filter (where closed_date_time_status in ('invalid_format','ambiguous_local_time','nonexistent_local_time')) as unusable_closure_date_count,
           sum(is_duration_valid::bigint)::bigint as duration_eligible_count,
           sum((not is_duration_valid)::bigint)::bigint as duration_ineligible_count,
           count(*) filter (where closed_before_created=true) as closed_before_created_count,
           sum(resolution_hours) as total_resolution_hours,
           avg(resolution_hours) as mean_resolution_hours,
           quantile_cont(resolution_hours, 0.5) as median_resolution_hours,
           quantile_cont(resolution_hours, 0.9) as p90_resolution_hours,
           min(snapshot_at_utc) as min_selected_lifecycle_capture_at_utc,
           max(snapshot_at_utc) as max_selected_lifecycle_capture_at_utc
    from population group by all
), completed as (
    select *, 'lifecycle_capture'::varchar as dimension_basis,
           {{ nyc311_nonblank_text('observed_agency_bk') }} is null as has_missing_agency,
           created_cohort_month_nyc is not null as is_cohort_month_known,
           usable_closure_date_count::double/retained_request_count as reported_closure_date_share,
           duration_eligible_count::double/retained_request_count as duration_coverage_share
    from expected
), actual as (select * from {{ ref('mart_agency_performance') }}),
extra as (select * from actual except all select * from completed),
absent as (select * from completed except all select * from actual)
select record_source from extra
union all select record_source from absent
union all select record_source from actual
    group by record_source, mode, observed_agency_bk, created_cohort_month_nyc having count(*)<>1
union all select record_source from actual
where retained_request_count<=0
   or lifecycle_observed_request_count+unobserved_lifecycle_count<>retained_request_count
   or usable_closure_date_count+missing_closure_date_count+unusable_closure_date_count+unobserved_lifecycle_count<>retained_request_count
   or duration_eligible_count+duration_ineligible_count<>retained_request_count
   or duration_eligible_count>usable_closure_date_count or duration_eligible_count>usable_creation_date_count
   or reported_closure_date_share not between 0 and 1 or duration_coverage_share not between 0 and 1
   or duration_coverage_share>reported_closure_date_share
   or (duration_eligible_count=0 and (total_resolution_hours is not null or mean_resolution_hours is not null
                                    or median_resolution_hours is not null or p90_resolution_hours is not null))
   or (duration_eligible_count>0 and (total_resolution_hours<0 or mean_resolution_hours<0
                                   or median_resolution_hours<0 or p90_resolution_hours<median_resolution_hours))
