{% macro mart_agency_population_expected() %}
    with evidence as ({{ mart_trend_evidence() }})
    select m.*, e.agency as observed_agency_bk,
           e.complaint_type as observed_complaint_type, e.borough as observed_borough,
           e.batch_id as dimension_batch_id, e.snapshot_at_utc as dimension_snapshot_at_utc,
           e.batch_id is not null as is_dimension_observed,
           {{ nyc311_nonblank_text('e.agency') }} is null as has_missing_agency,
           {{ nyc311_nonblank_text('e.complaint_type') }} is null as has_missing_problem,
           {{ nyc311_nonblank_text('e.borough') }} is null as has_missing_borough
    from {{ ref('mart_resolution_times') }} m
    left join evidence e on e.batch_id=m.batch_id and e.unique_key=m.request_bk
      and e.record_source=m.record_source and e.mode=m.mode
      and e.snapshot_at_utc=m.snapshot_at_utc
{% endmacro %}

{% macro mart_agency_preflight() %}
    with expected as ({{ mart_agency_population_expected() }}),
    actual as (select * from {{ ref('int_request_trend_population') }}),
    extra as (select * from actual except all select * from expected),
    absent as (select * from expected except all select * from actual)
    select case when exists (select 1 from extra) or exists (select 1 from absent)
        then error('Agency performance requires complete unique request population with consistent context, measures and provenance')
        else true end as agency_population_valid
{% endmacro %}
