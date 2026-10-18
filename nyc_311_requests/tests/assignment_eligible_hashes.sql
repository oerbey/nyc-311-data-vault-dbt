select batch_id,request_bk
from {{ ref('int_request_assignment_observations') }}
where is_agency_link_eligible <> (not has_missing_agency)
   or is_problem_link_eligible <> (not has_missing_agency and not has_missing_problem)
   or (is_agency_link_eligible and (agency_hk is null or request_agency_link_hk is null))
   or (not is_agency_link_eligible and (agency_hk is not null or request_agency_link_hk is not null))
   or (is_problem_link_eligible and (problem_hk is null or problem_bk is null or request_problem_link_hk is null))
   or (not is_problem_link_eligible and (problem_hk is not null or problem_bk is not null or request_problem_link_hk is not null))
