"""Agency/cohort populations, closure-date coverage and eligible duration quantiles."""

import math
import shutil
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from test_assignments import frozen, load
from test_lifecycle_metrics import record
from test_raw_vault import execute_dbt
from test_request_trends import assert_independent as assert_trends
from test_request_trends import population, query

from ingestion.fixtures import synthetic_batches
from ingestion.landing import load_batch

ROOT = Path(__file__).resolve().parents[1]
MODEL = "mart_agency_performance"
RAW = ["hub_request", "sat_request_status", "sat_request_lifecycle"]
KEY = ["record_source", "mode", "observed_agency_bk", "created_cohort_month_nyc"]


def rows(db):
    return query(db, "SELECT * FROM marts.mart_agency_performance ORDER BY ALL")


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    i = (len(ordered) - 1) * p
    lo = math.floor(i)
    hi = math.ceil(i)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (i - lo)


def assert_independent(db):
    assert_trends(db)
    groups = defaultdict(list)
    for r in population(db):
        groups[tuple(r[c] for c in KEY)].append(r)
    actual = rows(db)
    assert len(actual) == len(groups)
    for r in actual:
        members = groups[tuple(r[c] for c in KEY)]
        n = len(members)
        closed = sum(m["closed_event_date_nyc"] is not None for m in members)
        values = [m["resolution_hours"] for m in members if m["is_duration_valid"]]
        assert r["dimension_basis"] == "lifecycle_capture"
        assert r["retained_request_count"] == n
        assert r["lifecycle_observed_request_count"] == sum(
            m["is_lifecycle_observed"] for m in members
        )
        assert r["unobserved_lifecycle_count"] == sum(
            not m["is_lifecycle_observed"] for m in members
        )
        assert r["usable_creation_date_count"] == sum(
            m["created_event_date_nyc"] is not None for m in members
        )
        assert r["usable_closure_date_count"] == closed
        assert r["missing_closure_date_count"] == sum(
            m["closed_date_time_status"] == "missing" for m in members
        )
        assert r["unusable_closure_date_count"] == sum(
            m["closed_date_time_status"]
            in ["invalid_format", "ambiguous_local_time", "nonexistent_local_time"]
            for m in members
        )
        assert r["closed_before_created_count"] == sum(
            m["closed_before_created"] is True for m in members
        )
        assert r["duration_eligible_count"] == len(values)
        assert r["duration_ineligible_count"] == n - len(values)
        assert r["reported_closure_date_share"] == pytest.approx(closed / n)
        assert r["duration_coverage_share"] == pytest.approx(len(values) / n)
        stats = {
            "total_resolution_hours": math.fsum(values) if values else None,
            "mean_resolution_hours": math.fsum(values) / len(values)
            if values
            else None,
            "median_resolution_hours": percentile(values, 0.5),
            "p90_resolution_hours": percentile(values, 0.9),
        }
        for field, expected in stats.items():
            if expected is None:
                assert r[field] is None
            else:
                assert r[field] == pytest.approx(expected)
        captures = [
            m["snapshot_at_utc"] for m in members if m["snapshot_at_utc"] is not None
        ]
        assert r["min_selected_lifecycle_capture_at_utc"] == (
            min(captures) if captures else None
        )
        assert r["max_selected_lifecycle_capture_at_utc"] == (
            max(captures) if captures else None
        )
        agency = r["observed_agency_bk"]
        assert r["has_missing_agency"] == (agency is None or not agency.strip())
        assert r["is_cohort_month_known"] == (r["created_cohort_month_nyc"] is not None)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("agency-performance")
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    db = folder / "warehouse.db"
    for b in synthetic_batches():
        load_batch(database_path=db, **b)
    load(
        db,
        "initial",
        1,
        [
            record("AGG-0", agency="AGG", closed_date="2026-01-01T12:00:00"),
            record("AGG-1", agency="AGG", closed_date="2026-01-01T13:00:00"),
            record(
                "AGG-3",
                agency="AGG",
                closed_date="2026-01-01T15:00:00",
                complaint_type="Other category",
                borough="Other borough",
            ),
            record("AGG-9", agency="AGG", closed_date="2026-01-01T21:00:00"),
            record("AGG-missing", agency="AGG", closed_date=None),
            record("AGG-negative", agency="AGG", closed_date="2026-01-01T11:00:00"),
            record("ONLY-ZERO", agency="ZERO", closed_date="2026-01-01T12:00:00"),
            record("NO-ELIGIBLE", agency="NONE", closed_date=None),
            record("BAD-CLOSED", agency="BAD", closed_date="bad"),
            record("FOLD-CLOSED", agency="BAD", closed_date="2026-11-01T01:30:00"),
            record("GAP-CLOSED", agency="BAD", closed_date="2026-03-08T02:30:00"),
            record("MISSING-CREATED", agency="COHORT", created_date=None),
            record("BAD-CREATED", agency="COHORT", created_date="bad"),
            record(
                "MIDNIGHT",
                agency="MID",
                created_date="2026-01-31T23:30:00",
                closed_date="2026-02-01T00:30:00",
            ),
            record(
                "SPRING",
                agency="DST",
                created_date="2026-03-08T01:59:00",
                closed_date="2026-03-08T03:01:00",
            ),
            record(
                "FALL",
                agency="DST",
                created_date="2026-11-01T00:30:00",
                closed_date="2026-11-01T02:30:00",
            ),
            record("DUE-BAD", agency="DUE", due_date="bad"),
            record("NULL-AGENCY", agency=None),
            record("EMPTY-AGENCY", agency=""),
            record("BLANK-AGENCY", agency=" \t"),
            record("UNICODE-AGENCY", agency=" DÉP|; "),
            record("MOVE", agency="MOVE-A"),
        ],
    )
    load(
        db,
        "changed-cohort",
        4,
        [
            record(
                "MOVE",
                agency="MOVE-B",
                created_date="2026-02-01T12:00:00",
                closed_date="2026-02-02T12:00:00",
            )
        ],
    )
    load_batch(
        database_path=db,
        batch_id="live-namespace",
        records=[record("AGG-1", agency="AGG", closed_date="2026-01-01T14:00:00")],
        snapshot_at_utc=datetime(2026, 2, 1, 8, tzinfo=UTC),
        source_id="erm2-nwe9",
        record_source="NYC311:erm2-nwe9",
        mode="live",
        requested_filter="fictional namespace",
    )
    with duckdb.connect(str(db)) as con:
        con.execute(
            "UPDATE landing.ingestion_batch SET page_count=1,expected_rows_before=1,expected_rows_after=1 WHERE batch_id='live-namespace'"
        )

    def build(selectors=(MODEL,)):
        result = execute_dbt(project, db, folder / "profile", selectors=selectors)
        assert result.returncode == 0, result.stdout + result.stderr

    build(("+mart_request_trends", "+sat_request_status"))
    original = frozen(db, RAW)
    build()
    initial = rows(db)
    build()
    assert rows(db) == initial and frozen(db, RAW) == original
    load(
        db,
        "assignment-only",
        5,
        [
            record(
                "MOVE",
                agency="MOVE-C",
                created_date="2026-02-01T12:00:00",
                closed_date="2026-02-02T12:00:00",
            )
        ],
    )
    build(("+" + MODEL,))
    assert rows(db) == initial and frozen(db, RAW) == original
    load(
        db,
        "correct-lifecycle",
        6,
        [record("MOVE", agency="MOVE-A", closed_date="2026-01-03T12:00:00")],
    )
    assert rows(db) == initial
    build(("+sat_request_lifecycle",))
    latest = rows(db)
    assert latest != initial
    load(
        db,
        "safe-late",
        2,
        [record("MOVE", agency="LATE", closed_date="2026-01-04T12:00:00")],
    )
    build(("+sat_request_lifecycle",))
    assert rows(db) == latest
    load(
        db,
        "a-identical-tie",
        6,
        [
            record(
                "MOVE",
                agency="MOVE-A",
                closed_date="2026-01-03T12:00:00",
                descriptor="Untracked detail",
            )
        ],
    )
    build(("+" + MODEL,))
    assert rows(db) == latest
    final = frozen(db, RAW)
    assert (
        final["hub_request"] == original["hub_request"]
        and final["sat_request_status"] == original["sat_request_status"]
    )
    assert all(
        v in final["sat_request_lifecycle"] for v in original["sat_request_lifecycle"]
    )
    return {"db": db, "project": project, "raw": final}


def group(warehouse, agency, month=date(2026, 1, 1), source="synthetic_311"):
    return next(
        r
        for r in rows(warehouse["db"])
        if r["observed_agency_bk"] == agency
        and r["created_cohort_month_nyc"] == month
        and r["record_source"] == source
    )


def test_every_group_matches_python_population_and_quantiles(warehouse):
    assert_independent(warehouse["db"])
    assert frozen(warehouse["db"], RAW) == warehouse["raw"]


def test_known_quantiles_and_different_denominators(warehouse):
    r = group(warehouse, "AGG")
    assert r["retained_request_count"] == 6
    assert r["usable_closure_date_count"] == 5
    assert r["duration_eligible_count"] == 4 and r["duration_ineligible_count"] == 2
    assert r["missing_closure_date_count"] == r["closed_before_created_count"] == 1
    assert r["reported_closure_date_share"] == pytest.approx(5 / 6)
    assert r["duration_coverage_share"] == pytest.approx(4 / 6)
    assert r["total_resolution_hours"] == 13 and r["mean_resolution_hours"] == 3.25
    assert r["median_resolution_hours"] == 2 and r[
        "p90_resolution_hours"
    ] == pytest.approx(7.2)
    live = group(warehouse, "AGG", source="NYC311:erm2-nwe9")
    assert live["retained_request_count"] == 1 and live["median_resolution_hours"] == 2


@pytest.mark.parametrize("agency", ["NONE", "BAD"])
def test_no_eligible_duration_is_null_statistics(warehouse, agency):
    r = group(warehouse, agency)
    assert r["duration_eligible_count"] == 0 and r["duration_coverage_share"] == 0
    assert all(
        r[c] is None
        for c in [
            "total_resolution_hours",
            "mean_resolution_hours",
            "median_resolution_hours",
            "p90_resolution_hours",
        ]
    )
    if agency == "BAD":
        assert (
            r["unusable_closure_date_count"] == 3 and r["retained_request_count"] == 3
        )


def test_zero_duration_is_eligible(warehouse):
    r = group(warehouse, "ZERO")
    assert r["duration_eligible_count"] == 1 and r["duration_coverage_share"] == 1
    assert (
        r["mean_resolution_hours"]
        == r["median_resolution_hours"]
        == r["p90_resolution_hours"]
        == 0
    )


def test_unknown_month_nyc_midnight_and_dst(warehouse):
    r = group(warehouse, "COHORT", month=None)
    assert r["retained_request_count"] == 2 and r["is_cohort_month_known"] is False
    assert r["reported_closure_date_share"] == 1 and r["duration_coverage_share"] == 0
    assert group(warehouse, "MID")["mean_resolution_hours"] == 1
    assert group(warehouse, "DST", date(2026, 3, 1))[
        "median_resolution_hours"
    ] == pytest.approx(2 / 60)
    assert group(warehouse, "DST", date(2026, 11, 1))["median_resolution_hours"] == 3
    assert group(warehouse, "DUE")["duration_eligible_count"] == 1


@pytest.mark.parametrize("agency", [None, "", " \t", " DÉP|; "])
def test_exact_nullable_agency_groups(warehouse, agency):
    r = group(warehouse, agency)
    assert r["retained_request_count"] == 1
    assert r["has_missing_agency"] == (agency is None or not agency.strip())


def test_capture_context_cohort_restatement_and_timezone(warehouse):
    r = group(warehouse, "MOVE-A")
    assert r["mean_resolution_hours"] == 48
    assert (
        r["min_selected_lifecycle_capture_at_utc"].day
        == r["max_selected_lifecycle_capture_at_utc"].day
        == 6
    )
    assert not any(
        r["observed_agency_bk"] in ["MOVE-B", "MOVE-C", "LATE"]
        for r in rows(warehouse["db"])
    )
    with duckdb.connect(str(warehouse["db"]), read_only=True) as con:
        original = con.sql(
            "SELECT * FROM marts.mart_agency_performance ORDER BY ALL"
        ).fetchall()
        con.execute("SET TimeZone='America/New_York'")
        assert (
            con.sql(
                "SELECT * FROM marts.mart_agency_performance ORDER BY ALL"
            ).fetchall()
            == original
        )


def clone(warehouse, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(warehouse["db"], db)
    return db


@pytest.mark.parametrize(
    "case",
    ["duplicate", "missing", "agency", "cohort", "counter", "duration", "lineage"],
)
def test_population_drift_fails_before_publish(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        before = con.sql(
            "SELECT sql FROM duckdb_views() WHERE view_name='mart_agency_performance'"
        ).fetchone()
        original = con.sql(
            "SELECT sql FROM duckdb_views() WHERE view_name='int_request_trend_population'"
        ).fetchone()[0]
        con.execute(
            original.replace("int_request_trend_population", "population_original", 1)
        )
        q = {
            "duplicate": "SELECT * FROM marts.population_original UNION ALL SELECT * FROM marts.population_original",
            "missing": "SELECT * FROM marts.population_original WHERE request_bk<>'AGG-1'",
            "agency": "SELECT * REPLACE ('WRONG' AS observed_agency_bk) FROM marts.population_original",
            "cohort": "SELECT * REPLACE (DATE '2000-01-01' AS created_cohort_month_nyc) FROM marts.population_original",
            "counter": "SELECT * REPLACE (99::bigint AS duration_eligible_count) FROM marts.population_original",
            "duration": "SELECT * REPLACE (resolution_hours+1 AS resolution_hours) FROM marts.population_original",
            "lineage": "SELECT * REPLACE ('wrong-batch' AS dimension_batch_id) FROM marts.population_original",
        }[case]
        con.execute("CREATE OR REPLACE VIEW marts.int_request_trend_population AS " + q)
    raw = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=(MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert (
        "Agency performance requires complete unique request population"
        in result.stdout + result.stderr
    )
    assert frozen(db, RAW) == raw
    with duckdb.connect(str(db), read_only=True) as con:
        assert (
            con.sql(
                "SELECT sql FROM duckdb_views() WHERE view_name='mart_agency_performance'"
            ).fetchone()
            == before
        )


@pytest.mark.parametrize("case", ["landing", "raw"])
def test_input_guards_remain_active(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        if case == "landing":
            con.execute(
                "UPDATE landing.ingestion_batch SET status='failed' WHERE batch_id='correct-lifecycle'"
            )
        else:
            con.execute("UPDATE raw_vault.sat_request_lifecycle SET hashdiff='bad'")
    before = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=(MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert (
        "matching successful lifecycle landing observation"
        if case == "landing"
        else "canonical Raw Vault lifecycle hashes"
    ) in result.stdout + result.stderr
    assert frozen(db, RAW) == before


def test_hub_only_retained_as_unknown_month_and_context(warehouse, tmp_path):
    from test_raw_vault import digest

    db = clone(warehouse, tmp_path)
    hk = digest(["synthetic_311", "HUB-ONLY"], "request_hk")
    with duckdb.connect(str(db)) as con:
        con.execute(
            "INSERT INTO raw_vault.hub_request VALUES (?, 'HUB-ONLY','synthetic_311','synthetic',current_timestamp)",
            [hk],
        )
    result = execute_dbt(
        warehouse["project"], db, tmp_path / "profile", selectors=(MODEL,)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_independent(db)
    r = next(
        r
        for r in rows(db)
        if r["record_source"] == "synthetic_311"
        and r["observed_agency_bk"] is None
        and r["created_cohort_month_nyc"] is None
    )
    assert r["retained_request_count"] == r["unobserved_lifecycle_count"] == 1
    assert r["reported_closure_date_share"] == r["duration_coverage_share"] == 0
    assert (
        r["min_selected_lifecycle_capture_at_utc"] is None
        and r["median_resolution_hours"] is None
    )


def test_empty_fresh_dependency_build(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    db = tmp_path / "warehouse.db"
    load(db, "empty", 1, [])
    result = execute_dbt(project, db, tmp_path / "profile", selectors=("+" + MODEL,))
    assert result.returncode == 0, result.stdout + result.stderr
    assert rows(db) == []
