{% for kind in ['agency','problem'] %}
{% if not loop.first %}union all{% endif %}
select '{{ kind }}_hub' as entity, coalesce(o.{{ kind }}_hk,h.{{ kind }}_hk) as hash_key
from (
    select distinct {{ kind }}_hk,record_source,mode{% if kind=='agency' %},agency_bk{% else %},agency_bk,complaint_type,problem_bk{% endif %}
    from {{ ref('int_request_assignment_observations') }} where is_{{ kind }}_link_eligible
) as o
full outer join {{ ref('hub_' ~ kind) }} as h on o.{{ kind }}_hk=h.{{ kind }}_hk
where o.{{ kind }}_hk is null or h.{{ kind }}_hk is null
   or o.record_source is distinct from h.record_source or o.mode is distinct from h.mode
   or o.agency_bk is distinct from h.agency_bk
{% if kind=='problem' %}
   or o.complaint_type is distinct from h.complaint_type or o.problem_bk is distinct from h.problem_bk
{% endif %}
union all
select '{{ kind }}_link',coalesce(o.request_{{ kind }}_link_hk,l.link_hk)
from (
    select distinct request_{{ kind }}_link_hk,request_hk,{{ kind }}_hk,record_source,mode
    from {{ ref('int_request_assignment_observations') }} where is_{{ kind }}_link_eligible
) as o
full outer join {{ ref('lnk_request_' ~ kind) }} as l on o.request_{{ kind }}_link_hk=l.link_hk
where o.request_{{ kind }}_link_hk is null or l.link_hk is null
   or o.request_hk is distinct from l.request_hk or o.{{ kind }}_hk is distinct from l.{{ kind }}_hk
   or o.record_source is distinct from l.record_source or o.mode is distinct from l.mode
{% endfor %}
