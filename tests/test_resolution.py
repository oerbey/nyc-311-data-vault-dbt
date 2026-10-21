"""Offline resolution historization with exact raw text and capture-time ordering."""

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
TABLE = "sat_request_resolution"
OLD = [
    "hub_request",
    "sat_request_status",
    "hub_agency",
    "hub_problem",
    "lnk_request_agency",
    "lnk_request_problem",
    "sat_lnk_request_agency_effectivity",
    "sat_lnk_request_problem_effectivity",
    "sat_request_lifecycle",
]
UPDATED = "2026-01-03T12:00:00"
OLDER = "2026-01-01T12:00:00"


def resolution_row(key, description=None, updated=None, **changes):
    return (
        row(
            key,
            created_date="2026-01-01T09:00:00",
            resolution_description=description,
            resolution_action_updated_date=updated,
        )
        | changes
    )


def records(db, key):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.execute(
            "SELECT * FROM raw_vault.sat_request_resolution WHERE request_hk=? ORDER BY snapshot_at_utc",
            [digest(["synthetic_311", key], "request_hk")],
        )
        return [
            dict(zip([c[0] for c in cur.description], r, strict=True))
            for r in cur.fetchall()
        ]


@pytest.fixture(scope="module")
def resolution(tmp_path_factory):
    folder = tmp_path_factory.mktemp("resolution")
    db = folder / "warehouse.db"
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    for batch in synthetic_batches():
        load_batch(database_path=db, **batch)
    load(
        db,
        "quality",
        1,
        [
            resolution_row(
                "quality-" + r["unique_key"], "Fictional inspection", r["created_date"]
            )
            for r in edge_records()
        ],
    )
    load(
        db,
        "first",
        1,
        [
            resolution_row("RS", "Fictional inspection A", UPDATED),
            resolution_row("TEXT"),
            resolution_row("FORMAT", "Fictional", UPDATED),
            resolution_row("LATE"),
            resolution_row("CLEAR", "Fictional", UPDATED),
        ],
    )
    load(
        db,
        "second",
        2,
        [
            resolution_row("RS", "Fictional inspection B", UPDATED),
            resolution_row("TEXT", ""),
            resolution_row("FORMAT", "Fictional", " 2026-01-03 12:00:00 "),
            resolution_row("CLEAR", None, UPDATED),
        ],
    )
    load(
        db,
        "third",
        3,
        [
            resolution_row("RS", "Fictional inspection B", OLDER),
            resolution_row("TEXT", " \t"),
            resolution_row("CLEAR"),
        ],
    )
    load(
        db,
        "fourth",
        4,
        [
            resolution_row("RS", "Fictional inspection A", UPDATED),
            resolution_row("TEXT", "N;"),
            resolution_row("CLEAR", None, ""),
            resolution_row("LATE", "Later evidence", UPDATED),
        ],
    )
    load(db, "intraday-initial", 6, [resolution_row("INTRADAY", "A", UPDATED)], hour=9)
    load(db, "intraday-change", 6, [resolution_row("INTRADAY", "B", UPDATED)], hour=10)
    # Hash input probes include both non-null components, delimiter ambiguity,
    # leading/trailing whitespace, composed/decomposed Unicode and case.
    load(
        db,
        "hash-probes",
        1,
        [
            resolution_row(f"PROBE-{i}", d, t)
            for i, (d, t) in enumerate(
                [
                    ("a|b", "c"),
                    ("a", "b|c"),
                    ("é", "N;"),
                    ("e\u0301", "N;"),
                    ("A", ""),
                    ("a", ""),
                    (" a ", ""),
                    (None, ""),
                    ("", None),
                ]
            )
        ],
    )

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
            json=[resolution_row("RS", "Other namespace", UPDATED)]
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
            "+sat_request_lifecycle",
            "+sat_lnk_request_agency_effectivity",
            "+sat_lnk_request_problem_effectivity",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    old = frozen(db, OLD)

    def build():
        result = execute_dbt(
            project, db, folder / "profile", selectors=("+sat_request_resolution",)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert frozen(db, OLD) == old

    build()
    initial = frozen(db, [TABLE])
    build()
    assert frozen(db, [TABLE]) == initial
    load(
        db,
        "a-identical-tie",
        2,
        [resolution_row("RS", "Fictional inspection B", UPDATED)],
    )
    load(
        db,
        "unrelated-fields",
        5,
        [
            resolution_row(
                "RS",
                "Fictional inspection A",
                UPDATED,
                created_date="bad",
                closed_date="bad",
                due_date="bad",
                status="Unrelated",
                agency="UNRELATED",
                borough="QUEENS",
            )
        ],
    )
    build()
    assert frozen(db, [TABLE]) == initial
    load(
        db,
        "unicode-newline",
        5,
        [
            resolution_row("TEXT", " é;|:\nFictional\r\n detail "),
            resolution_row("CLEAR", None, " \t"),
        ],
    )
    load(
        db,
        "clear-return",
        6,
        [
            resolution_row("TEXT"),
            resolution_row("RS"),
            resolution_row("CLEAR", None, "bad"),
        ],
    )
    build()
    before_late = frozen(db, [TABLE])
    load(db, "late-additive", 2, [resolution_row("LATE", "Earlier capture", OLDER)])
    build()
    after_late = frozen(db, [TABLE])
    assert len(after_late[TABLE]) == len(before_late[TABLE]) + 1
    assert all(r in after_late[TABLE] for r in before_late[TABLE])
    build()
    assert frozen(db, [TABLE]) == after_late
    return {"db": db, "project": project, "old": old}


def test_adjacent_changes_reversal_and_source_time_order(resolution):
    history = records(resolution["db"], "RS")
    assert [
        (r["resolution_description"], r["resolution_action_updated_date"])
        for r in history
    ] == [
        ("Fictional inspection A", UPDATED),
        ("Fictional inspection B", UPDATED),
        ("Fictional inspection B", OLDER),
        ("Fictional inspection A", UPDATED),
        (None, None),
    ]
    assert [r["snapshot_at_utc"].day for r in history] == [1, 2, 3, 4, 6]
    assert history[0]["hashdiff"] == history[3]["hashdiff"]
    assert history[1]["batch_id"] == "second"
    assert (
        history[2]["resolution_action_updated_at_utc"]
        < history[1]["resolution_action_updated_at_utc"]
    )


def test_text_preserves_null_blank_unicode_newlines_and_return(resolution):
    history = records(resolution["db"], "TEXT")
    assert [r["resolution_description"] for r in history] == [
        None,
        "",
        " \t",
        "N;",
        " é;|:\nFictional\r\n detail ",
        None,
    ]
    assert history[0]["hashdiff"] == history[-1]["hashdiff"]
    assert len({r["hashdiff"] for r in history}) == 5


def test_date_clearing_blank_and_malformed_transitions(resolution):
    history = records(resolution["db"], "CLEAR")
    assert [r["resolution_action_updated_date"] for r in history] == [
        UPDATED,
        UPDATED,
        None,
        "",
        " \t",
        "bad",
    ]
    assert [r["resolution_description"] for r in history] == [
        "Fictional",
        None,
        None,
        None,
        None,
        None,
    ]
    assert [r["resolution_action_updated_date_time_status"] for r in history] == [
        "valid",
        "valid",
        "missing",
        "missing",
        "missing",
        "invalid_format",
    ]


def test_format_only_change_keeps_raw_evidence(resolution):
    history = records(resolution["db"], "FORMAT")
    assert len(history) == 2
    assert (
        history[0]["resolution_action_updated_at_utc"]
        == history[1]["resolution_action_updated_at_utc"]
    )
    assert history[0]["hashdiff"] != history[1]["hashdiff"]
    assert history[1]["resolution_action_updated_date"] == " 2026-01-03 12:00:00 "


@pytest.mark.parametrize(
    "key,state",
    [
        ("ambiguous", "ambiguous_local_time"),
        ("nonexistent", "nonexistent_local_time"),
        ("malformed", "invalid_format"),
        ("offset", "invalid_format"),
        ("missing", "missing"),
        ("blank", "missing"),
    ],
)
def test_date_quality_preserves_uncertain_and_missing_values(resolution, key, state):
    r = records(resolution["db"], "quality-" + key)[0]
    assert r["resolution_action_updated_date_time_status"] == state
    assert r["resolution_action_updated_at_utc"] is None
    assert r["resolution_action_updated_date_parse_failed"] == (
        state == "invalid_format"
    )
    assert r["has_resolution_date_quality_issue"] == (state != "missing")


def test_typed_date_precision_timezone_and_source_namespace(resolution):
    for key, hour in [("winter", 17), ("summer", 16)]:
        r = records(resolution["db"], "quality-" + key)[0]
        assert r["resolution_action_updated_at_utc"].hour == hour
        assert r["resolution_action_updated_at_nyc"].hour == 12
        assert r["resolution_action_updated_at_nyc"].tzinfo is None
        assert r["source_timezone"] == "America/New_York"
        assert r["snapshot_at_utc"].tzinfo and r["load_dts_utc"].tzinfo
    r = records(resolution["db"], "quality-whitespace")[0]
    assert r["resolution_action_updated_at_utc"].microsecond == 123456
    with duckdb.connect(str(resolution["db"]), read_only=True) as con:
        assert con.execute(
            "SELECT record_source,mode,count(*) FROM raw_vault.sat_request_resolution WHERE request_hk=? GROUP BY ALL",
            [digest(["NYC311:erm2-nwe9", "RS"], "request_hk")],
        ).fetchall() == [("NYC311:erm2-nwe9", "live", 1)]
        results = []
        for zone in ["UTC", "Europe/Riga", "America/Los_Angeles"]:
            con.execute(f"SET TimeZone='{zone}'")
            results.append(
                con.execute(
                    "SELECT resolution_action_updated_at_nyc,resolution_action_updated_at_utc FROM raw_vault.sat_request_resolution WHERE request_hk=?",
                    [digest(["synthetic_311", "quality-winter"], "request_hk")],
                ).fetchone()
            )
        assert results[0] == results[1] == results[2]


def test_all_hashes_match_independent_ordered_utf8_encoding(resolution):
    with duckdb.connect(str(resolution["db"]), read_only=True) as con:
        for hk, source, key, description, updated, hd in con.sql(
            "SELECT s.request_hk,s.record_source,h.request_bk,s.resolution_description,s.resolution_action_updated_date,s.hashdiff FROM raw_vault.sat_request_resolution s JOIN raw_vault.hub_request h USING(request_hk)"
        ).fetchall():
            assert hk == digest([source, key], "request_hk")
            assert hd == digest([description, updated], "request_resolution")
        assert con.sql(
            "SELECT count(DISTINCT hashdiff) FROM raw_vault.sat_request_resolution s JOIN raw_vault.hub_request h USING(request_hk) WHERE request_bk LIKE 'PROBE-%'"
        ).fetchone() == (9,)
    assert frozen(resolution["db"], OLD) == resolution["old"]


def test_intraday_capture_and_before_first_observation(resolution):
    history = records(resolution["db"], "INTRADAY")
    assert [r["snapshot_at_utc"].hour for r in history] == [9, 10]
    assert (
        history[0]["resolution_action_updated_date"]
        == history[1]["resolution_action_updated_date"]
    )
    with duckdb.connect(str(resolution["db"]), read_only=True) as con:
        assert con.execute(
            "SELECT count(*) FROM raw_vault.sat_request_resolution WHERE request_hk=? AND snapshot_at_utc<?",
            [history[0]["request_hk"], datetime(2026, 2, 6, 8, tzinfo=UTC)],
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "case",
    [
        "tie",
        "rewrite",
        "missing_parent",
        "full_refresh",
        "schema_change",
        "interpretation_change",
    ],
)
def test_guards_preserve_all_stored_rows(resolution, tmp_path, case):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(resolution["db"], db)
    if case == "tie":
        load(db, "conflict", 2, [resolution_row("RS", "Conflicting text", UPDATED)])
        message = "Conflicting resolution values"
    elif case == "rewrite":
        load(
            db, "would-retract", 3, [resolution_row("LATE", "Later evidence", UPDATED)]
        )
        message = "Existing resolution history would require rewriting"
    elif case == "missing_parent":
        load(db, "unbuilt", 7, [resolution_row("UNBUILT", "Fictional", UPDATED)])
        message = "Missing or inconsistent resolution Hub parents"
    elif case == "schema_change":
        with duckdb.connect(str(db)) as con:
            con.execute(
                "ALTER TABLE raw_vault.sat_request_resolution ADD COLUMN unexpected_column VARCHAR"
            )
        message = "are out of sync"
    elif case == "interpretation_change":
        with duckdb.connect(str(db)) as con:
            con.execute(
                "UPDATE raw_vault.sat_request_resolution SET source_timezone='Changed policy' WHERE request_hk=?",
                [digest(["synthetic_311", "RS"], "request_hk")],
            )
        message = "Existing resolution history would require rewriting"
    else:
        message = "is insert-only"
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        resolution["project"],
        db,
        tmp_path / "profile",
        selectors=(TABLE,),
        command="run",
        full_refresh=case == "full_refresh",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before


def test_failed_running_legacy_observations_are_excluded(resolution, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(resolution["db"], db)
    with duckdb.connect(str(db)) as con:
        for state in ["failed", "running"]:
            con.execute(
                f"INSERT INTO landing.ingestion_batch BY NAME SELECT * REPLACE ('legacy-{state}' AS batch_id,'{state}' AS status) FROM landing.ingestion_batch WHERE batch_id='first'"
            )
            con.execute(
                f"INSERT INTO landing.request_snapshots SELECT * REPLACE ('legacy-{state}' AS batch_id) FROM landing.request_snapshots WHERE batch_id='first'"
            )
    result = execute_dbt(
        resolution["project"],
        db,
        tmp_path / "profile",
        selectors=(
            "stg_311_request_snapshots",
            "int_request_resolution_observations",
            "int_request_resolution_changes",
        ),
        command="run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.sql(
            "SELECT count(*) FROM staging.int_request_resolution_observations WHERE batch_id LIKE 'legacy-%'"
        ).fetchone() == (0,)


def test_post_insert_error_rolls_back_resolution(resolution, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(resolution["db"], db)
    project = tmp_path / "project"
    shutil.copytree(
        resolution["project"], project, ignore=shutil.ignore_patterns("target", "logs")
    )
    model = project / "models/raw_vault/sat_request_resolution.sql"
    model.write_text(
        model.read_text().replace(
            "full_refresh=false, on_schema_change='fail',",
            "full_refresh=false, on_schema_change='fail', post_hook=\"select error('injected resolution load failure')\",",
        )
    )
    load(
        db, "new-before-error", 7, [resolution_row("RS", "New after missing", UPDATED)]
    )
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=(TABLE,), command="run"
    )
    assert result.returncode != 0
    assert "injected resolution load failure" in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before
