-- Run against the built synthetic demo database. These are retained samples.
-- Event dates use NYC local dates. Context is from the lifecycle capture.
select event_date_nyc, sum(created_request_count) as created,
       sum(closed_request_count) as closed
from marts.mart_request_trends
where record_source = 'synthetic_311' and mode = 'synthetic'
group by event_date_nyc order by event_date_nyc;

-- Eligible durations include zero. Invalid pairs remain in the denominator.
select count(*) as retained_requests, sum(duration_eligible_count) as eligible,
       sum(duration_ineligible_count) as ineligible,
       avg(resolution_hours) as mean_eligible_hours
from marts.mart_resolution_times
where record_source = 'synthetic_311' and mode = 'synthetic';

-- Counts at each instant cannot be summed as distinct requests across time.
select as_of_utc, status_class, sum(request_count) as requests
from marts.mart_historical_status
where record_source = 'synthetic_311' and mode = 'synthetic'
group by as_of_utc, status_class order by as_of_utc, status_class;

-- Explicit observed agency activations and deactivations, not source event times.
select r.request_bk, a.agency_bk, e.observed_from, e.active_flag
from raw_vault.sat_lnk_request_agency_effectivity e
join raw_vault.lnk_request_agency l on e.link_hk = l.link_hk
join raw_vault.hub_request r on e.request_hk = r.request_hk
join raw_vault.hub_agency a on l.agency_hk = a.agency_hk
where e.record_source = 'synthetic_311' and e.mode = 'synthetic'
order by r.request_bk, e.observed_from, e.active_flag;
