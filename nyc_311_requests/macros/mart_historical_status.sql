{% macro historical_status_class(observed, status) %}
case when not {{ observed }} then 'unobserved'
     when {{ status }} is null then 'observed_null'
     when trim({{ status }}, E' \t\n\r\v\f')='' then 'observed_blank'
     when {{ status }}='Open' then 'open'
     when {{ status }}='In Progress' then 'in_progress'
     when {{ status }}='Closed' then 'closed'
     else 'other' end
{% endmacro %}

{% macro historical_status_input_preflight() %}
with calendar as ({{ pit_status_calendar() }}), expected_pit as (
    select h.request_hk,c.as_of_utc,h.record_source,h.mode,
           s.request_hk is not null as is_status_observed,
           s.snapshot_at_utc as status_snapshot_at_utc,s.load_dts_utc as status_load_dts_utc,
           s.hashdiff as status_hashdiff,s.batch_id as status_batch_id,s.status
    from {{ ref('hub_request') }} h cross join calendar c
    left join {{ ref('sat_request_status') }} s
      on s.request_hk=h.request_hk and s.snapshot_at_utc<=c.as_of_utc
    qualify row_number() over (partition by h.request_hk,c.as_of_utc
                               order by s.snapshot_at_utc desc)=1
), actual_pit as (select * from {{ ref('pit_request_status_asof') }}),
extra_pit as (select * from actual_pit except all select * from expected_pit),
absent_pit as (select * from expected_pit except all select * from actual_pit),
activations as (
    select *, lead(link_hk) over w as next_link,
           lead(observed_from) over w as next_capture,
           lead(batch_id) over w as next_batch
    from {{ ref('sat_lnk_request_agency_effectivity') }} where active_flag
    window w as (partition by request_hk order by observed_from)
), expected_ends as (
    select link_hk,request_hk,next_capture as observed_from,next_batch as batch_id,
           record_source,mode from activations where next_capture is not null
), actual_ends as (
    select link_hk,request_hk,observed_from,batch_id,record_source,mode
    from {{ ref('sat_lnk_request_agency_effectivity') }} where not active_flag
), extra_ends as (select * from actual_ends except all select * from expected_ends),
absent_ends as (select * from expected_ends except all select * from actual_ends)
select case
when exists (select 1 from extra_pit) or exists (select 1 from absent_pit)
then error('Historical status requires a complete canonical PIT matching the configured calendar; rebuild + dependencies with matching vars')
when exists (
    select 1 from {{ ref('hub_agency') }} a
    where agency_hk is null or agency_bk is null or trim(agency_bk)=''
       or record_source is null or mode is null or load_dts_utc is null
       or agency_hk is distinct from {{ dv_sha256(['record_source','agency_bk'],'agency_hk') }}
) or exists (select 1 from {{ ref('hub_agency') }} group by agency_hk having count(*)<>1)
then error('Historical status requires unique canonical agency Hubs')
when exists (
    select 1 from {{ ref('lnk_request_agency') }} l
    left join {{ ref('hub_request') }} h on h.request_hk=l.request_hk
    left join {{ ref('hub_agency') }} a on a.agency_hk=l.agency_hk
    where h.request_hk is null or a.agency_hk is null or l.load_dts_utc is null
       or l.link_hk is distinct from {{ dv_sha256(['l.request_hk','l.agency_hk'],'lnk_request_agency') }}
       or l.record_source is distinct from h.record_source or l.mode is distinct from h.mode
       or l.record_source is distinct from a.record_source or l.mode is distinct from a.mode
) or exists (select 1 from {{ ref('lnk_request_agency') }} group by link_hk having count(*)<>1)
then error('Historical status requires unique canonical agency Links and consistent parents')
when exists (
    select 1 from {{ ref('sat_lnk_request_agency_effectivity') }} e
    left join {{ ref('lnk_request_agency') }} l on l.link_hk=e.link_hk
    where l.link_hk is null or e.request_hk is distinct from l.request_hk
       or e.record_source is distinct from l.record_source or e.mode is distinct from l.mode
       or e.active_flag is null or e.observed_from is null or e.batch_id is null
       or e.load_dts_utc is null
       or e.hashdiff is distinct from {{ dv_sha256(["case when e.active_flag then 'true' else 'false' end"],'request_agency_effectivity') }}
) or exists (
    select 1 from {{ ref('sat_lnk_request_agency_effectivity') }}
    group by link_hk,observed_from having count(*)<>1
) or exists (
    select 1 from activations group by request_hk,observed_from having count(*)<>1
) or exists (select 1 from activations where link_hk=next_link)
  or exists (select 1 from extra_ends) or exists (select 1 from absent_ends)
then error('Historical status requires consistent loaded agency effectivity, hashes and replacement boundaries')
else true end as historical_status_inputs_valid
{% endmacro %}

{% macro historical_status_population() %}
with activations as (
    select *, lead(observed_from) over (partition by request_hk order by observed_from) as next_capture
    from {{ ref('sat_lnk_request_agency_effectivity') }} where active_flag
)
select p.*,h.request_bk,e.link_hk as agency_link_hk,l.agency_hk,a.agency_bk,
       e.link_hk is not null as is_agency_observed,
       e.observed_from as agency_observed_from,e.batch_id as agency_batch_id,
       e.hashdiff as agency_effectivity_hashdiff,e.load_dts_utc as agency_load_dts_utc,
       {{ historical_status_class('p.is_status_observed','p.status') }} as status_class,
       'observed_asof'::varchar as dimension_basis
from {{ ref('pit_request_status_asof') }} p
join {{ ref('hub_request') }} h on h.request_hk=p.request_hk
left join activations e on e.request_hk=p.request_hk and e.observed_from<=p.as_of_utc
  and (e.next_capture is null or p.as_of_utc<e.next_capture)
left join {{ ref('lnk_request_agency') }} l on l.link_hk=e.link_hk
left join {{ ref('hub_agency') }} a on a.agency_hk=l.agency_hk
{% endmacro %}

{% macro historical_status_population_preflight() %}
with expected as ({{ historical_status_population() }}),
actual as (select * from {{ ref('int_request_historical_status_population') }}),
extra as (select * from actual except all select * from expected),
absent as (select * from expected except all select * from actual)
select case when exists (select 1 from extra) or exists (select 1 from absent)
then error('Historical status requires complete unique as-of population and original lineage')
else true end as historical_status_population_valid
{% endmacro %}
