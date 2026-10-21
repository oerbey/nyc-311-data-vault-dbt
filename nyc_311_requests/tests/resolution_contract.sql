with expected as (
    select * from {{ ref('int_request_resolution_changes') }}
), actual as (
    select * from {{ ref('sat_request_resolution') }}
), violations as (
    select a.request_hk from actual a
    where not exists (
        select 1 from expected e where a.request_hk=e.request_hk
          and a.snapshot_at_utc=e.snapshot_at_utc and a.hashdiff=e.hashdiff
          and a.record_source=e.record_source and a.mode=e.mode
          {% for attribute in dv_resolution_attributes() %}
          and a.{{ attribute }} is not distinct from e.{{ attribute }}
          {% endfor %}
    )
    union all
    select e.request_hk from expected e where not exists (
        select 1 from actual a where a.request_hk=e.request_hk and a.snapshot_at_utc=e.snapshot_at_utc
    )
    union all
    select request_hk from actual group by request_hk, snapshot_at_utc having count(*)<>1
    union all
    select a.request_hk from actual a
    where a.hashdiff <> {{ dv_sha256(['a.resolution_description', 'a.resolution_action_updated_date'], 'request_resolution') }}
       or not exists (
           -- Preserve original provenance even when a smaller identical tie arrives.
           select 1 from {{ ref('int_request_resolution_observations') }} o
           where a.batch_id=o.batch_id and a.request_hk=o.request_hk
             and a.snapshot_at_utc=o.snapshot_at_utc and a.record_source=o.record_source and a.mode=o.mode
             and a.hashdiff=o.hashdiff
       )
    union all
    select a.request_hk from actual a left join {{ ref('hub_request') }} h using(request_hk)
    where h.request_hk is null or a.record_source is distinct from h.record_source or a.mode is distinct from h.mode
)
select * from violations
