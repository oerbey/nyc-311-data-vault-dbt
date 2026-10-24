"""Latest-capture, source-date duration measures with independent Python checks."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from test_assignments import frozen, load
from test_raw_vault import digest, execute_dbt
from test_staging import edge_records

from ingestion.fixtures import synthetic_batches
from ingestion.landing import load_batch

ROOT = Path(__file__).resolve().parents[1]
MODEL = "bv_request_resolution_metrics"
RAW = ["hub_request", "sat_request_status", "sat_request_lifecycle"]
CREATED = "2026-01-01T12:00:00"
CLOSED = "2026-01-02T12:00:00"


def record(key, **changes):
    return {
        "unique_key": key,
        "status": "Open",
        "created_date": CREATED,
        "closed_date": CLOSED,
        **changes,
    }


def metrics(db):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.sql(
            "SELECT * FROM business_vault.bv_request_resolution_metrics ORDER BY request_hk"
        )
        return [
            dict(zip([c[0] for c in cur.description], r, strict=True))
            for r in cur.fetchall()
        ]


def assert_independent(db):
    """Select latest by Python capture ordering and subtract actual UTC datetimes."""
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.sql(
            "SELECT * FROM raw_vault.sat_request_lifecycle ORDER BY snapshot_at_utc"
        )
        columns = [c[0] for c in cur.description]
        latest = {r[0]: dict(zip(columns, r, strict=True)) for r in cur.fetchall()}
        hubs = con.sql(
            "SELECT request_hk,request_bk,record_source,mode FROM raw_vault.hub_request"
        ).fetchall()
    actual = metrics(db)
    assert len(actual) == len(hubs) == len({r["request_hk"] for r in actual})
    for hk, key, source, mode in hubs:
        row = next(r for r in actual if r["request_hk"] == hk)
        assert [row[c] for c in ["request_bk", "record_source", "mode"]] == [
            key,
            source,
            mode,
        ]
        version = latest.get(hk)
        if version is None:
            assert row["is_lifecycle_observed"] is False
            assert row["duration_quality_status"] == "lifecycle_unobserved"
            assert row["is_duration_valid"] is False
            assert all(
                row[c] is None
                for c in [
                    "resolution_hours",
                    "resolution_days",
                    "snapshot_at_utc",
                    "batch_id",
                    "load_dts_utc",
                ]
            )
            continue
        assert row["is_lifecycle_observed"] is True
        for c, v in version.items():
            assert row[c] == v, (c, row[c], v)
        start, end = version["created_at_utc"], version["closed_at_utc"]
        hours = (
            (end - start).total_seconds() / 3600
            if start is not None and end is not None
            else None
        )
        assert row["is_duration_evaluable"] == (hours is not None)
        valid = hours is not None and hours >= 0
        assert row["is_duration_valid"] == valid
        if hours is None:
            assert row["reported_closure_elapsed_hours"] is None
        else:
            assert row["reported_closure_elapsed_hours"] == pytest.approx(hours)
        if valid:
            assert row["resolution_hours"] == pytest.approx(hours)
            assert row["resolution_days"] == pytest.approx(hours / 24)
        else:
            assert row["resolution_hours"] is None and row["resolution_days"] is None


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("lifecycle-metrics")
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
        "edge-cases",
        1,
        edge_records()
        + [
            record("ZERO", closed_date=CREATED),
            record(
                "MICRO",
                created_date="2026-01-01T12:00:00.100001",
                closed_date="2026-01-01T12:00:00.200002",
            ),
            record("DUE-BAD", due_date="bad"),
            record("CLOSED-BAD", closed_date="bad"),
            record("CLOSED-FOLD", closed_date="2026-11-01T01:30:00"),
            record("CLOSED-GAP", closed_date="2026-03-08T02:30:00"),
            record("CLEAR"),
            record("CORRECT"),
            record("LATE"),
        ],
    )
    load(db, "clear-date", 3, [record("CLEAR", closed_date=None)])
    load(db, "correct-date", 4, [record("CORRECT", created_date="2026-01-02T06:00:00")])
    load(db, "late-last", 4, [record("LATE", closed_date="2026-01-03T12:00:00")])
    load_batch(
        database_path=db,
        batch_id="namespace",
        records=[record("CORRECT", closed_date="2026-01-04T12:00:00")],
        snapshot_at_utc=datetime(2026, 2, 1, 8, tzinfo=UTC),
        source_id="erm2-nwe9",
        record_source="NYC311:erm2-nwe9",
        mode="live",
        requested_filter="fictional namespace",
    )
    with duckdb.connect(str(db)) as con:
        con.execute(
            "UPDATE landing.ingestion_batch SET page_count=1, expected_rows_before=1, expected_rows_after=1 WHERE batch_id='namespace'"
        )

    load(db, "intraday-first", 6, [record("INTRADAY", closed_date=None)], hour=9)
    load(
        db,
        "intraday-last",
        6,
        [record("INTRADAY", closed_date="2026-01-04T12:00:00")],
        hour=10,
    )

    def build(selectors=(MODEL,)):
        result = execute_dbt(project, db, folder / "profile", selectors=selectors)
        assert result.returncode == 0, result.stdout + result.stderr

    build(("+sat_request_status", "+sat_request_lifecycle"))
    before = frozen(db, RAW)
    build()
    initial = metrics(db)
    build()
    assert metrics(db) == initial and frozen(db, RAW) == before
    load(db, "late-middle", 2, [record("LATE", closed_date="2026-01-05T12:00:00")])
    assert metrics(db) == initial  # Landing-only rows do not change derived metrics.
    build(("+sat_request_lifecycle",))
    assert (
        metrics(db) == initial
    )  # Earlier capture loaded later cannot override latest.
    load(
        db, "correct-latest", 6, [record("CORRECT", closed_date="2026-01-03T12:00:00")]
    )
    assert metrics(db) == initial
    build(("+sat_request_lifecycle",))
    dynamic = metrics(db)
    assert (
        dynamic != initial
    )  # The existing view reflects new Raw evidence without rebuild.
    final = frozen(db, RAW)
    assert final["hub_request"] == before["hub_request"]
    assert final["sat_request_status"] == before["sat_request_status"]
    assert all(
        r in final["sat_request_lifecycle"] for r in before["sat_request_lifecycle"]
    )
    build()
    assert metrics(db) == dynamic and frozen(db, RAW) == final
    return {"db": db, "project": project, "raw": final}


def get(warehouse, key, source="synthetic_311"):
    return next(
        r
        for r in metrics(warehouse["db"])
        if r["request_hk"] == digest([source, key], "request_hk")
    )


def test_all_values_and_original_provenance(warehouse):
    assert_independent(warehouse["db"])
    assert frozen(warehouse["db"], RAW) == warehouse["raw"]


@pytest.mark.parametrize(
    "key,hours",
    [
        ("ZERO", 0),
        ("MICRO", 0.100001 / 3600),
        ("spring_span", 120 / 3600),
        ("fall_span", 3),
        ("DUE-BAD", 24),
        ("CORRECT", 48),
        ("LATE", 48),
    ],
)
def test_elapsed_hours_and_dst(warehouse, key, hours):
    r = get(warehouse, key)
    assert r["resolution_hours"] == pytest.approx(hours)
    assert r["resolution_days"] == pytest.approx(hours / 24)
    assert r["duration_quality_status"] == "valid"
    if key == "DUE-BAD":
        assert r["has_lifecycle_date_quality_issue"] is True


@pytest.mark.parametrize(
    "key,quality",
    [
        ("missing", "missing_created"),
        ("blank", "missing_created"),
        ("malformed", "unusable_created"),
        ("ambiguous", "unusable_created"),
        ("nonexistent", "unusable_created"),
        ("CLEAR", "missing_closed"),
        ("CLOSED-BAD", "unusable_closed"),
        ("CLOSED-FOLD", "unusable_closed"),
        ("CLOSED-GAP", "unusable_closed"),
        ("negative", "closed_before_created"),
    ],
)
def test_invalid_endpoints_are_not_clamped_or_imputed(warehouse, key, quality):
    r = get(warehouse, key)
    assert r["duration_quality_status"] == quality
    assert r["is_duration_valid"] is False
    assert r["resolution_hours"] is None and r["resolution_days"] is None
    if key == "negative":
        assert r["is_duration_evaluable"] is True
        assert r["reported_closure_elapsed_hours"] == -24
    else:
        assert r["is_duration_evaluable"] is False
        assert r["reported_closure_elapsed_hours"] is None


def test_latest_capture_clearing_namespace_and_timezone(warehouse):
    assert get(warehouse, "DEMO-001")["resolution_hours"] is None
    assert get(warehouse, "INTRADAY")["snapshot_at_utc"].hour == 10
    assert get(warehouse, "INTRADAY")["resolution_hours"] == 72
    assert get(warehouse, "CLEAR")["snapshot_at_utc"].day == 3
    assert get(warehouse, "LATE")["snapshot_at_utc"].day == 4
    assert get(warehouse, "CORRECT")["snapshot_at_utc"].day == 6
    assert get(warehouse, "CORRECT", "NYC311:erm2-nwe9")["resolution_hours"] == 72
    with duckdb.connect(str(warehouse["db"]), read_only=True) as con:
        original = con.sql(
            "SELECT * FROM business_vault.bv_request_resolution_metrics ORDER BY request_hk"
        ).fetchall()
        con.execute("SET TimeZone='America/New_York'")
        assert (
            con.sql(
                "SELECT * FROM business_vault.bv_request_resolution_metrics ORDER BY request_hk"
            ).fetchall()
            == original
        )


def clone(warehouse, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(warehouse["db"], db)
    return db


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "null_capture",
        "null_metadata",
        "hash",
        "missing_hub",
        "parent_mode",
        "utc",
        "classification",
        "quality",
        "timezone",
        "duplicate_hub",
    ],
)
def test_corrupt_loaded_inputs_fail_before_view_replacement(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    hk = digest(["synthetic_311", "DUE-BAD"], "request_hk")
    with duckdb.connect(str(db)) as con:
        if case == "duplicate":
            con.execute(
                "INSERT INTO raw_vault.sat_request_lifecycle SELECT * FROM raw_vault.sat_request_lifecycle WHERE request_hk=?",
                [hk],
            )
            message = "unique Raw Vault request/capture"
        elif case == "duplicate_hub":
            con.execute(
                "INSERT INTO raw_vault.hub_request SELECT * FROM raw_vault.hub_request WHERE request_hk=?",
                [hk],
            )
            message = "unique canonical request Hub"
        elif case == "missing_hub":
            con.execute("DELETE FROM raw_vault.hub_request WHERE request_hk=?", [hk])
            message = "consistent request Hub parents"
        elif case == "parent_mode":
            con.execute(
                "UPDATE raw_vault.hub_request SET mode='live' WHERE request_hk=?", [hk]
            )
            message = "consistent request Hub parents"
        else:
            column, value, message = {
                "null_capture": ("snapshot_at_utc", "NULL", "non-null Raw Vault"),
                "null_metadata": ("batch_id", "NULL", "non-null Raw Vault"),
                "hash": ("hashdiff", "'bad'", "canonical Raw Vault lifecycle hashes"),
                "utc": (
                    "created_at_utc",
                    "created_at_utc+INTERVAL '1 hour'",
                    "consistent stored timestamp",
                ),
                "classification": (
                    "closed_date_time_status",
                    "'missing'",
                    "consistent stored timestamp",
                ),
                "quality": (
                    "has_lifecycle_date_quality_issue",
                    "false",
                    "consistent stored timestamp",
                ),
                "timezone": ("source_timezone", "'UTC'", "consistent stored timestamp"),
            }[case]
            con.execute(
                f"UPDATE raw_vault.sat_request_lifecycle SET {column}={value} WHERE request_hk=?",
                [hk],
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
    assert message in result.stdout + result.stderr
    assert frozen(db, RAW) == before


@pytest.mark.parametrize("case", ["tie", "rewrite"])
def test_upstream_guards_preserve_metrics(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    before, previous = frozen(db, RAW), metrics(db)
    if case == "tie":
        load(db, "conflict", 6, [record("CORRECT", closed_date="2026-01-04T12:00:00")])
        message = "Conflicting lifecycle values"
    else:
        load(db, "retraction", 3, [record("LATE", closed_date="2026-01-03T12:00:00")])
        message = "Existing lifecycle history would require rewriting"
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=("+" + MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, RAW) == before and metrics(db) == previous


def test_hub_without_lifecycle_is_unknown(warehouse, tmp_path):
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
    assert metrics(db) == []
