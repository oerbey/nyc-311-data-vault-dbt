{% for kind in ['agency','problem'] %}
{% if not loop.first %}union all{% endif %}
select l.link_hk
from {{ ref('lnk_request_' ~ kind) }} as l
left join {{ ref('hub_request') }} as r on l.request_hk=r.request_hk
left join {{ ref('hub_' ~ kind) }} as p on l.{{ kind }}_hk=p.{{ kind }}_hk
where r.request_hk is null or p.{{ kind }}_hk is null
   or l.record_source is distinct from r.record_source
   or l.record_source is distinct from p.record_source
   or l.mode is distinct from r.mode or l.mode is distinct from p.mode
{% endfor %}
