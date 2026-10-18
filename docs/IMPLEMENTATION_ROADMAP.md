# NYC 311 — Implementation Roadmap

**Status:** Environment setup and an unexecuted connection notebook verified locally; warehouse implementation pending. Track tasks using checked boxes **only after verification**.

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
- [ ] Build deterministic synthetic request snapshots, including a status reversion and agency reassignment.
- [ ] Extend .gitignore for local DuckDB/WAL files, outputs, logs, dbt target, extracts.
**Acceptance:** a new user can start the offline synthetic project without API tokens; repeat runs are deterministic.

Environment slice verified on 2026-10-18: macOS ARM64, Python 3.13.5 in `.venv`; requirements installed; `pip check`, dbt CLI/duckdb adapter discovery, JupyterLab CLI, and in-memory DuckDB smoke test passed. Exact resolved versions are in `requirements-lock.txt`; full dbt execution and synthetic demo remain pending. The connection notebook passed schema/syntax validation, Ruff, and offline mocked DataFrame/error/retry checks. It preserves the user's broader date filter and all requested fields for an in-memory sample only, with a 1,000-row default limit and deterministic tie ordering. Live requests and live schema validation are deferred to the user's run; M2 items remain unchecked.

Exploratory SQL slice verified on 2026-10-18: `run_sql_queries.ipynb` reuses the API notebook, registers its bounded DataFrame, and executes `create_311_service_request.sql` into `data/nyc_open_data.db`. The replaceable sample table includes `snapshot_at_utc` and `record_source`; it is outside the planned historical landing/Vault pipeline. Notebook schema/syntax and an offline mocked GET-to-DuckDB run passed from both repository and notebook directories, including persistence, replacement and failed-fetch stale-data protection. No live API call or project database write was performed during verification. M1/M2 warehouse milestones remain pending.

## M2 — Ingestion and staging
- [ ] Validate live API field names against Socrata metadata.
- [ ] Implement an optional `SOCRATA_APP_TOKEN`-aware client and pagination.
- [ ] Define source cohort and full-cohort daily refresh.
- [ ] Build batch audit, row validation, per-batch de-duplication, checkpointing and page-count reconciliation.
- [ ] Implement dbt staging and reusable canonical SHA-256 macros.
- [ ] Test nulls, malformed source dates, distinct and duplicate keys.
**Acceptance:** bounded source extracts or synthetic batches are reproducible; every loaded row has provenance and a capture timestamp; no silent partial batches.

## M3 — Raw Vault
- [ ] Implement hub_request, hub_agency, hub_problem.
- [ ] Implement lnk_request_agency, lnk_request_problem.
- [ ] Implement status/lifecycle/resolution/location/agency Satellites and effectivity for changing Links.
- [ ] Verify replay idempotency, full history preservation, reopened requests, and relationship changes.
- [ ] Add dbt relationships, uniqueness, not-null and change-history tests.
**Acceptance:** no existing business keys or unmodified Satellites duplicate on replay; changed relationships and states remain historically reconstructible.

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
