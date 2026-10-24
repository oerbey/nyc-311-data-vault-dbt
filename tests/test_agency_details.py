"""Hub-grain agency names: cross-request agreement, exact text and safe replay."""

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
TABLE = "sat_agency_details"
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
    "sat_request_location",
]


def agency_row(key, agency="TEST-A", name="Initial agency", **changes):
    return row(key, agency, agency_name=name) | changes


def records(db, agency):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.execute(
            "SELECT * FROM raw_vault.sat_agency_details WHERE agency_hk=? ORDER BY snapshot_at_utc",
            [digest(["synthetic_311", agency], "agency_hk")],
        )
        return [
            dict(zip([c[0] for c in cur.description], r, strict=True))
            for r in cur.fetchall()
        ]


@pytest.fixture(scope="module")
def agencies(tmp_path_factory):
    folder = tmp_path_factory.mktemp("agency-details")
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
        "z-initial",
        1,
        [
            agency_row("R2"),
            agency_row("R1"),
            agency_row("TEXT", "TEST-TEXT", None),
            agency_row("LATE", "TEST-LATE", "First"),
            agency_row("MISSING", None, "Named without code"),
            agency_row("BLANK", " \t", "Named without code"),
            agency_row(
                "PROBLEM-MISSING",
                "TEST-ONLY",
                "Agency without category",
                complaint_type=None,
            ),
            agency_row("EXACT", " TEST é;| ", " Exact name;| "),
            agency_row("ZERO", "001", "Same"),
            agency_row("NUM", "1", "Same"),
        ],
    )
    load(db, "zz-initial-extra", 1, [agency_row("R0")])
    load(
        db,
        "renamed",
        2,
        [
            agency_row("R2", name="Renamed agency"),
            agency_row("R1", name="Renamed agency"),
            agency_row("TEXT", "TEST-TEXT", ""),
        ],
    )
    load(
        db,
        "missing-name",
        3,
        [agency_row("R1", name=None), agency_row("TEXT", "TEST-TEXT", " \t")],
    )
    load(
        db,
        "returned",
        4,
        [
            agency_row("R1"),
            agency_row("TEXT", "TEST-TEXT", "N;"),
            agency_row("LATE", "TEST-LATE", "Final"),
        ],
    )
    load(db, "intraday-first", 6, [agency_row("INTRA", "TEST-INTRA", "A")], hour=9)
    load(db, "intraday-second", 6, [agency_row("INTRA", "TEST-INTRA", "B")], hour=10)
    probes = [
        None,
        "",
        "N;",
        "NULL",
        "a",
        "A",
        " a ",
        "é",
        "e\u0301",
        "Name;|:\n detail\r\n",
    ]
    load(
        db,
        "probes",
        1,
        [
            agency_row(f"PROBE-{i}", f"TEST-PROBE-{i}", name)
            for i, name in enumerate(probes)
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
            json=[agency_row("R1", name="Other source name")]
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
            "+sat_request_location",
            "+sat_lnk_request_agency_effectivity",
            "+sat_lnk_request_problem_effectivity",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    old = frozen(db, OLD)

    def build():
        result = execute_dbt(
            project, db, folder / "profile", selectors=("+sat_agency_details",)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert frozen(db, OLD) == old

    build()
    initial = frozen(db, [TABLE])
    build()
    assert frozen(db, [TABLE]) == initial
    # New candidate wins the input tie sort but cannot change stored provenance.
    load(db, "a-identical-tie", 1, [agency_row("R2")])
    load(
        db,
        "unrelated-fields",
        5,
        [
            agency_row(
                "R1",
                status="Unrelated",
                created_date="bad",
                descriptor="Different detail",
                resolution_description="Different",
                borough="QUEENS",
            )
        ],
    )
    load(db, "empty-capture", 5, [])
    build()
    assert frozen(db, [TABLE]) == initial
    load(
        db,
        "unicode-name",
        5,
        [agency_row("TEXT", "TEST-TEXT", " é;|:\n Fictional\r\n name ")],
    )
    load(db, "null-return", 6, [agency_row("TEXT", "TEST-TEXT", None)])
    build()
    before_late = frozen(db, [TABLE])
    load(db, "late-additive", 2, [agency_row("LATE", "TEST-LATE", "Middle")])
    build()
    after_late = frozen(db, [TABLE])
    assert len(after_late[TABLE]) == len(before_late[TABLE]) + 1
    assert all(r in after_late[TABLE] for r in before_late[TABLE])
    build()
    assert frozen(db, [TABLE]) == after_late
    return {"db": db, "project": project, "old": old}


def test_rename_clearing_and_return_preserve_hub_grain(agencies):
    history = records(agencies["db"], "TEST-A")
    assert [r["agency_name"] for r in history] == [
        "Initial agency",
        "Renamed agency",
        None,
        "Initial agency",
    ]
    assert [r["snapshot_at_utc"].day for r in history] == [1, 2, 3, 4]
    assert history[0]["hashdiff"] == history[-1]["hashdiff"]
    with duckdb.connect(str(agencies["db"]), read_only=True) as con:
        assert con.execute(
            "SELECT count(*) FROM raw_vault.hub_agency WHERE agency_hk=?",
            [history[0]["agency_hk"]],
        ).fetchone() == (1,)


def test_cross_request_and_cross_batch_ties_preserve_original_evidence(agencies):
    history = records(agencies["db"], "TEST-A")
    assert (history[0]["batch_id"], history[0]["evidence_request_bk"]) == (
        "z-initial",
        "R1",
    )
    assert (history[1]["batch_id"], history[1]["evidence_request_bk"]) == (
        "renamed",
        "R1",
    )
    with duckdb.connect(str(agencies["db"]), read_only=True) as con:
        assert con.execute(
            "SELECT count(*) FROM staging.int_agency_details_observations WHERE agency_hk=? AND snapshot_at_utc=?",
            [history[0]["agency_hk"], history[0]["snapshot_at_utc"]],
        ).fetchone() == (4,)
    assert frozen(agencies["db"], OLD) == agencies["old"]


def test_null_blank_case_unicode_and_linebreaks_remain_exact(agencies):
    history = records(agencies["db"], "TEST-TEXT")
    assert [r["agency_name"] for r in history] == [
        None,
        "",
        " \t",
        "N;",
        " é;|:\n Fictional\r\n name ",
        None,
    ]
    assert len({r["hashdiff"] for r in history}) == 5
    assert history[0]["hashdiff"] == history[-1]["hashdiff"]
    assert records(agencies["db"], " TEST é;| ")[0]["agency_name"] == " Exact name;| "
    assert (
        records(agencies["db"], "001")[0]["agency_hk"]
        != records(agencies["db"], "1")[0]["agency_hk"]
    )


def test_missing_agency_is_flagged_but_names_and_problem_missing_are_eligible(agencies):
    with duckdb.connect(str(agencies["db"]), read_only=True) as con:
        rows = con.sql(
            "SELECT request_bk,has_missing_agency,has_missing_agency_name,is_agency_details_eligible,agency_hk FROM staging.int_agency_details_observations WHERE batch_id='z-initial'"
        ).fetchall()
        by_key = {r[0]: r[1:] for r in rows}
        assert by_key["MISSING"] == by_key["BLANK"] == (True, False, False, None)
        assert by_key["TEXT"][:3] == (False, True, True)
        assert by_key["PROBLEM-MISSING"][:3] == (False, False, True)
        assert con.sql(
            "SELECT count(*) FROM raw_vault.sat_agency_details WHERE agency_name='Named without code'"
        ).fetchone() == (0,)
        assert (
            con.sql(
                "SELECT count(*) FROM staging.int_agency_details_observations"
            ).fetchone()
            == con.sql(
                "SELECT count(*) FROM staging.stg_311_request_snapshots"
            ).fetchone()
        )
    assert (
        records(agencies["db"], "TEST-ONLY")[0]["agency_name"]
        == "Agency without category"
    )


def test_independent_hashes_namespace_and_parent_coverage(agencies):
    with duckdb.connect(str(agencies["db"]), read_only=True) as con:
        for hk, source, bk, name, hd in con.sql(
            "SELECT s.agency_hk,s.record_source,h.agency_bk,s.agency_name,s.hashdiff FROM raw_vault.sat_agency_details s JOIN raw_vault.hub_agency h USING(agency_hk)"
        ).fetchall():
            assert hk == digest([source, bk], "agency_hk")
            assert hd == digest([name], "agency_details")
        assert con.sql(
            "SELECT count(DISTINCT hashdiff) FROM raw_vault.sat_agency_details s JOIN raw_vault.hub_agency h USING(agency_hk) WHERE agency_bk LIKE 'TEST-PROBE-%'"
        ).fetchone() == (10,)
        assert con.execute(
            "SELECT agency_name,record_source,mode FROM raw_vault.sat_agency_details WHERE agency_hk=?",
            [digest(["NYC311:erm2-nwe9", "TEST-A"], "agency_hk")],
        ).fetchall() == [("Other source name", "NYC311:erm2-nwe9", "live")]


def test_intraday_asof_and_before_first_observation(agencies):
    history = records(agencies["db"], "TEST-INTRA")
    assert [r["snapshot_at_utc"].hour for r in history] == [9, 10]
    assert all(
        r["snapshot_at_utc"].tzinfo and r["load_dts_utc"].tzinfo for r in history
    )
    with duckdb.connect(str(agencies["db"]), read_only=True) as con:
        for hour, expected in [(8, None), (9, ("A",)), (10, ("B",)), (11, ("B",))]:
            assert (
                con.execute(
                    "SELECT agency_name FROM raw_vault.sat_agency_details WHERE agency_hk=? AND snapshot_at_utc<=? ORDER BY snapshot_at_utc DESC LIMIT 1",
                    [history[0]["agency_hk"], datetime(2026, 2, 6, hour, tzinfo=UTC)],
                ).fetchone()
                == expected
            )


@pytest.mark.parametrize(
    "case",
    [
        "same_batch_conflict",
        "cross_batch_conflict",
        "null_conflict",
        "blank_conflict",
        "rewrite",
        "missing_parent",
        "inconsistent_parent",
        "full_refresh",
        "schema_change",
    ],
)
def test_guards_leave_every_stored_row_unchanged(agencies, tmp_path, case):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(agencies["db"], db)
    if case == "same_batch_conflict":
        load(
            db,
            "conflicting-pair",
            7,
            [agency_row("R1", name="One"), agency_row("R2", name="Two")],
        )
        message = "Conflicting agency names"
    elif case in ["cross_batch_conflict", "null_conflict", "blank_conflict"]:
        name = {
            "cross_batch_conflict": "Different",
            "null_conflict": None,
            "blank_conflict": " \t",
        }[case]
        load(db, "conflicting-evidence", 2, [agency_row("R0", name=name)])
        message = "Conflicting agency names"
    elif case == "rewrite":
        load(db, "would-retract", 3, [agency_row("LATE", "TEST-LATE", "Final")])
        message = "Existing agency-details history would require rewriting"
    elif case == "missing_parent":
        load(db, "unbuilt", 7, [agency_row("R1", "UNBUILT-AGENCY", "New agency")])
        message = "Missing or inconsistent agency-details Hub parents"
    elif case == "inconsistent_parent":
        with duckdb.connect(str(db)) as con:
            con.execute(
                "UPDATE raw_vault.hub_agency SET mode='live' WHERE agency_hk=?",
                [digest(["synthetic_311", "TEST-A"], "agency_hk")],
            )
        message = "Missing or inconsistent agency-details Hub parents"
    elif case == "schema_change":
        with duckdb.connect(str(db)) as con:
            con.execute(
                "ALTER TABLE raw_vault.sat_agency_details ADD COLUMN unexpected_column VARCHAR"
            )
        message = "are out of sync"
    else:
        message = "is insert-only"
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        agencies["project"],
        db,
        tmp_path / "profile",
        selectors=(TABLE,),
        command="run",
        full_refresh=case == "full_refresh",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before


def test_failed_running_legacy_observations_are_excluded(agencies, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(agencies["db"], db)
    with duckdb.connect(str(db)) as con:
        for state in ["failed", "running"]:
            con.execute(
                f"INSERT INTO landing.ingestion_batch BY NAME SELECT * REPLACE ('legacy-{state}' AS batch_id,'{state}' AS status) FROM landing.ingestion_batch WHERE batch_id='z-initial'"
            )
            con.execute(
                f"INSERT INTO landing.request_snapshots SELECT * REPLACE ('legacy-{state}' AS batch_id) FROM landing.request_snapshots WHERE batch_id='z-initial'"
            )
    result = execute_dbt(
        agencies["project"],
        db,
        tmp_path / "profile",
        selectors=(
            "stg_311_request_snapshots",
            "int_request_assignment_observations",
            "int_agency_details_observations",
            "int_agency_details_changes",
        ),
        command="run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.sql(
            "SELECT count(*) FROM staging.int_agency_details_observations WHERE batch_id LIKE 'legacy-%'"
        ).fetchone() == (0,)


def test_post_insert_error_rolls_back_agency_details(agencies, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(agencies["db"], db)
    project = tmp_path / "project"
    shutil.copytree(
        agencies["project"], project, ignore=shutil.ignore_patterns("target", "logs")
    )
    model = project / "models/raw_vault/sat_agency_details.sql"
    model.write_text(
        model.read_text().replace(
            "full_refresh=false, on_schema_change='fail',",
            "full_refresh=false, on_schema_change='fail', post_hook=\"select error('injected agency-details load failure')\",",
        )
    )
    load(db, "new-before-error", 7, [agency_row("R1", name="Next agency name")])
    before = frozen(db, OLD + [TABLE])
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=(TABLE,), command="run"
    )
    assert result.returncode != 0
    assert "injected agency-details load failure" in result.stdout + result.stderr
    assert frozen(db, OLD + [TABLE]) == before


def test_clean_agency_build_needs_no_request_hub_or_problem_members(tmp_path):
    db = tmp_path / "warehouse.db"
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    load(
        db,
        "agency-only",
        1,
        [
            agency_row("Q2", "AGENCY-ONLY", "Fictional agency", complaint_type=None),
            agency_row("Q1", "AGENCY-ONLY", "Fictional agency", complaint_type=None),
        ],
    )
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=("+sat_agency_details",)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.sql(
            "SELECT agency_name,evidence_request_bk FROM raw_vault.sat_agency_details"
        ).fetchall() == [("Fictional agency", "Q1")]
        tables = {
            r[0]
            for r in con.sql(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='raw_vault'"
            ).fetchall()
        }
        assert tables == {"hub_agency", "sat_agency_details"}
