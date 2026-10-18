# NYC 311 Historical Service Analytics — Project Plan

**Status:** Architecture and milestones drafted; implementation not started.  
**Updated:** 2026-10-18  
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

## 6. Data Vault 2.0 modeling contract

See [DATA_VAULT_ARCHITECTURE.md](DATA_VAULT_ARCHITECTURE.md) for full grain, keys and effectivity decisions.

### Hubs
- **`hub_request`**: business key `unique_key` (source text, never assume customer-visible SR number).
- **`hub_agency`**: business key `agency`.
- **`hub_problem`**: scoped taxonomy key (`agency`, `complaint_type`), because the source says problem categories are defined by responding agencies. Key composition is an explicit project design decision.

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
| `mart_resolution_times` | request_id | Created/closed timestamps, turnaround hours, anomaly flags |
| `mart_historical_status` | snapshot as-of date + agency + status | Observed open/in-progress/closed counts; only **from first snapshot forward** |

Do **not** use `due_date` as closure-SLA; the dictionary states it is when the responding agency is expected to update the request. Distinguish "new requests created in month" from "requests closed in month"; one request can contribute to both on different dates.

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
