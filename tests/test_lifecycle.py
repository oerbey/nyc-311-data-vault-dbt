"""Offline integration checks for exact-text lifecycle versions and date quality."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
from test_assignments import frozen, load, row
from test_raw_vault import digest, execute_dbt
from test_staging import edge_records

from ingestion.fixtures import synthetic_batches
from ingestion.landing import SOURCE_FIELDS, load_batch
from ingestion.socrata import DATE_FIELDS, load_live

ROOT = Path(__file__).resolve().parents[1]
TABLE = "sat_request_lifecycle"
OLD = [
    "hub_request",
    "sat_request_status",
    "hub_agency",
    "hub_problem",
    "lnk_request_agency",
    "lnk_request_problem",
    "sat_lnk_request_agency_effectivity",
    "sat_lnk_request_problem_effectivity",
]
CREATED = "2026-01-01T12:00:00"
CLOSED = "2026-01-02T12:00:00"


def lifecycle_row(key, **changes):
    return row(key, created_date=CREATED, closed_date=None, due_date=None) | changes


def records(db, key):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.execute(
            "SELECT * FROM raw_vault.sat_request_lifecycle WHERE request_hk=? ORDER BY snapshot_at_utc",
            [digest(["synthetic_311", key], "request_hk")],
        )
        return [
            dict(zip([c[0] for c in cur.description], r, strict=True))
            for r in cur.fetchall()
        ]


@pytest.fixture(scope="module")
def lifecycle(tmp_path_factory):
    folder = tmp_path_factory.mktemp("lifecycle")
    db = folder / "warehouse.db"
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    for batch in synthetic_batches():
        load_batch(database_path=db, **batch)
    load(db, "date-quality", 1, edge_records())
    load(
        db,
        "initial",
        1,
        [
            lifecycle_row("LC"),
            lifecycle_row("TEXT", created_date=None),
            lifecycle_row("CHANGES", due_date=CLOSED),
            lifecycle_row("LATE", created_date=None),
        ],
    )
    load(
        db,
        "second",
        2,
        [
            lifecycle_row("LC", closed_date=CLOSED),
            lifecycle_row("TEXT", created_date=None, closed_date=""),
            lifecycle_row("CHANGES", due_date="2026-01-03T12:00:00"),
        ],
    )
    load(
        db,
        "third",
        3,
        [
            lifecycle_row("LC"),
            lifecycle_row("TEXT", created_date=None, closed_date=" \t"),
            lifecycle_row(
                "CHANGES",
                created_date="2026-01-01T11:00:00",
                due_date="2026-01-03T12:00:00",
            ),
        ],
    )
    load(
        db,
        "fourth",
        4,
        [
            lifecycle_row("LC"),
            lifecycle_row("TEXT", created_date=None, closed_date="N;"),
            lifecycle_row(
                "CHANGES",
                created_date=" 2026-01-01 11:00:00 ",
                due_date="2026-01-03T12:00:00",
            ),
            lifecycle_row("LATE", created_date=None, closed_date=CLOSED),
        ],
    )

    load(db, "intraday-initial", 6, [lifecycle_row("INTRADAY")], hour=9)
    load(
        db,
        "intraday-closure",
        6,
        [lifecycle_row("INTRADAY", closed_date=CLOSED)],
        hour=10,
    )

    # Exercise source namespaces through the real adapter using a local mock.
    def handler(request):
        if request.url.path.startswith("/api/views/"):
            return httpx.Response(
                200,
                json={
                    "id": "erm2-nwe9",
                    "columns": [
                        {
                            "fieldName": f,
                            "dataTypeName": "calendar_date"
                            if f in DATE_FIELDS
                            else "text",
                        }
                        for f in SOURCE_FIELDS
                    ],
                },
            )
        if request.url.params.get("$select", "").startswith("count("):
            return httpx.Response(200, json=[{"row_count": "1"}])
        return httpx.Response(
            200,
            json=[lifecycle_row("LC", closed_date=CLOSED)]
            if request.url.params["$offset"] == "0"
            else [],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        load_live(database_path=db, batch_id="namespace-fixture", client=client)
    result = execute_dbt(
        project,
        db,
        folder / "profile",
        selectors=(
            "+sat_request_status",
            "+sat_lnk_request_agency_effectivity",
            "+sat_lnk_request_problem_effectivity",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    old = frozen(db, OLD)

    def build():
        result = execute_dbt(
            project, db, folder / "profile", selectors=("+sat_request_lifecycle",)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert frozen(db, OLD) == old

    build()
    initial = frozen(db, [TABLE])
    build()
    assert frozen(db, [TABLE]) == initial
    load(db, "a-identical-tie", 2, [lifecycle_row("LC", closed_date=CLOSED)])
    load(
        db,
        "unrelated-fields",
        5,
        [
            lifecycle_row(
                "LC",
                status="Unrelated status",
                agency="UNRELATED",
                descriptor="Changed detail",
                resolution_action_updated_date="bad",
            )
        ],
    )
    build()
    assert frozen(db, [TABLE]) == initial
    # An actual return to NULL must survive, even though its first hashdiff recurs.
    load(
        db,
        "text-unicode",
        5,
        [lifecycle_row("TEXT", created_date=None, closed_date=" é;|: ")],
    )
    load(db, "text-null-return", 6, [lifecycle_row("TEXT", created_date=None)])
    build()
    before_late = frozen(db, [TABLE])
    load(
        db,
        "late-additive",
        2,
        [lifecycle_row("LATE", created_date=None, closed_date="2026-01-03T12:00:00")],
    )
    build()
    after_late = frozen(db, [TABLE])
    assert len(after_late[TABLE]) == len(before_late[TABLE]) + 1
    assert all(r in after_late[TABLE] for r in before_late[TABLE])
    build()
    assert frozen(db, [TABLE]) == after_late
    return {"db": db, "project": project, "old": old}


def test_closure_and_clearing_preserve_adjacent_versions(lifecycle):
    history = records(lifecycle["db"], "LC")
    assert [r["closed_date"] for r in history] == [None, CLOSED, None]
    assert [r["snapshot_at_utc"].day for r in history] == [1, 2, 3]
    assert history[0]["hashdiff"] == history[2]["hashdiff"]
    assert history[1]["batch_id"] == "second"
    assert [r["is_lifecycle_evaluable"] for r in history] == [False, True, False]
    assert history[1]["closed_before_created"] is False


def test_creation_due_and_format_corrections_are_tracked(lifecycle):
    history = records(lifecycle["db"], "CHANGES")
    assert len(history) == 4
    assert history[0]["due_date"] != history[1]["due_date"]
    assert history[1]["created_date"] != history[2]["created_date"]
    assert history[2]["created_at_utc"] == history[3]["created_at_utc"]
    assert history[2]["hashdiff"] != history[3]["hashdiff"]
    assert history[3]["created_date"] == " 2026-01-01 11:00:00 "


def test_null_blank_sentinel_unicode_and_return_are_distinct(lifecycle):
    history = records(lifecycle["db"], "TEXT")
    assert [r["closed_date"] for r in history] == [
        None,
        "",
        " \t",
        "N;",
        " é;|: ",
        None,
    ]
    assert len({r["hashdiff"] for r in history}) == 5
    assert history[0]["hashdiff"] == history[-1]["hashdiff"]
    for r in history:
        assert r["hashdiff"] == digest(
            [r["created_date"], r["closed_date"], r["due_date"]], "request_lifecycle"
        )


@pytest.mark.parametrize(
    "key,state",
    [
        ("ambiguous", "ambiguous_local_time"),
        ("nonexistent", "nonexistent_local_time"),
        ("malformed", "invalid_format"),
        ("missing", "missing"),
        ("blank", "missing"),
    ],
)
def test_date_quality_survives_vault_storage(lifecycle, key, state):
    r = records(lifecycle["db"], key)[0]
    assert r["created_date_time_status"] == state
    assert r["created_at_utc"] is None
    assert r["created_date_parse_failed"] == (state == "invalid_format")
    assert r["has_lifecycle_date_quality_issue"] == (state not in ["missing", "valid"])
    assert r["closed_before_created"] is None


def test_typed_dates_dst_spans_and_negative_lifecycle(lifecycle):
    for key, seconds in [("spring_span", 120), ("fall_span", 10800)]:
        r = records(lifecycle["db"], key)[0]
        assert (r["closed_at_utc"] - r["created_at_utc"]).total_seconds() == seconds
        assert r["created_at_nyc"].tzinfo is None
        assert r["snapshot_at_utc"].tzinfo is not None
        assert r["load_dts_utc"].tzinfo is not None
        assert r["source_timezone"] == "America/New_York"
    assert records(lifecycle["db"], "negative")[0]["closed_before_created"] is True
    r = records(lifecycle["db"], "optional_issues")[0]
    assert r["closed_date_parse_failed"] is True
    assert r["due_date_time_status"] == "nonexistent_local_time"
    assert r["due_at_nyc"] is not None and r["due_at_utc"] is None
    assert r["has_lifecycle_date_quality_issue"] is True


def test_source_namespace_and_independent_existing_tables(lifecycle):
    with duckdb.connect(str(lifecycle["db"]), read_only=True) as con:
        actual = con.execute(
            "SELECT record_source,mode,count(*) FROM raw_vault.sat_request_lifecycle "
            "WHERE request_hk=? GROUP BY ALL",
            [digest(["NYC311:erm2-nwe9", "LC"], "request_hk")],
        ).fetchall()
        assert actual == [("NYC311:erm2-nwe9", "live", 1)]
        # Independently verify ordered raw inputs, including non-null creation/due
        # values, so swapped inputs cannot pass the NULL-heavy sequence checks.
        for hk, source, key, created, closed, due, hd in con.sql(
            "SELECT s.request_hk,s.record_source,h.request_bk,s.created_date,s.closed_date,s.due_date,s.hashdiff "
            "FROM raw_vault.sat_request_lifecycle s JOIN raw_vault.hub_request h USING(request_hk)"
        ).fetchall():
            assert hk == digest([source, key], "request_hk")
            assert hd == digest([created, closed, due], "request_lifecycle")
    assert frozen(lifecycle["db"], OLD) == lifecycle["old"]


def test_intraday_order_and_before_first_observation(lifecycle):
    history = records(lifecycle["db"], "INTRADAY")
    assert [r["snapshot_at_utc"].hour for r in history] == [9, 10]
    with duckdb.connect(str(lifecycle["db"]), read_only=True) as con:
        assert con.execute(
            "SELECT count(*) FROM raw_vault.sat_request_lifecycle WHERE request_hk=? AND snapshot_at_utc<?",
            [history[0]["request_hk"], datetime(2026, 2, 6, 8, tzinfo=UTC)],
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "case", ["tie", "rewrite", "missing_parent", "full_refresh", "schema_change"]
)
def test_guards_preserve_existing_rows(lifecycle, tmp_path, case):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(lifecycle["db"], db)
    selectors = (TABLE,)
    if case == "tie":
        load(
            db,
            "conflicting-lifecycle",
            2,
            [lifecycle_row("LC", due_date=CLOSED, closed_date=CLOSED)],
        )
        message = "Conflicting lifecycle values"
    elif case == "rewrite":
        load(
            db,
            "would-retract",
            3,
            [lifecycle_row("LATE", created_date=None, closed_date=CLOSED)],
        )
        message = "Existing lifecycle history would require rewriting"
    elif case == "missing_parent":
        load(db, "unbuilt-parent", 7, [lifecycle_row("UNBUILT")])
        message = "Missing or inconsistent lifecycle Hub parents"
    elif case == "schema_change":
        with duckdb.connect(str(db)) as con:
            con.execute(
                "ALTER TABLE raw_vault.sat_request_lifecycle ADD COLUMN unexpected_column VARCHAR"
            )
        message = "are out of sync"
    else:
        message = "is insert-only"
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        lifecycle["project"],
        db,
        tmp_path / "profile",
        selectors=selectors,
        command="run",
        full_refresh=case == "full_refresh",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before


def test_failed_and_running_legacy_observations_are_excluded(lifecycle, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(lifecycle["db"], db)
    with duckdb.connect(str(db)) as con:
        for state in ["failed", "running"]:
            con.execute(
                f"INSERT INTO landing.ingestion_batch BY NAME SELECT * REPLACE ('legacy-{state}' AS batch_id, '{state}' AS status) FROM landing.ingestion_batch WHERE batch_id='initial'"
            )
            con.execute(
                f"INSERT INTO landing.request_snapshots SELECT * REPLACE ('legacy-{state}' AS batch_id) FROM landing.request_snapshots WHERE batch_id='initial'"
            )
    # Run only views: source publication checks correctly reject these deliberately
    # corrupt legacy landing states, while staging still must defensively exclude them.
    result = execute_dbt(
        lifecycle["project"],
        db,
        tmp_path / "profile",
        selectors=(
            "stg_311_request_snapshots",
            "int_request_lifecycle_observations",
            "int_request_lifecycle_changes",
        ),
        command="run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.sql(
            "SELECT count(*) FROM staging.int_request_lifecycle_observations WHERE batch_id LIKE 'legacy-%'"
        ).fetchone() == (0,)


def test_injected_post_insert_failure_rolls_back(lifecycle, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(lifecycle["db"], db)
    project = tmp_path / "project"
    shutil.copytree(
        lifecycle["project"], project, ignore=shutil.ignore_patterns("target", "logs")
    )
    model = project / "models/raw_vault/sat_request_lifecycle.sql"
    model.write_text(
        model.read_text().replace(
            "full_refresh=false, on_schema_change='fail',",
            "full_refresh=false, on_schema_change='fail', post_hook=\"select error('injected lifecycle load failure')\",",
        )
    )
    load(db, "new-version-before-error", 7, [lifecycle_row("LC", due_date=CLOSED)])
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=(TABLE,), command="run"
    )
    assert result.returncode != 0
    assert "injected lifecycle load failure" in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before
