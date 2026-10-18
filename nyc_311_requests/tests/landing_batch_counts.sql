-- Successful batches must match committed rows and, for live captures, both API counts.
with observations as (
    select batch_id, count(*) as observed_rows
    from {{ source('landing', 'request_snapshots') }}
    group by batch_id
)
select b.batch_id
from {{ source('landing', 'ingestion_batch') }} as b
left join observations as r on b.batch_id = r.batch_id
where b.status = 'success'
  and (
      b.accepted_rows <> coalesce(r.observed_rows, 0)
      or b.fetched_rows <> b.accepted_rows
      or b.rejected_rows <> 0
      or (b.mode = 'live' and (
          b.expected_rows_before is null
          or b.expected_rows_after is null
          or b.expected_rows_before <> b.accepted_rows
          or b.expected_rows_after <> b.accepted_rows
          or b.page_count < 1
      ))
  )
