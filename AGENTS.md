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

The next Vault slice is request location, then agency details. Business Vault/PIT/marts are not implemented. A complete one-day live extraction has been verified; the full six-month default cohort has only been counted. Do not claim scheduling, full historical status coverage or transactional API snapshots.
