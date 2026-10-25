{% macro mart_trend_evidence() %}
    select r.*, b.mode
    from {{ source('landing', 'request_snapshots') }} r
    join {{ source('landing', 'ingestion_batch') }} b
      on r.batch_id=b.batch_id and r.record_source=b.record_source
     and r.snapshot_at_utc=b.snapshot_at_utc
    where b.status='success'
{% endmacro %}

{% macro mart_trends_preflight() %}
    with evidence as ({{ mart_trend_evidence() }}), metric as (
        select * from {{ ref('mart_resolution_times') }}
    ), original as (
        select * exclude (created_event_date_nyc, closed_event_date_nyc, created_cohort_month_nyc,
                          request_count, duration_eligible_count, duration_ineligible_count,
                          created_event_eligible_count, closed_event_eligible_count)
        from metric
    ), extra as (
        select * from original except all select * from {{ ref('bv_request_resolution_metrics') }}
    ), absent as (
        select * from {{ ref('bv_request_resolution_metrics') }} except all select * from original
    )
    select case
        when exists (select 1 from extra) or exists (select 1 from absent)
        then error('Request trends require complete unique resolution mart projection')
        when exists (
            select 1 from metric
            where created_event_date_nyc is distinct from
                  (case when created_date_time_status='valid' then created_at_nyc::date else null::date end)
               or closed_event_date_nyc is distinct from
                  (case when closed_date_time_status='valid' then closed_at_nyc::date else null::date end)
               or created_cohort_month_nyc is distinct from date_trunc('month', created_event_date_nyc)::date
               or request_count is distinct from 1::bigint
               or duration_eligible_count is distinct from is_duration_valid::bigint
               or duration_ineligible_count is distinct from (not is_duration_valid)::bigint
               or created_event_eligible_count is distinct from (created_event_date_nyc is not null)::bigint
               or closed_event_eligible_count is distinct from (closed_event_date_nyc is not null)::bigint
        ) then error('Request trends require consistent resolution mart dates and population counters')
        when exists (
            select 1 from metric m
            where m.is_lifecycle_observed and (
                select count(*) from evidence e
                where e.batch_id=m.batch_id and e.unique_key=m.request_bk
                  and e.record_source=m.record_source and e.mode=m.mode
                  and e.snapshot_at_utc=m.snapshot_at_utc
                  and e.created_date is not distinct from m.created_date
                  and e.closed_date is not distinct from m.closed_date
                  and e.due_date is not distinct from m.due_date
            )<>1
        ) then error('Request trends require exactly one matching successful lifecycle landing observation')
        else true end as request_trend_input_valid
{% endmacro %}
