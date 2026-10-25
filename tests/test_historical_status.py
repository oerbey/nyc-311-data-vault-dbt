"""Network-free capture-time status/agency reporting and independent reconstruction."""

import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from test_assignments import frozen, load, row
from test_raw_vault import digest, execute_dbt
from test_request_trends import query
from test_status_pit import configure

from ingestion.fixtures import synthetic_batches
from ingestion.landing import load_batch

ROOT = Path(__file__).resolve().parents[1]
MODEL = "mart_historical_status"
POP = "int_request_historical_status_population"
RAW = [
    "hub_request",
    "hub_agency",
    "lnk_request_agency",
    "sat_request_status",
    "sat_lnk_request_agency_effectivity",
]
INSTANTS = [
    "2026-01-31T00:00:00Z",
    "2026-02-01T08:00:00Z",
    "2026-02-02T07:59:59.999999Z",
    "2026-02-02T08:00:00Z",
    "2026-02-03T08:00:00Z",
    "2026-02-04T08:00:00Z",
    "2026-03-08T06:59:00Z",
    "2026-03-08T07:01:00Z",
]
KEY = [
    "as_of_utc",
    "record_source",
    "mode",
    "is_agency_observed",
    "agency_hk",
    "agency_bk",
    "is_status_observed",
    "status",
    "status_class",
    "dimension_basis",
]


def population(db):
    return query(db, "SELECT * FROM marts." + POP + " ORDER BY ALL")


def rows(db):
    return query(db, "SELECT * FROM marts." + MODEL + " ORDER BY ALL")


def classify(observed, status):
    if not observed:
        return "unobserved"
    if status is None:
        return "observed_null"
    if not status.strip(" \t\n\r\v\f"):
        return "observed_blank"
    return {"Open": "open", "In Progress": "in_progress", "Closed": "closed"}.get(
        status, "other"
    )


def assert_independent(db, instants=None):
    hubs = query(db, "SELECT * FROM raw_vault.hub_request")
    statuses = query(
        db, "SELECT * FROM raw_vault.sat_request_status ORDER BY snapshot_at_utc"
    )
    events = query(
        db,
        "SELECT * FROM raw_vault.sat_lnk_request_agency_effectivity ORDER BY observed_from",
    )
    links = {
        r["link_hk"]: r for r in query(db, "SELECT * FROM raw_vault.lnk_request_agency")
    }
    agencies = {
        r["agency_hk"]: r for r in query(db, "SELECT * FROM raw_vault.hub_agency")
    }
    if instants is None:
        instants = {r["snapshot_at_utc"] for r in statuses}
    expected = {}
    for h in hubs:
        hk = h["request_hk"]
        assert hk == digest([h["record_source"], h["request_bk"]], "request_hk")
        for instant in sorted(set(instants)):
            versions = [
                r
                for r in statuses
                if r["request_hk"] == hk and r["snapshot_at_utc"] <= instant
            ]
            status = versions[-1] if versions else None
            active = {}
            for e in events:
                if e["request_hk"] == hk and e["observed_from"] <= instant:
                    active[e["link_hk"]] = e
            active = [e for e in active.values() if e["active_flag"]]
            assert len(active) <= 1
            event = active[0] if active else None
            link = links[event["link_hk"]] if event else None
            agency = agencies[link["agency_hk"]] if link else None
            value = {
                "request_hk": hk,
                "as_of_utc": instant,
                "record_source": h["record_source"],
                "mode": h["mode"],
                "request_bk": h["request_bk"],
                "is_status_observed": status is not None,
                "status": status["status"] if status else None,
                "is_agency_observed": event is not None,
                "agency_hk": agency["agency_hk"] if agency else None,
                "agency_bk": agency["agency_bk"] if agency else None,
                "agency_link_hk": event["link_hk"] if event else None,
                "dimension_basis": "observed_asof",
            }
            for dest, src in [
                ("status_snapshot_at_utc", "snapshot_at_utc"),
                ("status_load_dts_utc", "load_dts_utc"),
                ("status_hashdiff", "hashdiff"),
                ("status_batch_id", "batch_id"),
            ]:
                value[dest] = status[src] if status else None
            for dest, src in [
                ("agency_observed_from", "observed_from"),
                ("agency_batch_id", "batch_id"),
                ("agency_effectivity_hashdiff", "hashdiff"),
                ("agency_load_dts_utc", "load_dts_utc"),
            ]:
                value[dest] = event[src] if event else None
            value["status_class"] = classify(status is not None, value["status"])
            expected[hk, instant] = value
    actual = population(db)
    assert len(actual) == len(expected)
    for p in actual:
        assert p == expected[p["request_hk"], p["as_of_utc"]]
    groups = defaultdict(list)
    for p in expected.values():
        groups[tuple(p[k] for k in KEY)].append(p)
    report = rows(db)
    assert len(report) == len(groups)
    for r in report:
        members = groups[tuple(r[k] for k in KEY)]
        assert r["request_count"] == len(members)
        for field, predicate in [
            ("status_observed_request_count", lambda p: p["is_status_observed"]),
            ("status_unknown_request_count", lambda p: not p["is_status_observed"]),
            (
                "status_null_request_count",
                lambda p: p["status_class"] == "observed_null",
            ),
            (
                "status_blank_request_count",
                lambda p: p["status_class"] == "observed_blank",
            ),
        ]:
            assert r[field] == sum(predicate(p) for p in members)
        for label, field in [
            ("status", "status_snapshot_at_utc"),
            ("agency", "agency_observed_from"),
        ]:
            captures = [p[field] for p in members if p[field] is not None]
            assert r[f"min_selected_{label}_capture_at_utc"] == (
                min(captures) if captures else None
            )
            assert r[f"max_selected_{label}_capture_at_utc"] == (
                max(captures) if captures else None
            )


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("historical-status")
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    configure(project, INSTANTS)
    db = folder / "warehouse.db"
    for b in synthetic_batches():
        load_batch(database_path=db, **b)
    load(
        db,
        "initial",
        1,
        [
            row("MOVE", "A"),
            row("NULL", None, status=None),
            row("BLANK", " é;| ", status=" \t\n"),
            row("EXACT", "a", status="open"),
            row("LATE", "L1"),
        ],
    )
    load(
        db,
        "move",
        2,
        [
            row("MOVE", "B"),
            row("NULL", "N", status="In Progress"),
            row("EXACT", "a", status=" Open "),
        ],
    )
    load(
        db,
        "return",
        3,
        [
            row("MOVE", "A"),
            row("NULL", None, status=""),
            row("EXACT", "a", status="é;|:"),
            row("LATEAG", "AG3"),
        ],
    )
    load(
        db, "missing-agency", 4, [row("MOVE", None), row("LATE", "L4", status="Closed")]
    )
    for batch, t, state in [
        ("dst-before", "2026-03-08T06:59:00Z", "Open"),
        ("dst-after", "2026-03-08T07:01:00Z", "Closed"),
    ]:
        load_batch(
            database_path=db,
            batch_id=batch,
            records=[row("DST", "DST", status=state)],
            snapshot_at_utc=datetime.fromisoformat(t),
            source_id="synthetic_311",
            record_source="synthetic_311",
            mode="synthetic",
            requested_filter="fictional",
        )
    load_batch(
        database_path=db,
        batch_id="live-namespace",
        records=[row("MOVE", "A", status="Closed")],
        snapshot_at_utc=datetime(2026, 2, 2, 8, tzinfo=UTC),
        source_id="erm2-nwe9",
        record_source="NYC311:erm2-nwe9",
        mode="live",
        requested_filter="fictional",
    )
    with duckdb.connect(str(db)) as con:
        con.execute(
            "UPDATE landing.ingestion_batch SET page_count=1,expected_rows_before=1,expected_rows_after=1 WHERE batch_id='live-namespace'"
        )

    def build(selectors=("+" + MODEL,)):
        result = execute_dbt(project, db, folder / "profile", selectors=selectors)
        assert result.returncode == 0, result.stdout + result.stderr

    build()
    old, before = frozen(db, RAW), population(db)
    build((MODEL,))
    assert frozen(db, RAW) == old and population(db) == before
    load(
        db,
        "safe-late",
        2,
        [
            row("LATE", "L1", status="In Progress"),
            row("LATEAG", "AG2", status="Earlier state"),
        ],
    )
    assert population(db) == before
    build()
    assert population(db) != before
    latest = frozen(db, RAW)
    assert all(all(r in latest[t] for r in old[t]) for t in RAW)
    load(db, "a-identical", 3, [row("MOVE", "A")])
    build()
    assert frozen(db, RAW) == latest
    return {"db": db, "project": project, "raw": latest}


def test_independent_every_identity_instant_and_group(warehouse):
    assert_independent(warehouse["db"], [datetime.fromisoformat(t) for t in INSTANTS])
    assert frozen(warehouse["db"], RAW) == warehouse["raw"]


def test_assignment_only_moves_return_missing_and_unknown(warehouse):
    items = [
        r
        for r in population(warehouse["db"])
        if r["request_bk"] == "MOVE" and r["record_source"] == "synthetic_311"
    ]
    assert [r["agency_bk"] for r in items] == [None, "A", "A", "B", "A", "A", "A", "A"]
    assert [r["status"] for r in items] == [None] + ["Open"] * 7
    assert [r["agency_batch_id"] for r in items][3:6] == ["move", "return", "return"]
    assert not items[0]["is_agency_observed"] and not items[0]["is_status_observed"]
    assert items[4]["status_snapshot_at_utc"].day == 1
    assert items[4]["agency_observed_from"].day == 3


def test_null_blank_other_exact_namespace_and_dst(warehouse):
    data = population(warehouse["db"])
    states = [r for r in data if r["request_bk"] == "NULL"]
    assert [(r["status_class"], r["agency_bk"]) for r in states][:5] == [
        ("unobserved", None),
        ("observed_null", None),
        ("observed_null", None),
        ("in_progress", "N"),
        ("observed_blank", "N"),
    ]
    assert all(
        r["status_class"] == "other"
        for r in data
        if r["request_bk"] == "EXACT" and r["is_status_observed"]
    )
    assert any(r["agency_bk"] == " é;| " and r["status"] == " \t\n" for r in data)
    live = [r for r in data if r["record_source"] == "NYC311:erm2-nwe9"]
    assert [r["status"] for r in live][:4] == [None, None, None, "Closed"]
    dst = [r for r in data if r["request_bk"] == "DST"]
    assert [r["status"] for r in dst][-2:] == ["Open", "Closed"]
    with duckdb.connect(str(warehouse["db"]), read_only=True) as con:
        old = con.sql(
            "SELECT * FROM marts.mart_historical_status ORDER BY ALL"
        ).fetchall()
        con.execute("SET TimeZone='America/New_York'")
        assert (
            con.sql(
                "SELECT * FROM marts.mart_historical_status ORDER BY ALL"
            ).fetchall()
            == old
        )


def clone(warehouse, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copy2(warehouse["db"], db)
    return db


@pytest.mark.parametrize(
    "case",
    [
        "status_hash",
        "hub",
        "link_parent",
        "link_hash",
        "agency_hash",
        "event_hash",
        "missing_end",
        "duplicate",
        "multiple_active",
        "event_parent",
    ],
)
def test_raw_corruption_rejected_before_publish(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    edits = {
        "status_hash": "UPDATE raw_vault.sat_request_status SET hashdiff='bad'",
        "hub": "INSERT INTO raw_vault.hub_request SELECT * FROM raw_vault.hub_request LIMIT 1",
        "link_parent": "UPDATE raw_vault.lnk_request_agency SET agency_hk='bad'",
        "link_hash": "UPDATE raw_vault.lnk_request_agency SET link_hk='bad'",
        "agency_hash": "UPDATE raw_vault.hub_agency SET agency_bk='bad'",
        "event_hash": "UPDATE raw_vault.sat_lnk_request_agency_effectivity SET hashdiff='bad'",
        "missing_end": "DELETE FROM raw_vault.sat_lnk_request_agency_effectivity WHERE NOT active_flag",
        "duplicate": "INSERT INTO raw_vault.sat_lnk_request_agency_effectivity SELECT * FROM raw_vault.sat_lnk_request_agency_effectivity LIMIT 1",
        "multiple_active": "UPDATE raw_vault.sat_lnk_request_agency_effectivity SET observed_from=TIMESTAMPTZ '2026-02-01T08:00:00Z' WHERE active_flag",
        "event_parent": "UPDATE raw_vault.sat_lnk_request_agency_effectivity SET request_hk='bad'",
    }
    with duckdb.connect(str(db)) as con:
        before = con.sql(
            "SELECT sql FROM duckdb_views() WHERE view_name='mart_historical_status'"
        ).fetchone()
        con.execute(edits[case])
    original = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=(MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert (
        "Historical status requires" in result.stdout
        or "requires unique canonical" in result.stdout
        or "Raw Vault status" in result.stdout
    )
    assert frozen(db, RAW) == original
    with duckdb.connect(str(db), read_only=True) as con:
        assert (
            con.sql(
                "SELECT sql FROM duckdb_views() WHERE view_name='mart_historical_status'"
            ).fetchone()
            == before
        )


@pytest.mark.parametrize(
    "case", ["duplicate", "missing", "lineage", "classification", "agency"]
)
def test_population_drift_rejected(warehouse, tmp_path, case):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        sql = con.sql(
            "SELECT sql FROM duckdb_views() WHERE view_name='int_request_historical_status_population'"
        ).fetchone()[0]
        con.execute(sql.replace(POP, "original_population", 1))
        select = {
            "duplicate": "SELECT * FROM marts.original_population UNION ALL SELECT * FROM marts.original_population",
            "missing": "SELECT * FROM marts.original_population WHERE request_bk<>'MOVE'",
            "lineage": "SELECT * REPLACE ('bad' AS agency_batch_id) FROM marts.original_population",
            "classification": "SELECT * REPLACE ('closed' AS status_class) FROM marts.original_population",
            "agency": "SELECT * REPLACE ('bad' AS agency_bk) FROM marts.original_population",
        }[case]
        con.execute("CREATE OR REPLACE VIEW marts." + POP + " AS " + select)
    original = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=(MODEL,),
        command="run",
    )
    assert (
        result.returncode != 0 and "complete unique as-of population" in result.stdout
    )
    assert frozen(db, RAW) == original


@pytest.mark.parametrize(
    "calendar", [None, [], ["2026-02-02T08:00:00Z", "2026-02-02T03:00:00-05:00"]]
)
def test_configured_calendar_requires_pit_rebuild(warehouse, tmp_path, calendar):
    db = clone(warehouse, tmp_path)
    project = tmp_path / "project"
    shutil.copytree(warehouse["project"], project)
    configure(project, calendar)
    before, raw = rows(db), frozen(db, RAW)
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=(MODEL,), command="run"
    )
    assert (
        result.returncode != 0 and "matching the configured calendar" in result.stdout
    )
    assert rows(db) == before
    result = execute_dbt(project, db, tmp_path / "profile", selectors=("+" + MODEL,))
    assert result.returncode == 0, result.stdout + result.stderr
    assert_independent(
        db, None if calendar is None else [datetime.fromisoformat(t) for t in calendar]
    )
    assert frozen(db, RAW) == raw


def test_hub_only_is_retained_unknown(warehouse, tmp_path):
    db = clone(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        con.execute(
            "INSERT INTO raw_vault.hub_request VALUES (?, 'HUB-ONLY','synthetic_311','synthetic',current_timestamp)",
            [digest(["synthetic_311", "HUB-ONLY"], "request_hk")],
        )
    result = execute_dbt(
        warehouse["project"], db, tmp_path / "profile", selectors=(MODEL,)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_independent(db, [datetime.fromisoformat(t) for t in INSTANTS])
    items = [r for r in population(db) if r["request_bk"] == "HUB-ONLY"]
    assert len(items) == len(INSTANTS)
    assert all(
        not r["is_status_observed"] and not r["is_agency_observed"] for r in items
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
    assert rows(db) == [] and population(db) == []


def test_agency_known_while_status_unknown(warehouse, tmp_path):
    db = clone(warehouse, tmp_path)
    hk = digest(["synthetic_311", "MOVE"], "request_hk")
    with duckdb.connect(str(db)) as con:
        con.execute("DELETE FROM raw_vault.sat_request_status WHERE request_hk=?", [hk])
    result = execute_dbt(
        warehouse["project"], db, tmp_path / "profile", selectors=(MODEL,)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_independent(db, [datetime.fromisoformat(t) for t in INSTANTS])
    data = [r for r in population(db) if r["request_hk"] == hk]
    assert all(not r["is_status_observed"] for r in data)
    assert all(r["is_agency_observed"] for r in data[1:])


def test_dependency_full_refresh_stays_blocked(warehouse, tmp_path):
    db = clone(warehouse, tmp_path)
    before = frozen(db, RAW)
    result = execute_dbt(
        warehouse["project"],
        db,
        tmp_path / "profile",
        selectors=("+" + MODEL,),
        full_refresh=True,
        command="run",
    )
    assert result.returncode != 0 and "full-refresh" in result.stdout
    assert frozen(db, RAW) == before
