"""NYC source-event trends and coherent lifecycle-capture dimension lineage."""

import shutil
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from test_assignments import frozen, load
from test_lifecycle_metrics import record
from test_raw_vault import digest, execute_dbt
from test_resolution_mart import assert_independent as assert_resolution
from test_resolution_mart import rows as resolution_rows
from test_staging import edge_records

from ingestion.fixtures import synthetic_batches
from ingestion.landing import load_batch

ROOT = Path(__file__).resolve().parents[1]
MODEL = "mart_request_trends"
RAW = ["hub_request", "sat_request_status", "sat_request_lifecycle"]
DIMS = [
    "observed_agency_bk",
    "observed_complaint_type",
    "observed_borough",
    "dimension_batch_id",
    "dimension_snapshot_at_utc",
    "is_dimension_observed",
    "has_missing_agency",
    "has_missing_problem",
    "has_missing_borough",
]


def query(db, sql, parameters=None):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.execute(sql, parameters or [])
        return [
            dict(zip([c[0] for c in cur.description], r, strict=True))
            for r in cur.fetchall()
        ]


def rows(db):
    return query(db, "SELECT * FROM marts.mart_request_trends ORDER BY ALL")


def population(db):
    return query(
        db, "SELECT * FROM marts.int_request_trend_population ORDER BY request_hk"
    )


def assert_independent(db):
    assert_resolution(db)
    originals = resolution_rows(db)
    pop = population(db)
    assert len(pop) == len(originals)
    for r, m in zip(pop, originals, strict=True):
        assert {k: v for k, v in r.items() if k not in DIMS} == m
        evidence = query(
            db,
            "SELECT r.*, b.mode FROM landing.request_snapshots r JOIN landing.ingestion_batch b USING(batch_id) WHERE b.status='success' AND r.batch_id=? AND r.unique_key=? AND r.record_source=? AND r.snapshot_at_utc=?",
            [m["batch_id"], m["request_bk"], m["record_source"], m["snapshot_at_utc"]],
        )
        if not m["is_lifecycle_observed"]:
            assert not evidence and r["is_dimension_observed"] is False
            assert all(r[c] is None for c in DIMS[:5])
            continue
        assert len(evidence) == 1
        e = evidence[0]
        assert r["is_dimension_observed"] is True
        for dest, src in [
            ("observed_agency_bk", "agency"),
            ("observed_complaint_type", "complaint_type"),
            ("observed_borough", "borough"),
            ("dimension_batch_id", "batch_id"),
            ("dimension_snapshot_at_utc", "snapshot_at_utc"),
        ]:
            assert r[dest] == e[src]
        for label, field in [
            ("agency", "agency"),
            ("problem", "complaint_type"),
            ("borough", "borough"),
        ]:
            assert r[f"has_missing_{label}"] == (
                e[field] is None or not e[field].strip()
            )
    expected = Counter()
    expected_events = []
    for r in pop:
        for kind in ["created", "closed"]:
            d = r[f"{kind}_event_date_nyc"]
            if d is None:
                continue
            k = (
                r["record_source"],
                r["mode"],
                d,
                r["observed_agency_bk"],
                r["observed_complaint_type"],
                r["observed_borough"],
                kind,
            )
            expected[k] += 1
            expected_events.append(
                (
                    r["request_hk"],
                    kind,
                    d,
                    r["dimension_batch_id"],
                    r["dimension_snapshot_at_utc"],
                    r["load_dts_utc"],
                )
            )
    actual = Counter()
    for r in rows(db):
        assert r["dimension_basis"] == "lifecycle_capture"
        for kind in ["created", "closed"]:
            n = r[f"{kind}_request_count"]
            assert isinstance(n, int) and n >= 0
            if n:
                actual[
                    (
                        r["record_source"],
                        r["mode"],
                        r["event_date_nyc"],
                        r["observed_agency_bk"],
                        r["observed_complaint_type"],
                        r["observed_borough"],
                        kind,
                    )
                ] += n
    assert actual == expected
    events = query(
        db,
        "SELECT request_hk,event_type,event_date_nyc,dimension_batch_id,dimension_snapshot_at_utc,load_dts_utc FROM marts.int_request_trend_events ORDER BY request_hk,event_type",
    )
    assert [tuple(r.values()) for r in events] == sorted(
        expected_events, key=lambda r: r[:2]
    )


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("request-trends")
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
        edge_records()
        + [
            record("MOVE", agency="DEP", complaint_type="A", borough="BROOKLYN"),
            record(
                "MIDNIGHT",
                agency="MID",
                complaint_type="X",
                borough="Q",
                created_date="2026-01-31T23:30:00",
                closed_date="2026-02-01T00:30:00",
            ),
            record(
                "SAME-DAY",
                agency="SAME",
                complaint_type="X",
                borough=None,
                closed_date="2026-01-01T13:00:00",
            ),
            record("NULL-LABEL", agency=None, complaint_type=None, borough=None),
            record("EMPTY-LABEL", agency="", complaint_type="", borough=""),
            record("BLANK-LABEL", agency=" \t", complaint_type=" ", borough="\t"),
            record(
                "UNICODE",
                agency=" DÉP|; ",
                complaint_type=" é;|:\n ",
                borough="e\u0301",
            ),
            record("CLOSURE-ONLY", created_date=None),
            record("ALL-NULL", created_date=None, closed_date=None),
        ],
    )
    load(
        db,
        "latest",
        4,
        [
            record(
                "MOVE",
                agency="DSNY",
                complaint_type="B",
                borough="QUEENS",
                closed_date=None,
            )
        ],
    )
    load_batch(
        database_path=db,
        batch_id="live-namespace",
        records=[
            record(
                "MOVE", agency="LIVE", complaint_type="Live category", borough="LIVE"
            )
        ],
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

    build(("+mart_resolution_times", "+sat_request_status"))
    before = frozen(db, RAW)
    build(("+" + MODEL,))
    initial = (rows(db), population(db))
    build()
    assert (rows(db), population(db)) == initial and frozen(db, RAW) == before
    # Dimensions remain anchored to lifecycle evidence when assignment alone changes.
    load(
        db,
        "agency-only",
        5,
        [
            record(
                "MOVE",
                agency="OTHER",
                complaint_type="C",
                borough="BRONX",
                closed_date=None,
            )
        ],
    )
    build(("+" + MODEL,))
    assert (rows(db), population(db)) == initial and frozen(db, RAW) == before
    load(
        db,
        "correction",
        6,
        [
            record(
                "MOVE",
                agency="DEP",
                complaint_type="RETURN",
                borough=None,
                created_date="2026-01-03T12:00:00",
                closed_date="2026-01-04T12:00:00",
            )
        ],
    )
    assert (rows(db), population(db)) == initial
    build(("+sat_request_lifecycle",))
    corrected = (rows(db), population(db))
    assert corrected != initial
    load(
        db,
        "a-identical-latest",
        6,
        [
            record(
                "MOVE",
                agency="DEP",
                complaint_type="RETURN",
                borough=None,
                created_date="2026-01-03T12:00:00",
                closed_date="2026-01-04T12:00:00",
                descriptor="Only detail changed",
            )
        ],
    )
    build(("+" + MODEL,))
    assert (rows(db), population(db)) == corrected
    load(
        db,
        "safe-late",
        2,
        [
            record(
                "MOVE",
                agency="LATE",
                complaint_type="OLD",
                borough="LATE",
                closed_date="2026-01-05T12:00:00",
            )
        ],
    )
    build(("+sat_request_lifecycle",))
    assert (rows(db), population(db)) == corrected
    final = frozen(db, RAW)
    assert (
        final["hub_request"] == before["hub_request"]
        and final["sat_request_status"] == before["sat_request_status"]
    )
    assert all(
        r in final["sat_request_lifecycle"] for r in before["sat_request_lifecycle"]
    )
    build()
    assert (rows(db), population(db)) == corrected and frozen(db, RAW) == final
    return {"db": db, "project": project, "raw": final}


def get(warehouse, key, source="synthetic_311"):
    return next(
        r
        for r in population(warehouse["db"])
        if r["request_hk"] == digest([source, key], "request_hk")
    )


def test_independent_populations_dates_groups_and_lineage(warehouse):
    assert_independent(warehouse["db"])
    assert frozen(warehouse["db"], RAW) == warehouse["raw"]


def test_separate_dates_same_day_and_nyc_midnight(warehouse):
    r = get(warehouse, "MIDNIGHT")
    assert r["created_event_date_nyc"] == date(2026, 1, 31) and r[
        "closed_event_date_nyc"
    ] == date(2026, 2, 1)
    groups = [r for r in rows(warehouse["db"]) if r["observed_agency_bk"] == "SAME"]
    assert (
        len(groups) == 1
        and groups[0]["created_request_count"] == groups[0]["closed_request_count"] == 1
    )
    assert groups[0]["event_date_nyc"] == date(2026, 1, 1)
    assert all(r["event_date_nyc"] != date(2026, 2, 6) for r in rows(warehouse["db"]))


@pytest.mark.parametrize(
    "key,created,closed",
    [
        ("missing", 0, 0),
        ("ambiguous", 0, 1),
        ("nonexistent", 0, 0),
        ("negative", 1, 1),
        ("CLOSURE-ONLY", 0, 1),
        ("ALL-NULL", 0, 0),
    ],
)
def test_endpoint_eligibility_preserves_exclusions(warehouse, key, created, closed):
    r = get(warehouse, key)
    events = query(
        warehouse["db"],
        "SELECT event_type FROM marts.int_request_trend_events WHERE request_hk=?",
        [r["request_hk"]],
    )
    assert Counter(e["event_type"] for e in events) == Counter(
        {k: 1 for k, n in [("created", created), ("closed", closed)] if n}
    )
    assert r["request_count"] == 1
    if key in ["negative", "CLOSURE-ONLY"]:
        assert not r["is_duration_valid"]


def test_context_is_selected_lifecycle_evidence_not_latest_assignment(warehouse):
    moved = get(warehouse, "MOVE")
    assert (
        moved["observed_agency_bk"],
        moved["observed_complaint_type"],
        moved["observed_borough"],
    ) == ("DEP", "RETURN", None)
    assert (
        moved["dimension_batch_id"] == "correction"
        and moved["dimension_snapshot_at_utc"].day == 6
    )
    # Unchanged lifecycle on the reassigned fictional request still references Feb 1.
    unchanged = get(warehouse, "DEMO-002")
    assert unchanged["observed_agency_bk"] == "DEP"
    assert unchanged["dimension_batch_id"] == "synthetic-20260201-v1"
    assert get(warehouse, "MOVE", "NYC311:erm2-nwe9")["observed_agency_bk"] == "LIVE"


def test_exact_missing_and_unicode_labels_form_distinct_groups(warehouse):
    for key, agency in [
        ("NULL-LABEL", None),
        ("EMPTY-LABEL", ""),
        ("BLANK-LABEL", " \t"),
        ("UNICODE", " DÉP|; "),
    ]:
        r = get(warehouse, key)
        assert r["observed_agency_bk"] == agency
        assert r["has_missing_agency"] == (key != "UNICODE")
        group = [
            g
            for g in rows(warehouse["db"])
            if g["observed_agency_bk"] == agency
            and g["observed_complaint_type"] == r["observed_complaint_type"]
            and g["observed_borough"] == r["observed_borough"]
            and g["event_date_nyc"] == date(2026, 1, 1)
        ]
        assert len(group) == 1
        assert group[0]["created_request_count"] >= 1
    assert get(warehouse, "UNICODE")["observed_complaint_type"] == " é;|:\n "


def test_totals_reconcile_and_timezone_is_stable(warehouse):
    pop = population(warehouse["db"])
    trends = rows(warehouse["db"])
    for kind in ["created", "closed"]:
        assert sum(r[f"{kind}_request_count"] for r in trends) == sum(
            r[f"{kind}_event_eligible_count"] for r in pop
        )
    with duckdb.connect(str(warehouse["db"]), read_only=True) as con:
        original = con.sql(
            "SELECT * FROM marts.mart_request_trends ORDER BY ALL"
        ).fetchall()
        con.execute("SET TimeZone='America/New_York'")
        assert (
            con.sql("SELECT * FROM marts.mart_request_trends ORDER BY ALL").fetchall()
            == original
        )


def clone(warehouse, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(warehouse["db"], db)
    return db


@pytest.mark.parametrize(
    "case",
    [
        "missing_evidence",
        "failed_batch",
        "raw_date_drift",
        "capture_drift",
        "source_drift",
        "mode_drift",
    ],
)
def test_broken_lifecycle_evidence_fails_before_publish(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        if case == "missing_evidence":
            con.execute(
                "DELETE FROM landing.request_snapshots WHERE batch_id='correction'"
            )
        elif case == "failed_batch":
            con.execute(
                "UPDATE landing.ingestion_batch SET status='failed' WHERE batch_id='correction'"
            )
        elif case == "raw_date_drift":
            con.execute(
                "UPDATE landing.request_snapshots SET created_date='2025-01-01T12:00:00' WHERE batch_id='correction'"
            )
        elif case == "capture_drift":
            con.execute(
                "UPDATE landing.ingestion_batch SET snapshot_at_utc=snapshot_at_utc+INTERVAL '1 hour' WHERE batch_id='correction'"
            )
        elif case == "source_drift":
            con.execute(
                "UPDATE landing.ingestion_batch SET record_source='other-source' WHERE batch_id='correction'"
            )
        else:
            con.execute(
                "UPDATE landing.ingestion_batch SET mode='live' WHERE batch_id='correction'"
            )
        old = con.sql(
            "SELECT sql FROM duckdb_views() WHERE view_name='mart_request_trends'"
        ).fetchone()
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
        "exactly one matching successful lifecycle landing observation"
        in result.stdout + result.stderr
    )
    assert frozen(db, RAW) == raw
    with duckdb.connect(str(db), read_only=True) as con:
        assert (
            con.sql(
                "SELECT sql FROM duckdb_views() WHERE view_name='mart_request_trends'"
            ).fetchone()
            == old
        )


@pytest.mark.parametrize("case", ["duplicate", "date", "count"])
def test_bad_resolution_mart_is_rejected(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        original = con.sql(
            "SELECT sql FROM duckdb_views() WHERE view_name='mart_resolution_times'"
        ).fetchone()[0]
        con.execute(original.replace("mart_resolution_times", "resolution_original", 1))
        q = {
            "duplicate": "SELECT * FROM marts.resolution_original UNION ALL SELECT * FROM marts.resolution_original",
            "date": "SELECT * REPLACE (created_event_date_nyc+1 AS created_event_date_nyc) FROM marts.resolution_original",
            "count": "SELECT * REPLACE (2::bigint AS request_count) FROM marts.resolution_original",
        }[case]
        con.execute("CREATE OR REPLACE VIEW marts.mart_resolution_times AS " + q)
    raw = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=(MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert "Request trends require" in result.stdout + result.stderr
    assert frozen(db, RAW) == raw


def test_hub_only_population_without_invented_events(warehouse, tmp_path):
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
    r = next(r for r in population(db) if r["request_hk"] == hk)
    assert (
        r["is_dimension_observed"] is False
        and r["created_event_eligible_count"] == r["closed_event_eligible_count"] == 0
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
    assert rows(db) == population(db) == []
