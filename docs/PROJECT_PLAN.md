# NYC 311 Historical Service Analytics — Project Plan

**Status:** Landing/staging, required M3 Raw Vault models/checks, and required M4 Business Vault models/temporal checks implemented; M5 resolution-time mart implemented; remaining marts and dashboard are pending.
**Updated:** 2026-10-24

**Owner:** Portfolio repository `oerbey/nyc-311-data-vault-dbt`  
**Read first:** [../AGENTS.md](../AGENTS.md) · [DATA_VAULT_ARCHITECTURE.md](DATA_VAULT_ARCHITECTURE.md) · [SOURCE_DATA_DICTIONARY.md](SOURCE_DATA_DICTIONARY.md) · [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md)

## 1. Goal

Develop an end-to-end **Python + DuckDB + dbt Core + Data Vault 2.0** project that ingests NYC 311 service requests, preserves **observed changes** in operational records, supports time-series analysis of request creation and closure events, and demonstrates **snapshot-time as-of reporting** with tests.

**Portfolio story:** "How do NYC 311 requests flow through agencies, and how does the observed backlog and resolution performance evolve over time?"

**Non-negotiable distinction:** Historical `created_date` and `closed_date` support historical EVENT analysis immediately. The NYC dataset's `status` is the **current reported status**, not an audit trail of past status transitions. Complete historical status as-of snapshots only begin **after this pipeline starts taking snapshots**. Never claim a retrospective daily 2020–2025 status ledger from one 2026 export.

## 2. Source and working scope

- Primary source: NYC Open Data — 311 Service Requests from 2020 to Present, dataset ID **`erm2-nwe9`**.
- Dataset: https://data.cityofnewyork.us/d/erm2-nwe9
- Socrata API: https://data.cityofnewyork.us/resource/erm2-nwe9.json
- Provided data dictionary: `311_ServiceRequest_2020-present_DataDictionary_Updated_2025.xlsx` (user-provided reference; *not* automatically present in this repository).
- Publisher FAQ: https://www.nyc.gov/opendata/get-started/FAQs (NYC says there are generally no restrictions on the use of Open Data; review dataset-specific details and provide attribution).
- API field names remain `complaint_type` and `descriptor`, even though UI labels changed to "Problem" and "Problem Detail" in December 2025: https://www.nyc.gov/opendata/news/all-news/311-Service-Requests-Updates

**Initial cohort (configurable)**: requests **created 2026-01-01 through 2026-06-30**, with agencies **DEP** and **DSNY**, across NYC. Extract counts first; reduce the window if data volume is too large. The cohort is a *development sample*, not all requests. All data must be labeled as scoped, never citywide complete when filtered. For capturing *future* state changes, re-poll the **same source cohort** daily, including requests originally created earlier than the current extraction day; filtering only to newly created records misses updates.

**Privacy minimization:** Default extraction includes borough/ZIP/community board, *not* detailed street addresses, person-identifying text, raw latitude/longitude, or full location geometry. Keep analytic scope to public-service aggregates.

## 3. Stack and target architecture

- Python **3.11+** for ingestion, validation and snapshot audit.
- Socrata JSON API / `httpx`; optional app token from `SOCRATA_APP_TOKEN` env var.
- DuckDB local file, schema-separated landing / staging / raw_vault / business_vault / marts.
- dbt Core + `dbt-duckdb`; SQL models, deterministic SHA-256 hash keys and hashdiffs, incremental tables, dbt data/unit tests.
- pytest for ingestion and temporal edge cases; Ruff for Python lint.
- Streamlit for dashboard (stretch after tested marts); GitHub Actions for deterministic synthetic CI.

```text
NYC 311 API  ────────┐
                     ├──> Ingestion audit + landing snapshots --> dbt staging
Synthetic demo data ─┘                                          |
                  Raw Vault: hubs / links / change Satellites / link effectivity
                                                                 |
                       Business Vault: observed status intervals + as-of PIT
                                                                 |
               Marts: requests over time / resolution time / agency KPIs /
                            snapshot-observed backlog
                                                                 |
                        Streamlit dashboard / documentation
```

**Ingestion responsibility:** Python records batch/run and capture time; dbt does transformations and builds Vault/marts. The demo/CI workflow must operate without any network access or application token.

## 4. Timestamp semantics (must remain distinct)

| Time | Source/ownership | Interpretation |
|---|---|---|
| `created_date` | NYC source | Service request creation event |
| `closed_date` | NYC source | Agency's recorded closure event; may be NULL |
| `due_date` | NYC source | Expected **update** date, not a universal closure SLA |
| `resolution_action_updated_date` | NYC source | Last reported resolution-action update; **not** an authoritative status-change event log |
| `snapshot_at_utc` | Ingestion pipeline | When source response was captured; our observed-state timeline |
| `load_dts_utc` | Warehouse pipeline | First time a Vault version was loaded |
| `record_source` | Ingestion pipeline | e.g. `NYC311:erm2-nwe9` or `synthetic_311` |

Socrata "floating timestamp" fields contain no embedded offset. Explicitly document the assumption when interpreting source timestamps as **America/New_York local time** and convert to UTC for duration measures; test daylight-saving cases. Do not label source timestamps UTC without conversion. Observed status applies from the first snapshot that captured the value, **not necessarily when the City changed it**.

## 5. Landing and incremental ingest contract

Proposed landing tables:

**`landing.ingestion_batch`**
`batch_id` PK, source_id, mode (synthetic/live), requested_filter, snapshot_at_utc, started_at_utc, finished_at_utc, status, page_count, fetched_rows, accepted_rows, rejected_rows, checksum, failure_message.

**`landing.request_snapshots`** — immutable snapshot observations of one source request:
`batch_id`, `snapshot_at_utc`, `unique_key`, `created_date`, `closed_date`, `agency`, `agency_name`, `complaint_type`, `descriptor`, `status`, `due_date`, `resolution_action_updated_date`, `resolution_description`, `borough`, `incident_zip`, `community_board`, `open_data_channel_type`, `record_source`.
Primary ingestion uniqueness = (`batch_id`, `unique_key`). Reject duplicate conflicting source keys within a batch. Record extracted filters/window alongside each batch.

**API query example** (API name, not UI label):
```text
GET https://data.cityofnewyork.us/resource/erm2-nwe9.json
$select=unique_key,created_date,closed_date,agency,agency_name,complaint_type,
        descriptor,status,due_date,resolution_action_updated_date,
        resolution_description,borough,incident_zip,community_board,
        open_data_channel_type
$where=created_date >= '2026-01-01T00:00:00'
  AND created_date < '2026-07-01T00:00:00'
  AND agency IN ('DEP','DSNY')
$order=unique_key
$limit=5000
$offset=0
```

Use properly URL-encoded parameters; pagination with stable ordering, retries/backoff, clear timeouts and rate-limit handling. Because the live source can change during extraction, a multi-page pull is **not a transactional point-in-time snapshot**. Record start/end capture times; reconcile expected counts and duplicate keys; retry or restart inconsistent batches. No universal reliable `last_modified_at` exists in the 311 field contract: **do not** treat `resolution_action_updated_date` as a complete change-data-capture cursor.

For the MVP, **re-query the complete bounded cohort** on each daily run. Use the full landing snapshot to detect real changes; optionally optimize later with reconciliation rules. Avoid resetting `load_dts` on a no-op replay.

**Repeatability:** the identical batch replay is idempotent, and daily unchanged request rows should not create new Raw Vault status versions. A new status that equals an older *non-adjacent* status (open→closed→open) **is** a new version.

### Implemented offline landing decisions (2026-10-18)

The Python loader supplies three deterministic fictional batches to the two landing tables. Source values remain text/null, including raw source timestamps; no event-time conversion or Vault load timestamp is assigned. Synthetic observations use `synthetic_311` provenance and no detailed addresses or coordinates. The audit table additionally stores `record_source` so replay checks include provenance. All duplicate keys within a batch are rejected, including identical duplicates. Unknown fields and non-text values are rejected instead of silently dropped or coerced.

A running audit is persisted before loading; snapshot inserts and the success audit update are atomic. Failed validation/insertion leaves a failed audit and no committed snapshots. Accepted rows count committed rows; rejected rows count structurally invalid input records. Invalid batch metadata is rejected before audit creation. Empty supplied batches are valid zero-row observations; live extraction completeness checks are still pending. Successful identical replay is a no-op based on canonical source checksum plus source/filter/capture metadata. Failed or interrupted IDs require new IDs for retry; automatic checkpoint recovery is deferred. Fixed fixture batch IDs/timestamps make the demo repeatable, while actual audit start/end times record real loader execution.

Only the offline landing portion of M2 is complete. Live pagination, reconciliation, staging, and Vault logic remain pending. No historical/source timestamp semantics or Hub/Link grains have changed.

### Implemented live landing decisions (2026-10-18)

The live adapter validates the 15 landing fields/types against Socrata metadata before reading records. Defaults retain the January–June 2026 DEP/DSNY cohort, with configurable dates/agencies, 5,000-row pages and a 500,000-row guard. Count-only preflight is read-only. A complete one-day verification captured 515 rows; the full default cohort was counted (302,762 rows) but not downloaded. Requests preserve raw source strings and exclude detailed addresses/coordinates.

Live batches begin auditing before network access, checkpoint diagnostics after successful page responses, and reuse the offline loader's atomic snapshot writer. Two nullable audit columns, `expected_rows_before` and `expected_rows_after`, are added automatically to existing databases. Both counts must equal fetched rows before success. Page count includes the empty terminal response, excluding metadata/count requests and retries. A new UUID identifies every live observation; used live IDs are rejected before networking and cannot be resumed. Offline fixture replay remains idempotent.

Capture start is `started_at_utc`, capture/reconciliation end is `snapshot_at_utc`, and publication end is `finished_at_utc`; running batches have a provisional snapshot timestamp. Failed extracts retain no published snapshots. Count agreement and duplicate checks do not make a multi-page pull transactional or detect every same-count source change. Agency-filtered membership may change; disappearance cannot establish deletion or a new assignment outside the selected agencies. Scheduling, tracked-ID follow-up, resumable extraction, staging and Vault loading remain later work. No Hub/Link grain or source-event semantics changed.

### Implemented staging and timezone policy (2026-10-18)

`staging.stg_311_request_snapshots` retains every successful `(batch_id, unique_key)` observation and all raw source strings. It includes batch mode/source/filter metadata so live and synthetic populations remain explicitly selectable. Raw keys are exposed as text `request_bk` / `agency_bk`; no key normalization or hashing is applied yet. The dbt schema macro uses literal custom schema names; separate database files isolate developer environments.

Source dates are interpreted as **America/New_York** local wall-clock values. Parsing accepts full second-resolution ISO-like strings with T/space separator, optional microseconds and surrounding ASCII whitespace. Date-only, offset-bearing, malformed calendar/clock values are flagged instead of silently coerced. Source strings, NULL and blank remain inspectable. Local `_nyc` columns use TIMESTAMP. `_utc` columns use TIMESTAMPTZ and are populated only for unambiguous valid times. The model detects modern NYC one-hour DST gaps/folds by round-trip conversion and adjacent-hour alternatives; both cases retain parsed local values and NULL UTC instants.

Per-field `*_time_status` distinguishes valid, missing, invalid_format, ambiguous_local_time and nonexistent_local_time. `*_parse_failed` denotes format/calendar problems. Missing required attributes and date quality issues are flagged. `closed_before_created` compares valid UTC instants, returns NULL when chronology cannot be evaluated, and never removes a row. Missing optional closure/due/resolution dates alone are not errors. No closure SLA, duration metric, historical status before capture or Vault `load_dts_utc` is inferred. Hash macros and Vault model implementation follow this slice.

## 6. Data Vault 2.0 modeling contract

See [DATA_VAULT_ARCHITECTURE.md](DATA_VAULT_ARCHITECTURE.md) for full grain, keys and effectivity decisions.

### Hubs
- **`hub_request`**: identity (`record_source`, `unique_key`) with original source text preserved in `request_bk`; the source namespace separates synthetic/live records.
- **`hub_agency`**: identity (`record_source`, exact `agency`).
- **`hub_problem`**: scoped taxonomy key (`record_source`, exact `agency`, exact `complaint_type`), because the source says problem categories are defined by responding agencies. Key composition is an explicit project design decision.

### Links
- **`lnk_request_agency`**: relationship of request to agency.
- **`lnk_request_problem`**: relationship of request to agency-scoped problem category.
- **Effectivity**: changes in observed relationship membership should be represented in Link effectivity Satellites (or equivalent documented insert-only change Satellites). Never mistake a Link's creation/load date for the assignment's actual event date.

### Satellites
- **`sat_request_status`** attached to `hub_request`: `status`, `hashdiff`, `snapshot_at_utc`, `load_dts_utc`, provenance. One row each **observed status change**, including return to a previous value.
- **`sat_request_lifecycle`**: `created_date`, `closed_date`, `due_date`, `hashdiff`, capture time.
- **`sat_request_resolution`**: `resolution_description`, `resolution_action_updated_date`, hashdiff, capture time.
- **`sat_request_location`**: `borough`, `incident_zip`, `community_board`, hashdiff, capture time.
- **`sat_agency_details`**: `agency_name` with observed updates (do not assume stable text).

Every Hub/Link has `load_dts_utc` and `record_source`. Business/hash keys are canonical SHA-256; hashdiffs hash tracked *attributes*, with deterministic NULL handling, normalization and delimiter-safe encoding. Use `snapshot_at_utc` as effective **observed** start and preserve first-load warehouse timestamps. Design composite PKs to distinguish versions captured in separate batches; reject ambiguous ties for one request at one snapshot time. Do not overwrite Raw Vault Satellites to close past states.

### Implemented first Raw Vault slice (2026-10-18)

Canonical SHA-256 v1 uses domain/arity prefixes and null-aware UTF-8 byte-length text encoding. The request hash includes `record_source` and exact `request_bk`; status hashdiff includes only exact raw status. Empty, NULL, whitespace, case, leading-zero and Unicode distinctions are preserved. This source-scoped request identity is an explicit refinement to the original single-field key proposal, avoiding synthetic/live collisions.

`hub_request` and `sat_request_status` are append-only incremental models. Warehouse `load_dts_utc` records first insertion and never resets on replay. Adjacent observations determine status boundaries; reversals and NULL transitions survive. Identical same-time captures coalesce, while conflicting same-time values fail. Safe late observations append; a late observation requiring removal/rewrite of an existing boundary fails before either model mutates. Full refresh is blocked. Reconstruct/reconcile invalidated history in a separate database; no automated retraction or promotion exists. Each dbt model commits separately. Current data produces 518 Hub identities and 520 status versions; the broader Vault and PIT remain pending.

### Implemented assignment Hub/Link slice (2026-10-18)

Agency/problem Hubs extend source namespacing to agency codes and agency/category tuples, preserving exact source strings and the existing SHA-256 v1 encoding. Link identities hash the ordered request/agency or request/problem parent hashes. `problem_bk` canonically encodes agency/category using prefix `dv2:v1:problem_bk:2:`. No existing request/status hash or warehouse metadata is migrated.

An observation view retains all successful records and exposes missing/eligibility flags. Missing agency omits both relationship types; missing category with a known agency omits only the problem identity/Link. No unknown members are created. Same-time conflicting eligible relationships fail preflight, and Links fail if parent identities are absent or provenance differs. Models append unseen identities/pairs and preserve first-load metadata on replay. Full refresh is blocked; schema changes fail. Earlier evidence can append an ever-observed relationship but cannot imply deactivation. Capture/batch provenance is retained in the input view, while Hub/Link rows store source/mode and warehouse load time.

The current eligible input derives 4 agency identities, 31 problem identities, and 519 Links per relationship type. Counts are validated against source tuples, not permanent thresholds. Partial dbt builds use `indirect_selection: buildable` to avoid tests on unbuilt downstream relations. Each model commits independently; effectivity and current-assignment interpretation remain future work. No API extraction was added.

### Implemented assignment effectivity (2026-10-18)

Two insert-only Link Satellites now store observed activation/deactivation events.
The architecture's effectivity section specifies grain, canonical boolean hashdiffs,
independent eligibility timelines, replacement-only deactivation, late-history guards
and retained provenance. No deactivation follows absence or incomplete captures.
All 124 Python checks, Ruff and 114 selected dbt data tests passed; the copied
project database derived 520 events per type without changing existing Vault rows.
M3 descriptive Satellites and all Business Vault/mart work remain pending.

### Implemented lifecycle Satellite (2026-10-19)

`sat_request_lifecycle` stores adjacent raw created/closed/due tuples at
`(request_hk, snapshot_at_utc)` grain with exact-text SHA-256 v1 domain
`request_lifecycle`. Typed staging dates and lifecycle-specific quality flags are
persisted alongside source strings. NULL clearing, format changes and reversals
are retained; source event dates never set observed version time. Replay,
conflicting capture, parent, schema, refresh and historical-rewrite safeguards
follow the existing insert-only policy; dependency models commit separately.
All 142 Python tests, Ruff and 67 selected dbt data tests passed against a copy.
Independent source reconstruction matched 520 versions without changing existing
tables. Resolution/location/agency Satellites and Business Vault remain pending.

### Implemented resolution Satellite (2026-10-21)

`sat_request_resolution` stores adjacent exact raw description/update-date tuples
at `(request_hk, snapshot_at_utc)` grain under SHA-256 v1 domain `request_resolution`.
Staging-derived NYC/UTC timestamps, source-specific date quality and original raw
text are retained. Capture time orders observations even if source dates repeat
or move backward. Description/date clearing and returning tuples produce versions.
Replay retains warehouse metadata; conflicting ties, missing/inconsistent parents,
schema/full-refresh changes and historical rewrites fail. Models commit separately.
Twenty-one new integration cases passed; all 163 Python tests and Ruff passed.
Copied-database builds passed 60 selected and 164 full-project dbt data tests.
Independent source-history/hash reconstruction matched 518 versions without
changing prior tables. Location/agency Satellites and Business Vault remain pending.

### Implemented location Satellite (2026-10-21)

`sat_request_location` preserves adjacent exact raw borough/ZIP/community-board
changes at `(request_hk, snapshot_at_utc)` grain under hashdiff domain
`request_location`. Nullable text and leading zeroes survive without normalization
or carry-forward. Capture time describes observed values; it does not establish
physical movement or location at earlier source events. Replay retains first-load
metadata; ties, parent/schema/refresh and history-rewrite safeguards follow the
existing insert-only policy. Each dbt model commits independently.
Seventeen new offline integration cases passed; all 180 Python tests and Ruff passed.
Copied-database builds passed 55 selected and 174 full-project dbt data tests.
Independent source/hash reconstruction matched 518 versions without changing
prior base tables. Agency details and Business Vault remain pending.

### Implemented agency details / M3 completion (2026-10-22)

`sat_agency_details` stores exact nullable agency-name changes at
`(agency_hk, snapshot_at_utc)` grain, using domain `agency_details`. Names never
participate in identity. Agreeing request observations coalesce by batch ID then
request key; conflicting names at the same agency/capture fail, including NULL
versus populated names. Missing agency keys are flagged/excluded; missing names
and missing categories do not suppress a valid agency observation. Supporting
`evidence_request_bk` plus batch retains original lineage on replay. The agency
Hub is the only Hub parent; existing request/assignment definitions are unchanged.
Late-history, parent/schema/refresh and transaction/replay safeguards are tested.
All 198 Python tests, Ruff, 67 selected and 185 full-project dbt data tests passed
against temporary/copied databases. Independent history/hash/evidence checks matched
four versions. Existing tables stayed unchanged. The required M3 models and tests
are complete; M4 status intervals and PIT follow. M1 tooling and M6 CI/release tasks
remain tracked separately.

### Implemented first M4 slice (2026-10-24)

`business_vault.bv_request_status_intervals` is a view over status Satellite
versions. It preserves raw status/hash/batch/source/mode/first-load provenance and
aliases capture time to inclusive `observed_from`; `LEAD` supplies exclusive
`next_observed_from`, with NULL on the latest row. Before-first state is unknown;
NULL source status is an observed missing value. Source event dates do not backdate
history. Currently accepted late evidence can revise derived ends without changing
Raw Vault rows. This is observed-time reconstruction, not warehouse-knowledge as-of.
Structural/hash/parent preflight and coverage/interval tests reject invalid input.
Seventeen new offline cases passed; all 215 Python tests and Ruff passed.
Copied-database builds passed 68 selected and 195 full-project dbt data tests.
Independent endpoint/provenance checks matched 520 intervals / 518 unbounded latest
rows, preserving every prior base table and replay row. Bounded PIT and lifecycle
metrics remain pending; M4 is not complete.


## Bounded observed status PIT (2026-10-24)

From `nyc_311_requests`, after landing ingestion:

```bash
dbt build --profiles-dir . --select +pit_request_status_asof
```

`business_vault.pit_request_status_asof` is a view at `(request_hk, as_of_utc)`
grain. Every loaded request Hub identity crosses the chosen calendar, including
identities not yet observed at that instant. The start-inclusive/end-exclusive
interval join selects one applicable status version, preserving exact nullable
`status`, `status_hashdiff`, `status_batch_id`, `status_snapshot_at_utc` and original
`status_load_dts_utc`. `record_source` and `mode` come from the Hub even for unknown
rows. The version reference is `(request_hk, status_snapshot_at_utc)`; load time is
provenance, not the ordering key. No PIT refresh/load clock is assigned.

`is_status_observed=false` means no loaded version exists at/before the instant;
all five status/version fields are NULL. An observed NULL status has the flag true
and non-NULL version pointers. No source creation/closure date backdates status.
An open-ended last state makes no freshness claim. Filter source/mode explicitly
when reporting; the global calendar intentionally includes unknown rows across
source populations. Hub-only identities also remain unknown.

By default the calendar contains **distinct loaded status-version capture instants**,
across namespaces. It is not a daily calendar and does not include every unchanged
landing capture. Empty Raw status history yields an empty default calendar. It
never extends to the current clock automatically. To choose a smaller reporting
calendar or inspect before-first/intraday times, supply a controlled list:

```bash
dbt build --profiles-dir . --select +pit_request_status_asof \
  --vars '{pit_status_as_of_instants: ["2026-01-31T00:00:00Z", "2026-02-02T08:00:00Z", "2026-02-03T08:00:00Z"]}'
```

Each input must be an ISO timestamp with seconds and an explicit `Z` or numeric
offset (up to six fractional-second digits). Naive/date-only/invalid inputs fail;
SQL literals are emitted only after parsing/normalization. Equivalent instants are
deduplicated in UTC. `[]` explicitly creates an empty PIT; omitted/null uses the
default. Maximum input/default calendar: **366 instants**; maximum entire Hub ×
calendar grid: **1,000,000 rows**. Oversized builds fail before replacing the view,
and query-time checks also reject growth beyond these bounds. Select fewer explicit
instants when the default exceeds the limits; no rows are silently truncated.
Calendar configuration is embedded in the view: rebuild it to change configuration.

All three time columns are TIMESTAMPTZ. Offset-equivalent instants and DST crossings
use absolute instants, independently of DuckDB's display timezone. The view reflects
accepted late Raw Vault versions without rebuilding and may revise prior as-of
answers; default calendar membership can grow with Raw captures. Pending landing
rows have no effect until loaded. This is **current-evidence observed-time**
reconstruction, not historical warehouse-knowledge or authoritative NYC event history.
The two timestamps do not implement bitemporal knowledge filtering.

Preflight reuses status structural/hash/parent safeguards and additionally requires
unique canonical Hub identities/provenance, including Hubs with no status. Upstream
Raw Vault guards remain unchanged. Each dbt model commits independently. A normal
`+` build loads dependencies; selecting only the PIT uses already loaded data.
Derived views are rebuildable; Raw Vault full-refresh protections still apply.

Inspect the synthetic request's accumulated observations:

```sql
set TimeZone = 'UTC';
select h.request_bk, p.as_of_utc, p.is_status_observed, p.status,
       p.status_snapshot_at_utc, p.status_batch_id, p.status_load_dts_utc
from business_vault.pit_request_status_asof p
join raw_vault.hub_request h using (request_hk)
where p.record_source = 'synthetic_311' and h.request_bk = 'DEMO-001'
order by p.as_of_utc;
```

Verified offline: 22 PIT integration cases cover independent version selection,
unknown/NULL/blank/Unicode, reversals, exact/microsecond/intraday/DST boundaries,
namespaces, safe late arrivals, landing-only isolation, replay/load provenance,
empty/explicit/default/duplicate-offset/future calendars, invalid inputs, both
size limits (including query-time growth), corrupt inputs, Hub-only identities,
and the 366-instant boundary. Ruff passed. The copied-database build passed all
203 project dbt data tests, including an independent latest-capture comparison.
The default calendar currently has four instants and produces 2,072 rows: 527
observed and 1,545 unknown. All prior base tables and replay results were unchanged.
Counts describe retained evidence, not permanent thresholds. Lifecycle duration
metrics are next; M4 remains incomplete. No new extraction or mart is added.


### Implemented lifecycle metrics / M4 completion (2026-10-24)

`business_vault.bv_request_resolution_metrics` selects the latest loaded lifecycle
capture per request Hub. It preserves raw/typed dates, quality and original provenance;
unknown Hub-only state stays explicit. Signed UTC elapsed hours remain diagnostic,
including negative values. `resolution_hours`/`resolution_days` require usable,
nonnegative creation/closure pairs; invalid/missing/DST-uncertain endpoints stay NULL.
Due dates and status do not decide eligibility. Closure clearing updates latest metrics;
late older captures never replace newer ones. This is latest-capture source-date
measurement, not historical lifecycle as-of, response-time, closure-cycle or SLA analysis.
See the architecture for field definitions, quality priority and guarded loading.
Thirty-four new offline cases and Ruff passed; the copied-database build passed
216 dbt data tests and independently matched 518 latest rows (498 valid, 20 missing
closure). All prior base data and replay provenance were preserved. Required M4
models/temporal checks are complete for retained evidence; M5 marts are next.

## 7. Business Vault and marts

**Business Vault**
- **`bv_request_status_intervals`**: one row per request status version; `observed_from`, `next_observed_from` via `LEAD` ordered by capture time; use half-open interval `observed_from <= as_of_time < next_observed_from`.
- **`pit_request_status_asof`**: `request_hk`, as-of date/time from a bounded snapshot calendar, applicable Satellite version `load_dts_utc`/`snapshot_at_utc`. This is a **snapshot-observed PIT**, not an authoritative NYC event-time PIT or classical source-effective PIT.
- **`bv_request_resolution_metrics`**: durations based on NYC `created_date`/`closed_date`, convert both from assumed local NYC time to comparable timestamps; null or negative anomalies flagged, not silently clamped to zero.
- **`bv_request_assignment_history`**: optional Link effectivity history of observed agency/category changes.

**Marts and grain**
| Mart | Grain | Main metrics |
|---|---|---|
| `mart_request_trends` | date + agency + problem + borough | Created count; closed count from **events**, separate date measures |
| `mart_agency_performance` | agency + cohort/month | Volume, closed share at defined extract/as-of, median/p90 closure time |
| `mart_resolution_times` | source-scoped request_hk, latest loaded lifecycle capture | Implemented: raw/typed dates, valid durations, quality/provenance, NYC event dates and population/eligibility counters |
| `mart_historical_status` | snapshot as-of date + agency + status | Observed open/in-progress/closed counts; only **from first snapshot forward** |

Do **not** use `due_date` as closure-SLA; the dictionary states it is when the responding agency is expected to update the request. Distinguish "new requests created in month" from "requests closed in month"; one request can contribute to both on different dates.

### Implemented first M5 slice (2026-10-24)

`marts.mart_resolution_times` publishes the latest lifecycle metric per request
without dropping duration exclusions. NYC event dates and creation cohort month
use only valid source times. Population, duration and event counters have explicit
denominators in [REPORTING_CONTRACTS.md](REPORTING_CONTRACTS.md). Record source/mode
remain separate; capture dates are not event dates. Twenty-two offline cases and
Ruff passed; copied-database builds passed 230 dbt tests. Independent date/counter/
provenance reconstruction matched 518 rows (498 eligible durations, 20 exclusions).
Prior base tables/BV outputs and mart replay stayed unchanged. M5 remains in progress.

## 8. Data tests and tricky history scenarios

**Must-have deterministic fixtures** (fake IDs; never actual customer records):
1. Request created and closed, changing observed status.
2. Open→Closed→Open reversal; final status matches earlier hashdiff but is a new version.
3. Same status in successive captures; only one Raw Vault status version.
4. Request shifts agency or problem category; relationship membership history is preserved.
5. Request created before snapshot coverage: status for earlier dates must be **unknown**, not fabricated.
6. Missing closure date and `due_date`; blank vs NULL; invalid timestamp.
7. Duplicate replay and conflicting values within a batch.
8. Two captures at different times on same date; PIT selection deterministic.
9. Daylight-saving ambiguity for local source times; documented conversion strategy.
10. One request modified after its original creation window; bounded cohort refresh detects it.

**Automated assertions:** unique Hub/Link BK/hashes; Link FK relationships; unique Satellite version grain; deterministic hash canonicalization; stable load timestamp on replay; monotonic observed interval ordering; no interval overlap; no look-ahead; no duplicate counts from joins; closure duration anomalies flagged; synthetic expected aggregation totals.

## 9. Delivery milestones

See [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) for tasks and sign-off criteria.

- **M0 Documentation:** project blueprint, architecture, source map, agent guide, Python requirements, README.
- **M1 Foundation:** Python environment, dbt project/profile, DuckDB connection, .env.example, Makefile, ignore rules, deterministic synthetic 311 samples.
- **M2 Landing & staging:** bounded Socrata client, full-cohort snapshot ingestion, audit, schema/typing and hash normalization.
- **M3 Raw Vault:** Hubs, Links, Satellites, relationship effectivity, incremental change detection, tests.
- **M4 Business Vault:** observed intervals, PIT, lifecycle metric calculations and temporal tests.
- **M5 Analytics & visuals:** marts and Streamlit trends, agency performance, snapshot-observed backlog.
- **M6 Release-quality:** GitHub Actions synthetic CI, dbt docs/lineage, diagrams, screenshots, reproducible README and CV summary.

**Milestone status:** M0 is complete only when all named documentation files exist in `main`; M1–M6 are pending until implementation/tests actually run.

## 10. Planned commands (not currently executable)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# To implement as part of M1; these commands do not exist yet:
make demo
make test
make dbt-docs
make dashboard
```

Use a default **offline synthetic demo**, no Socrata token needed. Real API integration may use `SOCRATA_APP_TOKEN` in local `.env`; never commit tokens or generated DuckDB, payloads, and Parquet files. Check `git status` before commits.

## 11. Questions / decisions to resolve during M1–M2

- Final agency and date filters based on API `COUNT(*)` estimates and laptop memory limits.
- Interpretation of NYC source floating timestamps and DST ambiguous cases.
- Whether to track `descriptor` as an attribute of agency-scoped problem or as an additional business entity in phase 2.
- Which status values count as "open" vs "resolved" for business analytics (do not assume only Pending/Closed).
- Which historical assignment relationships to model in Link effectivity Satellites; keep semantics as **observed from snapshot**, not authoritative reassignment time.
- Snapshot retention policy, reproducibility strategy, and source revisions during page-by-page extraction.

**Change control:** record changes to Hub/Link grains, historical semantics, status taxonomy, or business metrics as architecture decision notes; update `AGENTS.md` and docs when verified behavior changes.
