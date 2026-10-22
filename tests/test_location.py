"""Offline exact-text location history, replay and incremental safety checks."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
from test_assignments import frozen, load, row
from test_raw_vault import digest, execute_dbt

from ingestion.fixtures import synthetic_batches
from ingestion.landing import SOURCE_FIELDS, load_batch
from ingestion.socrata import DATE_FIELDS, load_live

ROOT = Path(__file__).resolve().parents[1]
TABLE = "sat_request_location"
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
    "sat_request_resolution",
]
FIRST = ("QUEENS", "00123", "01 QUEENS")
SECOND = ("BROOKLYN", "11201", "01 BROOKLYN")


def location_row(key, values=(None, None, None), **changes):
    return (
        row(key, borough=values[0], incident_zip=values[1], community_board=values[2])
        | changes
    )


def records(db, key):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.execute(
            "SELECT * FROM raw_vault.sat_request_location WHERE request_hk=? ORDER BY snapshot_at_utc",
            [digest(["synthetic_311", key], "request_hk")],
        )
        return [
            dict(zip([c[0] for c in cur.description], r, strict=True))
            for r in cur.fetchall()
        ]


def values(history):
    return [(r["borough"], r["incident_zip"], r["community_board"]) for r in history]


@pytest.fixture(scope="module")
def location(tmp_path_factory):
    folder = tmp_path_factory.mktemp("location")
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
        "first",
        1,
        [
            location_row("LOC", FIRST),
            location_row("TEXT"),
            location_row("LATE"),
            location_row("UNCHANGED", FIRST),
            location_row("CLEAR", FIRST),
        ],
    )
    load(
        db,
        "second",
        2,
        [
            location_row("LOC", ("QUEENS", "123", "01 QUEENS")),
            location_row("TEXT", ("", None, None)),
            location_row("CLEAR", ("QUEENS", None, "01 QUEENS")),
        ],
    )
    load(
        db,
        "third",
        3,
        [
            location_row("LOC", ("queens", "123", "01 QUEENS")),
            location_row("TEXT", (" \t", "", None)),
            location_row("CLEAR", ("QUEENS", None, None)),
        ],
    )
    load(
        db,
        "fourth",
        4,
        [
            location_row("LOC", ("queens", "123", "02 QUEENS")),
            location_row("TEXT", ("N;", "NULL", "N;")),
            location_row("LATE", SECOND),
            location_row("CLEAR"),
        ],
    )
    load(db, "intraday-first", 6, [location_row("INTRADAY", FIRST)], hour=9)
    load(db, "intraday-second", 6, [location_row("INTRADAY", SECOND)], hour=10)
    probes = [
        ("A|B", "C", "D"),
        ("A", "B|C", "D"),
        ("é", "00123", "01"),
        ("e\u0301", "00123", "01"),
        ("A", "1", "01"),
        ("a", "1", "01"),
        (" a ", "1", "01"),
        (None, "", None),
        ("", None, None),
        ("Unspecified", "ZIP+4?", "Unknown board"),
    ]
    load(
        db,
        "hash-probes",
        1,
        [location_row(f"PROBE-{i}", v) for i, v in enumerate(probes)],
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
            json=[location_row("LOC", FIRST)]
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
            "+sat_request_resolution",
            "+sat_lnk_request_agency_effectivity",
            "+sat_lnk_request_problem_effectivity",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    old = frozen(db, OLD)

    def build():
        result = execute_dbt(
            project, db, folder / "profile", selectors=("+sat_request_location",)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert frozen(db, OLD) == old

    build()
    initial = frozen(db, [TABLE])
    build()
    assert frozen(db, [TABLE]) == initial
    load(
        db, "a-identical-tie", 2, [location_row("LOC", ("QUEENS", "123", "01 QUEENS"))]
    )
    load(db, "empty-capture", 5, [])
    load(
        db,
        "unrelated-fields",
        5,
        [
            location_row(
                "UNCHANGED",
                FIRST,
                status="Unrelated",
                agency="UNRELATED",
                created_date="bad",
                resolution_description="Changed",
                descriptor="Changed detail",
            )
        ],
    )
    build()
    assert frozen(db, [TABLE]) == initial
    load(
        db,
        "location-return",
        5,
        [
            location_row("LOC", FIRST),
            location_row("TEXT", (" é;|:\n ", " 00123 ", "01;| ")),
        ],
    )
    load(db, "null-return", 6, [location_row("TEXT")])
    build()
    before_late = frozen(db, [TABLE])
    load(db, "late-additive", 2, [location_row("LATE", FIRST)])
    build()
    after_late = frozen(db, [TABLE])
    assert len(after_late[TABLE]) == len(before_late[TABLE]) + 1
    assert all(r in after_late[TABLE] for r in before_late[TABLE])
    build()
    assert frozen(db, [TABLE]) == after_late
    return {"db": db, "project": project, "old": old}


def test_each_attribute_change_and_return_create_adjacent_versions(location):
    history = records(location["db"], "LOC")
    assert values(history) == [
        FIRST,
        ("QUEENS", "123", "01 QUEENS"),
        ("queens", "123", "01 QUEENS"),
        ("queens", "123", "02 QUEENS"),
        FIRST,
    ]
    assert [r["snapshot_at_utc"].day for r in history] == [1, 2, 3, 4, 5]
    assert history[0]["hashdiff"] == history[-1]["hashdiff"]
    assert history[1]["batch_id"] == "second"
    assert history[0]["hashdiff"] != history[1]["hashdiff"]


def test_null_blank_sentinels_unicode_and_linebreaks_survive(location):
    history = records(location["db"], "TEXT")
    assert values(history) == [
        (None, None, None),
        ("", None, None),
        (" \t", "", None),
        ("N;", "NULL", "N;"),
        (" é;|:\n ", " 00123 ", "01;| "),
        (None, None, None),
    ]
    assert len({r["hashdiff"] for r in history}) == 5
    assert history[0]["hashdiff"] == history[-1]["hashdiff"]


def test_missing_fields_are_recorded_without_carry_forward(location):
    assert values(records(location["db"], "CLEAR")) == [
        FIRST,
        ("QUEENS", None, "01 QUEENS"),
        ("QUEENS", None, None),
        (None, None, None),
    ]


def test_unchanged_missing_requests_and_other_attributes_add_no_version(location):
    history = records(location["db"], "UNCHANGED")
    assert len(history) == 1
    assert values(history) == [FIRST]
    assert frozen(location["db"], OLD) == location["old"]


def test_hashes_match_independent_ordered_utf8_contract(location):
    with duckdb.connect(str(location["db"]), read_only=True) as con:
        for hk, source, key, borough, zip_code, board, hd in con.sql(
            "SELECT s.request_hk,s.record_source,h.request_bk,s.borough,s.incident_zip,s.community_board,s.hashdiff FROM raw_vault.sat_request_location s JOIN raw_vault.hub_request h USING(request_hk)"
        ).fetchall():
            assert hk == digest([source, key], "request_hk")
            assert hd == digest([borough, zip_code, board], "request_location")
        assert con.sql(
            "SELECT count(DISTINCT hashdiff) FROM raw_vault.sat_request_location s JOIN raw_vault.hub_request h USING(request_hk) WHERE request_bk LIKE 'PROBE-%'"
        ).fetchone() == (10,)
        types = dict(
            con.sql(
                "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='raw_vault' AND table_name='sat_request_location'"
            ).fetchall()
        )
        assert all(
            types[c] == "VARCHAR"
            for c in ["borough", "incident_zip", "community_board"]
        )
        assert (
            types["snapshot_at_utc"]
            == types["load_dts_utc"]
            == "TIMESTAMP WITH TIME ZONE"
        )
        assert set(types) == {
            "request_hk",
            "snapshot_at_utc",
            "hashdiff",
            "batch_id",
            "record_source",
            "mode",
            "load_dts_utc",
            "borough",
            "incident_zip",
            "community_board",
        }


def test_namespace_separation_and_capture_instants(location):
    with duckdb.connect(str(location["db"]), read_only=True) as con:
        assert con.execute(
            "SELECT record_source,mode,count(*) FROM raw_vault.sat_request_location WHERE request_hk=? GROUP BY ALL",
            [digest(["NYC311:erm2-nwe9", "LOC"], "request_hk")],
        ).fetchall() == [("NYC311:erm2-nwe9", "live", 1)]
        results = []
        for zone in ["UTC", "Europe/Riga", "America/Los_Angeles"]:
            con.execute(f"SET TimeZone='{zone}'")
            results.append(
                con.execute(
                    "SELECT snapshot_at_utc,load_dts_utc FROM raw_vault.sat_request_location WHERE request_hk=?",
                    [digest(["synthetic_311", "UNCHANGED"], "request_hk")],
                ).fetchone()
            )
        assert results[0] == results[1] == results[2]
        assert results[0][0].tzinfo and results[0][1].tzinfo
        assert results[0][0] != results[0][1]


def test_intraday_asof_boundaries_and_unknown_before_capture(location):
    history = records(location["db"], "INTRADAY")
    assert [r["snapshot_at_utc"].hour for r in history] == [9, 10]
    with duckdb.connect(str(location["db"]), read_only=True) as con:
        for hour, expected in [(8, None), (9, FIRST), (10, SECOND), (11, SECOND)]:
            actual = con.execute(
                "SELECT borough,incident_zip,community_board FROM raw_vault.sat_request_location WHERE request_hk=? AND snapshot_at_utc<=? ORDER BY snapshot_at_utc DESC LIMIT 1",
                [history[0]["request_hk"], datetime(2026, 2, 6, hour, tzinfo=UTC)],
            ).fetchone()
            assert actual == expected


@pytest.mark.parametrize(
    "case",
    [
        "borough_tie",
        "zip_tie",
        "board_tie",
        "rewrite",
        "missing_parent",
        "inconsistent_parent",
        "full_refresh",
        "schema_change",
    ],
)
def test_guards_preserve_all_existing_rows(location, tmp_path, case):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(location["db"], db)
    if case.endswith("_tie"):
        v = ["QUEENS", "123", "01 QUEENS"]
        v[{"borough_tie": 0, "zip_tie": 1, "board_tie": 2}[case]] = "Conflicting value"
        load(db, "conflict", 2, [location_row("LOC", tuple(v))])
        message = "Conflicting location values"
    elif case == "rewrite":
        load(db, "would-retract", 3, [location_row("LATE", SECOND)])
        message = "Existing location history would require rewriting"
    elif case == "missing_parent":
        load(db, "unbuilt", 7, [location_row("UNBUILT", FIRST)])
        message = "Missing or inconsistent location Hub parents"
    elif case == "inconsistent_parent":
        with duckdb.connect(str(db)) as con:
            con.execute(
                "UPDATE raw_vault.hub_request SET mode='live' WHERE request_hk=?",
                [digest(["synthetic_311", "LOC"], "request_hk")],
            )
        message = "Missing or inconsistent location Hub parents"
    elif case == "schema_change":
        with duckdb.connect(str(db)) as con:
            con.execute(
                "ALTER TABLE raw_vault.sat_request_location ADD COLUMN unexpected_column VARCHAR"
            )
        message = "are out of sync"
    else:
        message = "is insert-only"
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        location["project"],
        db,
        tmp_path / "profile",
        selectors=(TABLE,),
        command="run",
        full_refresh=case == "full_refresh",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before


def test_failed_and_running_legacy_rows_are_excluded(location, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(location["db"], db)
    with duckdb.connect(str(db)) as con:
        for state in ["failed", "running"]:
            con.execute(
                f"INSERT INTO landing.ingestion_batch BY NAME SELECT * REPLACE ('legacy-{state}' AS batch_id,'{state}' AS status) FROM landing.ingestion_batch WHERE batch_id='first'"
            )
            con.execute(
                f"INSERT INTO landing.request_snapshots SELECT * REPLACE ('legacy-{state}' AS batch_id) FROM landing.request_snapshots WHERE batch_id='first'"
            )
    result = execute_dbt(
        location["project"],
        db,
        tmp_path / "profile",
        selectors=(
            "stg_311_request_snapshots",
            "int_request_location_observations",
            "int_request_location_changes",
        ),
        command="run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.sql(
            "SELECT count(*) FROM staging.int_request_location_observations WHERE batch_id LIKE 'legacy-%'"
        ).fetchone() == (0,)


def test_post_insert_error_rolls_back_location(location, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(location["db"], db)
    project = tmp_path / "project"
    shutil.copytree(
        location["project"], project, ignore=shutil.ignore_patterns("target", "logs")
    )
    model = project / "models/raw_vault/sat_request_location.sql"
    model.write_text(
        model.read_text().replace(
            "full_refresh=false, on_schema_change='fail',",
            "full_refresh=false, on_schema_change='fail', post_hook=\"select error('injected location load failure')\",",
        )
    )
    load(db, "new-before-error", 7, [location_row("LOC", SECOND)])
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=(TABLE,), command="run"
    )
    assert result.returncode != 0
    assert "injected location load failure" in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before
