# NYC 311 — Implementation Roadmap

**Status:** Landing, typed staging and the first request/status Raw Vault slice verified; remaining Vault structures are pending. Track tasks using checked boxes **only after verification**.

The full implementation contract is in [PROJECT_PLAN.md](PROJECT_PLAN.md). AI agents must follow [../AGENTS.md](../AGENTS.md).

## M0 — Design documentation
- [x] README with entry links and scope.
- [x] Canonical project plan and milestone acceptance criteria.
- [x] Source-to-target data dictionary.
- [x] Data Vault Hub, Link, Satellite, effectivity and PIT design.
- [x] AGENTS.md and requirements.txt.
**Acceptance:** these documents exist in repository and links resolve; no claims of pipeline readiness.

## M1 — Reproducible environment + synthetic demo
- [x] Confirm Python 3.11+ setup; verify installation in a clean macOS virtual environment.
- [x] Pin validated package versions/lockfile after compatibility smoke tests.
- [ ] Create dbt-duckdb project, profile example, .env.example, Makefile and local DuckDB database.
- [x] Build deterministic synthetic request snapshots, including a status reversion and agency reassignment.
- [x] Extend .gitignore for local DuckDB/WAL files, outputs, logs, dbt target, extracts (notebook outputs must still be cleared before commit).
**Acceptance:** a new user can start the offline synthetic project without API tokens; repeat runs are deterministic.

Environment slice verified on 2026-10-18: macOS ARM64, Python 3.13.5 in `.venv`; requirements installed; `pip check`, dbt CLI/duckdb adapter discovery, JupyterLab CLI, and in-memory DuckDB smoke test passed. Exact resolved versions are in `requirements-lock.txt`; full dbt execution and synthetic demo remain pending. The connection notebook passed schema/syntax validation, Ruff, and offline mocked DataFrame/error/retry checks. It preserves the user's broader date filter and all requested fields for an in-memory sample only, with a 1,000-row default limit and deterministic tie ordering. Live requests and live schema validation are deferred to the user's run; M2 items remain unchecked.

Exploratory SQL slice verified on 2026-10-18: `run_sql_queries.ipynb` reuses the API notebook, registers its bounded DataFrame, and executes `create_311_service_request.sql` into `data/nyc_open_data.db`. The replaceable sample table includes `snapshot_at_utc` and `record_source`; it is outside the planned historical landing/Vault pipeline. Notebook schema/syntax and an offline mocked GET-to-DuckDB run passed from both repository and notebook directories, including persistence, replacement and failed-fetch stale-data protection. No live API call or project database write was performed during verification. M1/M2 warehouse milestones remain pending.

## M2 — Ingestion and staging
- [x] Implement offline landing schema, transactional batch audit, immutable observations, and replay protection.
- [x] Declare dbt landing sources and provide an offline walkthrough with verified synthetic history.
- [x] Validate live API field names against Socrata metadata (all 15 landing fields/types confirmed).
- [x] Implement an optional `SOCRATA_APP_TOKEN`-aware client and pagination.
- [x] Define a configurable bounded cohort and a repeatable full-cohort refresh command (manual execution; scheduling remains future work).
- [x] Extend live batch audit with diagnostic progress checkpoints, duplicate rejection, count reconciliation and atomic publication.
- [ ] Implement resumable extraction/checkpoint recovery if required; failed/interrupted batches currently restart under new IDs.
- [x] Implement the successful-batch staging view with raw-value preservation, typed local/UTC timestamps and date/lifecycle quality flags.
- [x] Implement reusable canonical SHA-256 v1 text key/hashdiff macros with namespace/domain separation and independent byte-encoding tests.
- [x] Test null/blank/malformed source dates, distinct/duplicate keys, snapshot preservation, failed-batch exclusion and NYC DST boundaries.
**Acceptance:** bounded source extracts or synthetic batches are reproducible; every loaded row has provenance and a capture timestamp; no silent partial batches.

Offline landing slice verified on 2026-10-18: 27 Python checks passed against temporary databases, including replay immutability, source preservation, injected partial-insert rollback, existing-table preservation, CLI execution, and notebook execution from root/notebook directories with network connections blocked. Ruff passed for ingestion, tests and notebook cells. dbt 1.11.16 / duckdb adapter 1.10.1 recognized two landing sources and passed all nine source tests against a temporary synthetic database. The existing unused `models.nyc_311_requests.example` configuration produces a warning in the isolated dbt check. The landing contract adds audit `record_source` for provenance-aware replay. Failed/running IDs require new IDs; automatic checkpoint recovery, API page reconciliation and live schema validation remain unimplemented, so the broader M2 audit task remains unchecked.

Live landing slice verified on 2026-10-18: all 15 source fields/types matched live metadata. Count-only returned 302,762 rows for the planned January–June DEP/DSNY scope and 515 for January 1. A complete one-day extract passed multi-page retrieval and before/fetched/after reconciliation with 515 committed rows. The full six-month extraction was not run. Sixty-nine Python tests passed offline, including schema drift, retries/rate limits, malformed responses, duplicate keys, changing counts, size guards, transaction rollback, existing-data preservation, CLI dispatch and the offline walkthrough. Ruff passed. All eleven dbt source and publication/count checks passed against the verified live extraction. API validation and pagination are implemented; staging and Vault modeling remain pending. No scheduler or automatic resume is implemented.

Staging slice verified on 2026-10-18: `staging.stg_311_request_snapshots` is a view over successful landing batches, retaining source strings, leading-zero IDs/ZIPs, all modes and observation history. NYC local times are parsed independently from observation time; UTC is produced only for unambiguous valid source times. DST gaps/folds are flagged and have NULL UTC values. Negative lifecycle order is flagged without dropping rows. All 85 Python checks passed, including 16 staging integration scenarios; all 26 staging dbt tests passed against a copy of the existing landing database. Ruff passed. The project uses literal custom schema names and separate database files for independent environments. Canonical Vault hashing and Vault load timestamps are intentionally pending. Existing dbt example-model warnings remain unrelated to staging.

## M3 — Raw Vault
- [x] Implement source-scoped `hub_request`.
- [ ] Implement hub_agency and hub_problem.
- [ ] Implement lnk_request_agency, lnk_request_problem.
- [x] Implement insert-only `sat_request_status` using adjacent capture changes and replay-safe first-load timestamps.
- [ ] Implement lifecycle/resolution/location/agency Satellites and effectivity for changing Links.
- [x] Verify request/status replay, reversals, NULL transitions, safe late arrivals, conflicting captures and full-refresh protection.
- [ ] Verify relationship-history changes after Link/effectivity implementation.
- [x] Add request/status relationships, uniqueness, provenance, canonical hash and expected change-history tests.
- [ ] Extend tests to remaining Vault models.
**Acceptance:** no existing business keys or unmodified Satellites duplicate on replay; changed relationships and states remain historically reconstructible.

Request/status slice verified on 2026-10-18: 12 new offline integration tests independently reproduce UTF-8 SHA-256 encoding, isolate synthetic/live request identities, preserve leading zeroes and NULL/empty distinctions, retain Open → Closed → Open, keep original warehouse timestamps/provenance on replay, append safe late boundaries, and reject ties/backfills/full-refresh operations requiring historical mutation. The dependency build passes all 58 dbt data tests against a copy of the current database. The current data produces 518 request Hub identities and 520 status versions. Only the first M3 slice is complete; other Hubs/Links/Satellites, effectivity and M4 remain pending. Backfills requiring retraction need a separately validated reconstruction, not an in-place update.

## M4 — Business Vault / temporal semantics
- [ ] Build bv_request_status_intervals with LEAD and non-overlapping observed validity.
- [ ] Build bounded pit_request_status_asof.
- [ ] Build request lifecycle duration metrics with source local-time conversion.
- [ ] Test before-first-snapshot, exact boundary, late arrival and DST cases.
**Acceptance:** as-of results match deterministic fixtures; do not pretend to reconstruct pre-capture status history.

## M5 — Marts + dashboard
- [ ] mart_request_trends with created and closed event metrics.
- [ ] mart_agency_performance with carefully defined rates, median and p90 closure time.
- [ ] mart_resolution_times and mart_historical_status.
- [ ] Streamlit dashboard for volumes, response performance and *observed* backlog.
- [ ] Document denominator/grain assumptions and source population coverage.
**Acceptance:** plots reproduce tested synthetic aggregates and never imply filtered data are whole-city totals.

## M6 — Public portfolio quality
- [ ] CI pipeline runs lint/pytest/dbt build in offline demo mode.
- [ ] Publish dbt docs/lineage, architecture diagram, screenshots, example SQL and known limitations.
- [ ] Test fresh clone setup and document all commands actually executed.
- [ ] Add concise, verifiable CV bullet and source attribution.
**Acceptance:** a fresh environment can reproduce the synthetic project without credentials; CI passes.

## Delivery rules
1. Ship the simplest correct vertical slice first: request Hub + status Satellite + as-of test.
2. Prioritize correctness over cloud/tool proliferation.
3. Never claim that 311 snapshots provide event-by-event historical status history.
4. Update boxes only after code is implemented and tests were run; report what actually passed.
