select request_hk, snapshot_at_utc
from {{ ref('int_request_assignment_observations') }}
group by request_hk, snapshot_at_utc
having count(distinct agency_hk) filter (where is_agency_link_eligible) > 1
    or count(distinct problem_hk) filter (where is_problem_link_eligible) > 1
