with expected as (select * from {{ ref('bv_request_resolution_metrics') }}),
actual as (select * from {{ ref('mart_resolution_times') }}),
original as (
    select * exclude (created_event_date_nyc, closed_event_date_nyc, created_cohort_month_nyc,
                      request_count, duration_eligible_count, duration_ineligible_count,
                      created_event_eligible_count, closed_event_eligible_count)
    from actual
), extra as (select * from original except all select * from expected),
absent as (select * from expected except all select * from original)
select request_hk from extra
union all select request_hk from absent
union all select request_hk from actual group by request_hk having count(*)<>1
union all select request_hk from actual
where created_event_date_nyc is distinct from
      (case when created_date_time_status='valid' then timezone('America/New_York', created_at_utc)::date else null::date end)
   or closed_event_date_nyc is distinct from
      (case when closed_date_time_status='valid' then timezone('America/New_York', closed_at_utc)::date else null::date end)
   or created_cohort_month_nyc is distinct from date_trunc('month', created_event_date_nyc)::date
   or request_count<>1 or duration_eligible_count is distinct from is_duration_valid::bigint
   or duration_ineligible_count<>1-duration_eligible_count
   or created_event_eligible_count is distinct from (created_event_date_nyc is not null)::bigint
   or closed_event_eligible_count is distinct from (closed_event_date_nyc is not null)::bigint
