"""Request-grain reporting population, event dates and valid-duration denominators."""

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from test_assignments import frozen, load
from test_lifecycle_metrics import assert_independent as assert_lifecycle
from test_lifecycle_metrics import record
from test_raw_vault import digest, execute_dbt
from test_staging import edge_records

from ingestion.fixtures import synthetic_batches
from ingestion.landing import load_batch

ROOT = Path(__file__).resolve().parents[1]
MODEL = "mart_resolution_times"
RAW = ["hub_request", "sat_request_status", "sat_request_lifecycle"]
REPORTING = [
    "created_event_date_nyc",
    "closed_event_date_nyc",
    "created_cohort_month_nyc",
    "request_count",
    "duration_eligible_count",
    "duration_ineligible_count",
    "created_event_eligible_count",
    "closed_event_eligible_count",
]


def rows(db, relation="marts.mart_resolution_times"):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.sql(f"SELECT * FROM {relation} ORDER BY request_hk")
        return [
            dict(zip([c[0] for c in cur.description], row, strict=True))
            for row in cur.fetchall()
        ]


def assert_independent(db):
    assert_lifecycle(db)
    upstream = rows(db, "business_vault.bv_request_resolution_metrics")
    actual = rows(db)
    assert len(actual) == len(upstream) == len({r["request_hk"] for r in actual})
    for row, original in zip(actual, upstream, strict=True):
        assert {k: v for k, v in row.items() if k not in REPORTING} == original
        for prefix in ["created", "closed"]:
            local = original[f"{prefix}_at_nyc"]
            expected = (
                local.date()
                if original[f"{prefix}_date_time_status"] == "valid"
                else None
            )
            assert row[f"{prefix}_event_date_nyc"] == expected
            assert row[f"{prefix}_event_eligible_count"] == int(expected is not None)
        d = row["created_event_date_nyc"]
        assert row["created_cohort_month_nyc"] == (d.replace(day=1) if d else None)
        assert row["request_count"] == 1
        assert row["duration_eligible_count"] == int(original["is_duration_valid"])
        assert row["duration_ineligible_count"] == 1 - row["duration_eligible_count"]


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("resolution-mart")
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
        "cases",
        1,
        edge_records()
        + [
            record("AGG-0", closed_date="2026-01-01T12:00:00"),
            record("AGG-1", closed_date="2026-01-01T13:00:00"),
            record("AGG-3", closed_date="2026-01-01T15:00:00"),
            record("AGG-9", closed_date="2026-01-01T21:00:00"),
            record("AGG-missing", closed_date=None),
            record("AGG-negative", closed_date="2026-01-01T11:00:00"),
            record(
                "MIDNIGHT",
                created_date="2026-01-31T23:30:00",
                closed_date="2026-02-01T00:30:00",
            ),
            record("CHANGE"),
            record("EMPTY-CREATED", created_date="", closed_date="2026-01-02T12:00:00"),
            record("ALL-NULL", created_date=None, closed_date=None),
            record("BAD-CLOSED", closed_date="bad"),
            record("DUE-BAD", due_date="bad"),
        ],
    )
    load(db, "clear", 3, [record("CHANGE", closed_date=None)])
    load_batch(
        database_path=db,
        batch_id="live-namespace",
        records=[record("AGG-1", closed_date="2026-01-01T14:00:00")],
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

    build(("+bv_request_resolution_metrics", "+sat_request_status"))
    original = frozen(db, RAW)
    build()
    first = rows(db)
    build()
    assert rows(db) == first and frozen(db, RAW) == original
    load(db, "unchanged", 4, [record("CHANGE", closed_date=None)])
    load_batch(database_path=db, **synthetic_batches()[0])
    build(("+" + MODEL,))
    assert rows(db) == first and frozen(db, RAW) == original
    load(
        db,
        "latest-correction",
        6,
        [record("CHANGE", closed_date="2026-01-03T12:00:00")],
    )
    assert rows(db) == first
    build(("+sat_request_lifecycle",))
    changed = rows(db)
    assert changed != first  # Existing mart follows loaded evidence without rebuilding.
    load(
        db,
        "earlier-loaded-later",
        2,
        [record("CHANGE", closed_date="2026-01-04T12:00:00")],
    )
    build(("+sat_request_lifecycle",))
    assert rows(db) == changed
    final = frozen(db, RAW)
    assert final["hub_request"] == original["hub_request"]
    assert final["sat_request_status"] == original["sat_request_status"]
    assert all(
        v in final["sat_request_lifecycle"] for v in original["sat_request_lifecycle"]
    )
    build()
    assert rows(db) == changed and frozen(db, RAW) == final
    return {"db": db, "project": project, "raw": final}


def get(warehouse, key, source="synthetic_311"):
    return next(
        r
        for r in rows(warehouse["db"])
        if r["request_hk"] == digest([source, key], "request_hk")
    )


def test_full_population_dates_denominators_and_provenance(warehouse):
    assert_independent(warehouse["db"])
    assert frozen(warehouse["db"], RAW) == warehouse["raw"]


def test_nyc_calendar_dates_are_not_utc_dates(warehouse):
    r = get(warehouse, "MIDNIGHT")
    assert r["created_event_date_nyc"] == date(2026, 1, 31)
    assert r["created_at_utc"].date() == date(2026, 2, 1)
    assert r["closed_event_date_nyc"] == date(2026, 2, 1)
    assert r["created_cohort_month_nyc"] == date(2026, 1, 1)
    assert r["resolution_hours"] == 1


@pytest.mark.parametrize(
    "key,created,closed,eligible",
    [
        ("AGG-0", 1, 1, 1),
        ("AGG-missing", 1, 0, 0),
        ("AGG-negative", 1, 1, 0),
        ("EMPTY-CREATED", 0, 1, 0),
        ("ALL-NULL", 0, 0, 0),
        ("BAD-CLOSED", 1, 0, 0),
        ("ambiguous", 0, 1, 0),
        ("nonexistent", 0, 0, 0),
        ("DUE-BAD", 1, 1, 1),
    ],
)
def test_event_and_duration_populations_are_distinct(
    warehouse, key, created, closed, eligible
):
    r = get(warehouse, key)
    assert r["request_count"] == 1
    assert r["created_event_eligible_count"] == created
    assert r["closed_event_eligible_count"] == closed
    assert r["duration_eligible_count"] == eligible
    assert r["duration_ineligible_count"] == 1 - eligible
    if key == "AGG-negative":
        assert (
            r["reported_closure_elapsed_hours"] == -1 and r["resolution_hours"] is None
        )
    if key == "DUE-BAD":
        assert r["has_lifecycle_date_quality_issue"] is True


def test_scoped_aggregates_keep_zero_and_exclude_invalid_duration(warehouse):
    with duckdb.connect(str(warehouse["db"]), read_only=True) as con:
        result = con.sql(
            "SELECT sum(request_count),sum(duration_eligible_count),sum(duration_ineligible_count),sum(created_event_eligible_count),sum(closed_event_eligible_count),sum(resolution_hours),avg(resolution_hours),median(resolution_hours),quantile_cont(resolution_hours,0.9) FROM marts.mart_resolution_times WHERE record_source='synthetic_311' AND mode='synthetic' AND request_bk LIKE 'AGG-%'"
        ).fetchone()
        assert result[:5] == (6, 4, 2, 6, 5)
        assert result[5:8] == (13, 3.25, 2)
        assert result[8] == pytest.approx(7.2)
        excluded = con.sql(
            "SELECT sum(request_count), sum(duration_eligible_count), avg(resolution_hours), median(resolution_hours), quantile_cont(resolution_hours,0.9) FROM marts.mart_resolution_times WHERE record_source='synthetic_311' AND request_bk='AGG-missing'"
        ).fetchone()
        assert excluded == (1, 0, None, None, None)

    assert get(warehouse, "AGG-1", "NYC311:erm2-nwe9")["resolution_hours"] == 2
    assert get(warehouse, "AGG-1")["resolution_hours"] == 1
    assert get(warehouse, "CHANGE")["resolution_hours"] == 48
    assert get(warehouse, "CHANGE")["snapshot_at_utc"].day == 6


def test_dst_quality_types_and_session_timezone(warehouse):
    assert get(warehouse, "spring_span")["resolution_hours"] == pytest.approx(2 / 60)
    assert get(warehouse, "fall_span")["resolution_hours"] == 3
    assert get(warehouse, "ambiguous")["created_event_date_nyc"] is None
    with duckdb.connect(str(warehouse["db"]), read_only=True) as con:
        original = con.sql(
            "SELECT * FROM marts.mart_resolution_times ORDER BY request_hk"
        ).fetchall()
        con.execute("SET TimeZone='America/New_York'")
        assert (
            con.sql(
                "SELECT * FROM marts.mart_resolution_times ORDER BY request_hk"
            ).fetchall()
            == original
        )
        types = dict(
            con.sql(
                "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='marts' AND table_name='mart_resolution_times'"
            ).fetchall()
        )
        for c in REPORTING[:3]:
            assert types[c] == "DATE"
        for c in REPORTING[3:]:
            assert types[c] == "BIGINT"
        for c in ["snapshot_at_utc", "load_dts_utc", "created_at_utc", "closed_at_utc"]:
            assert types[c] == "TIMESTAMP WITH TIME ZONE"


def clone(warehouse, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(warehouse["db"], db)
    return db


@pytest.mark.parametrize(
    "case", ["duplicate", "missing", "duration", "metadata", "quality"]
)
def test_bad_upstream_metrics_fail_without_replacing_mart(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        before = con.sql(
            "SELECT sql FROM duckdb_views() WHERE schema_name='marts' AND view_name='mart_resolution_times'"
        ).fetchone()
        original = con.sql(
            "SELECT sql FROM duckdb_views() WHERE schema_name='business_vault' AND view_name='bv_request_resolution_metrics'"
        ).fetchone()[0]
        # Preserve the real metric SQL as a separately named view, then inject drift.
        con.execute(
            original.replace("bv_request_resolution_metrics", "metric_original", 1)
        )
        query = {
            "duplicate": "SELECT * FROM business_vault.metric_original UNION ALL SELECT * FROM business_vault.metric_original LIMIT 10000",
            "missing": "SELECT * FROM business_vault.metric_original WHERE request_bk<>'AGG-1'",
            "duration": "SELECT * REPLACE (resolution_hours+1 AS resolution_hours) FROM business_vault.metric_original",
            "metadata": "SELECT * REPLACE ('bad-batch' AS batch_id) FROM business_vault.metric_original",
            "quality": "SELECT * REPLACE ('valid' AS duration_quality_status) FROM business_vault.metric_original",
        }[case]
        con.execute(
            "CREATE OR REPLACE VIEW business_vault.bv_request_resolution_metrics AS "
            + query
        )
    raw = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=(MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert "complete unique latest lifecycle metrics" in result.stdout + result.stderr
    assert frozen(db, RAW) == raw
    with duckdb.connect(str(db), read_only=True) as con:
        assert (
            con.sql(
                "SELECT sql FROM duckdb_views() WHERE schema_name='marts' AND view_name='mart_resolution_times'"
            ).fetchone()
            == before
        )


@pytest.mark.parametrize("case", ["hash", "interpretation"])
def test_corrupt_raw_lifecycle_is_rejected(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        if case == "hash":
            con.execute("UPDATE raw_vault.sat_request_lifecycle SET hashdiff='bad'")
        else:
            con.execute(
                "UPDATE raw_vault.sat_request_lifecycle SET created_at_utc=created_at_utc+INTERVAL '1 hour'"
            )
    before = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=(MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert "Lifecycle metrics require" in result.stdout + result.stderr
    assert frozen(db, RAW) == before


def test_hub_without_lifecycle_retains_population_row(warehouse, tmp_path):
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
    r = next(r for r in rows(db) if r["request_hk"] == hk)
    assert r["request_count"] == r["duration_ineligible_count"] == 1
    assert r["duration_quality_status"] == "lifecycle_unobserved"


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
