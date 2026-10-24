# Instructions for AI Coding Agents

Read **[docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md)**, **[docs/DATA_VAULT_ARCHITECTURE.md](docs/DATA_VAULT_ARCHITECTURE.md)**, **[docs/SOURCE_DATA_DICTIONARY.md](docs/SOURCE_DATA_DICTIONARY.md)**, and **[docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md)** before editing code.

## Scope
Build an NYC 311 historical **service analytics** warehouse using Python, dbt Core, DuckDB and Data Vault 2.0, with an **offline synthetic demo** as the reproducibility baseline. The primary NYC API is `https://data.cityofnewyork.us/resource/erm2-nwe9.json`.

## Historical correctness rules
- NYC 311 is a current-state request feed, **not** a historical log of every status change.
- Source `created_date`, `closed_date`, `due_date` and `resolution_action_updated_date` have different semantics. In particular `due_date` is for an agency update, not a universal closure SLA.
- Preserve `snapshot_at_utc` (when we observed the record) separately from `load_dts_utc` (when Vault first loaded this version). Historical observed status is **unknown before first snapshot**.
- Hubs and Links are identity/relationship structures. Raw Vault Satellites and Link effectivity records are insert-only. Detect *adjacent* changes and status reversals rather than deduplicating all equal historical values.
- Explicitly distinguish event-date trends from snapshot-as-of backlog. Don't claim full retrospective status history or intraday source-change timestamps.
- Treat reassignment as a relationship-history problem. A partial/failed snapshot is not evidence a relationship ended.
- Persist stable deterministic SHA-256 business keys and null-safe hashdiffs; ensure idempotent replay.

## Engineering workflow
1. Check actual repo files (planned structure isn't implemented yet).
2. Pick an unchecked milestone in the roadmap; state what it completes.
3. Develop in small slices from synthetic fixture to ingestion/staging, Raw Vault, PIT, marts, tests.
4. Run available tests, report pass/fail honestly and update docs only after verification.
5. Document deviations to business key composition, granularity, time interpretation and historization.
6. Do not commit tokens, environment files, DuckDB files, source extracts, generated dbt artifacts or unnecessarily precise location/person data.
7. Keep GitHub Actions demo network-free; public Socrata API is optional for real mode. Maintain conservative retry, pagination and record reconciliation in source adapter.
8. Avoid changing unrelated files; keep commits reviewable.

## Current implementation boundary
Offline and bounded live landing are implemented in `ingestion`. Live runs validate schema, paginate/reconcile counts, and publish through the shared transactional landing writer. Running/failed live IDs are never resumed; progress checkpoints are diagnostic only. Audit `expected_rows_before` and `expected_rows_after` are additive nullable columns. Source event dates remain strings. Network-free tests and the synthetic demo must remain runnable without credentials.

Typed staging is implemented as `staging.stg_311_request_snapshots`, preserving successful source observations and exposing local/UTC timestamps plus quality flags. UTC uses America/New_York only for unambiguous valid local times; DST gaps/folds stay unresolved. Do not silently drop flagged rows or alter raw strings. Literal custom dbt schema names are intentional; use separate database files to isolate environments.

Canonical SHA-256 v1 and the insert-only `hub_request` / `sat_request_status` slice are implemented. Request identity is (`record_source`, exact request_bk), not a cross-source key alone. Hash canonicalization preserves exact text with null-aware UTF-8 byte-length encoding and domain/arity prefixes. Adjacent observations retain reversals and missing-status transitions. Conflicting capture ties and late captures requiring an existing boundary to be removed are rejected before loading. Full refresh is blocked; schema changes fail. Never reset first-load warehouse timestamps or silently retract Raw Vault versions. Build `+sat_request_status` to include dependencies. Staging still never assigns Vault load timestamps.

Agency/problem Hubs and request-assignment Links are implemented using existing exact-text SHA-256 v1 domains. Their identities are source-scoped; a Link is an ever-observed parent pair. The assignment observation view retains missing fields and eligibility flags. Missing agency suppresses both relationships; missing problem with known agency still permits its agency Link. Never fabricate unknown members, deactivate Links from absence, or infer source assignment dates. All four new models reject conflicting eligible capture ties and block full refresh; Links require parent coverage. Existing request/status definitions remain unchanged. Project `indirect_selection: buildable` keeps partial builds from testing unbuilt downstream relations.

Assignment effectivity is implemented in two insert-only Link Satellites. Only an
explicit successful eligible replacement ends a known relationship; failed/running
batches, missing fields and disappearance never do. Agency/problem timelines are
independent, so last-known problem may lag agency when category evidence is missing.
Observed capture time is not source event time. Preserve activation/deactivation
reversals, batch provenance and first-load timestamps. Both effectivity models reject
late evidence that would retract/change stored events in either timeline; safe additive
late events remain allowed. Full refresh is blocked; Link parent coverage is required.

The lifecycle Satellite is implemented with exact-text raw created/closed/due
hashdiff domain `request_lifecycle`, adjacent capture versions and persisted staging
typed dates/quality flags. NULL/blank/format changes and closure-date clearing are
observed versions; never normalize raw inputs before hashing. Capture time is not
source event time. Lifecycle quality covers only these three dates. Replay retains
batch/load provenance; ties, missing/inconsistent Hub parents, schema changes,
full refresh and historical rewrites (including derived interpretation changes)
fail. Each dbt model commits separately; safe additive late boundaries can append.

The resolution Satellite is implemented using the exact raw ordered description/
update-date tuple under hashdiff domain `request_resolution`. Persist staging's
NYC/UTC resolution timestamp and resolution-only quality flags. Capture time orders
versions even when source update dates repeat or move backward; never use the
source date as a complete CDC cursor, status transition or observed boundary.
Retain NULL/blank/text/format distinctions, adjacent reversals and clearing.
Replay preserves provenance/load timestamps; conflicting ties, missing/inconsistent
parents, schema/full-refresh changes and historical/interpretation rewrites fail.
Safe additive late versions append. Each model commits independently.

The location Satellite is implemented with the ordered exact raw borough/ZIP/
community-board tuple under hashdiff domain `request_location`. Keep all three
fields nullable text, including leading zeroes, NULL/blank, whitespace and unknown
source values. Never infer/normalize geography or carry forward missing fields.
Captured missing values create versions; absent requests do not. Preserve adjacent
returns, capture ordering and replay metadata. Conflicting ties, missing/inconsistent
parents, schema/full-refresh changes and historical rewrites fail; additive safe
late boundaries append. Each dbt model commits independently. Observed corrections
do not establish physical movement or location at earlier source event dates.

Agency details complete the required M3 models/checks. The Satellite uses existing
source-scoped agency identity and exact nullable name hashdiff domain `agency_details`.
Every eligible request observation for one agency/capture must agree, including
NULL/blank distinctions. Reject conflicts rather than choosing a majority/populated
name. Coalesce agreeing ties by batch ID then exact request key; retain original
`evidence_request_bk`, batch and load timestamps on replay. Missing agency keys remain
flagged/excluded; missing names or categories do not suppress an eligible agency.
All eligible observations require consistent Hub business key/source/mode. Preserve
adjacent rename/clearing/return versions and safe additive late evidence; block
history rewrites, full refresh and schema changes. Each model commits independently.
Observed feed labels are not authoritative legal names or rename event timestamps.

The first M4 model is a view: `business_vault.bv_request_status_intervals` reads
only loaded Raw Vault status versions, preserves exact nullable status and original
provenance/load timestamps, and projects capture time to `observed_from` with LEAD
for exclusive `next_observed_from`. The last interval has NULL end. State before
first capture is unknown; an observed NULL status is distinct from absence. Never
backdate from source event dates or infer source freshness/continuous status from
open ends. Accepted late Raw Vault versions can revise derived endpoints; this is
current-evidence observed-time reconstruction, not warehouse-knowledge as-of.
Selecting + dependencies loads Raw Vault first; view-only builds use loaded data.
Preflight rejects duplicate/null/canonical-hash/Hub inconsistencies. Preserve the
Raw Vault guards and timestamps; each dbt model commits independently.

Bounded `business_vault.pit_request_status_asof` is implemented as a view over
loaded request Hubs and status intervals. Grain is request_hk + as_of_utc, including
unknown rows before first capture or for Hub-only identities. is_status_observed
separates unknown from observed NULL status; preserve original version pointers,
namespace/mode and Raw first-load timestamps. Default calendar is distinct loaded
status-version captures, not every unchanged landing capture or a daily calendar.
Optional pit_status_as_of_instants requires explicit timezone-aware ISO strings;
normalize/deduplicate absolute instants, reject naive/invalid input. Cap calendars
at 366 instants and global Hub/calendar grids at 1,000,000 rows, at build and query
time; fail rather than truncating. Rebuild to change explicit configuration.
Accepted late Raw evidence revises current-evidence PIT answers dynamically;
never claim event-time or warehouse-knowledge history. Preserve Raw guards/hashes
and preflight status/Hub integrity; no new refresh clock.
M1 tooling and M6 CI/release tasks remain separately tracked. Marts are not implemented. A complete one-day live extraction has been verified; the full six-month default cohort has only been counted. Do not claim scheduling, full historical status coverage or transactional API snapshots.


M4 lifecycle metrics are implemented in business_vault.bv_request_resolution_metrics,
a view at one row per request Hub, using the latest loaded lifecycle capture, not
warehouse load time. Preserve every selected raw/typed attribute, quality flag and
original provenance/load timestamp; Hub-only lifecycle state is explicitly unknown.
Signed reported_closure_elapsed_hours keeps negative diagnostics; resolution_hours
and resolution_days require usable nonnegative creation/closure UTC pairs. Preserve
missing/invalid/DST uncertainty and clearing; never impute or clamp to zero. Days
mean hours/24. Due-date quality and reported status do not gate this source-date
measure; no SLA, response-time, closure-cycle or historical lifecycle as-of claims.
Late earlier captures never override later ones. Preflight validates loaded-only
canonical keys/hash/parents and stored timestamp interpretation with existing staging
macros; keep Raw Vault safeguards unchanged. Required M4 models/temporal checks are
complete for retained evidence. M5 reporting marts are next; M1/M6 remain tracked.
