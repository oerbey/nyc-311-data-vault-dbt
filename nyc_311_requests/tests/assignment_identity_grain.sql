select 'agency' as entity
from {{ ref('hub_agency') }}
group by record_source,agency_bk having count(*)<>1
union all
select 'problem'
from {{ ref('hub_problem') }}
group by record_source,agency_bk,complaint_type having count(*)<>1
union all
select 'agency_link'
from {{ ref('lnk_request_agency') }}
group by request_hk,agency_hk having count(*)<>1
union all
select 'problem_link'
from {{ ref('lnk_request_problem') }}
group by request_hk,problem_hk having count(*)<>1
