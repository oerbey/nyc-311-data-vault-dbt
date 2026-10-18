"""Offline integration coverage for source-scoped assignment identities and Links."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
from test_raw_vault import digest, execute_dbt

from ingestion.fixtures import synthetic_batches
from ingestion.landing import SOURCE_FIELDS, load_batch
from ingestion.socrata import DATE_FIELDS, load_live

ROOT = Path(__file__).resolve().parents[1]
NEW_TABLES = ["hub_agency", "hub_problem", "lnk_request_agency", "lnk_request_problem"]
OLD_TABLES = ["hub_request", "sat_request_status"]
SELECTORS = ("+lnk_request_agency", "+lnk_request_problem")


def load(db, batch_id, day, records, hour=8):
    load_batch(
        database_path=db,
        batch_id=batch_id,
        records=records,
        snapshot_at_utc=datetime(2026, 2, day, hour, tzinfo=UTC),
        source_id="synthetic_311",
        record_source="synthetic_311",
        mode="synthetic",
        requested_filter="fictional assignment tests",
    )


def frozen(db, tables=NEW_TABLES):
    with duckdb.connect(str(db), read_only=True) as con:
        return {
            t: con.execute(f"SELECT * FROM raw_vault.{t} ORDER BY ALL").fetchall()
            for t in tables
        }


def row(key, agency="DEP", problem="Synthetic problem", **changes):
    return {
        "unique_key": key,
        "agency": agency,
        "complaint_type": problem,
        "status": "Open",
        **changes,
    }


@pytest.fixture(scope="module")
def assignments(tmp_path_factory):
    folder = tmp_path_factory.mktemp("assignments")
    db = folder / "warehouse.db"
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", "profiles.yml", ".user.yml"),
    )
    for batch in synthetic_batches():
        load_batch(database_path=db, **batch)
    load(
        db,
        "eligibility",
        4,
        [
            row("MISSING-AGENCY", None, "Present problem"),
            row("BLANK-AGENCY", " \t", "Present"),
            row("MISSING-PROBLEM", "VALID-ONLY", None),
            row("BLANK-PROBLEM", "VALID-ONLY", "\t "),
            row("EXACT", " DEP ", " Café;|: "),
            row("UPPER", "DEP", "Same"),
            row("LOWER", "dep", "Same"),
            row("DELIM-1", "A|B", "C"),
            row("DELIM-2", "A", "B|C"),
            row("ZERO-AGENCY", "001", "Case"),
            row("NUM-AGENCY", "1", "Case"),
            row("LATE", "LATE-NEW", "Late problem"),
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
            json=[row("DEMO-001", "DEP", "Synthetic water issue")]
            if request.url.params["$offset"] == "0"
            else [],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        load_live(database_path=db, batch_id="live-fictional", client=client)
    # Establish this request before its known assignment, so late relationship
    # evidence does not require retracting its existing status boundary.
    load(db, "late-request-first", 1, [row("LATE", None, None)])
    # Build the existing request/status slice independently before introducing assignments.
    result = execute_dbt(project, db, folder / "profile")
    assert result.returncode == 0, result.stdout + result.stderr
    old_before = frozen(db, OLD_TABLES)

    def build():
        result = execute_dbt(project, db, folder / "profile", selectors=SELECTORS)
        assert result.returncode == 0, result.stdout + result.stderr

    build()
    initial = frozen(db)
    assert frozen(db, OLD_TABLES) == old_before
    build()
    replay = frozen(db)
    # Existing relationship return, description/name changes, and an identical tie.
    original = synthetic_batches()[0]["records"][0]
    load(db, "return-agency", 5, [row("DEMO-002", "DEP", "Synthetic cleanup")])
    load(
        db,
        "description-only",
        5,
        [
            original
            | {"agency_name": "Renamed synthetic agency", "descriptor": "New detail"}
        ],
    )
    load(db, "a-identical-tie", 1, [original])
    load(db, "incomplete-agency-tie", 1, [original | {"agency": None}])
    load(db, "incomplete-problem-tie", 1, [original | {"complaint_type": None}])
    build()
    no_new_identity = frozen(db)
    assert frozen(db, OLD_TABLES) == old_before
    # Earlier assignment evidence is additive even though global watermark is later.
    load(db, "late-assignment", 2, [row("LATE", "LATE-OLD", "Earlier problem")])
    build()
    after_late = frozen(db)
    assert frozen(db, OLD_TABLES) == old_before
    return {
        "db": db,
        "project": project,
        "initial": initial,
        "replay": replay,
        "no_new_identity": no_new_identity,
        "after_late": after_late,
        "old_before": old_before,
    }


def test_replay_returns_and_descriptive_changes_are_noops(assignments):
    assert (
        assignments["initial"]
        == assignments["replay"]
        == assignments["no_new_identity"]
    )
    for table, records in assignments["initial"].items():
        assert all(record in assignments["after_late"][table] for record in records)


def test_reassignment_keeps_both_parent_pairs(assignments):
    hk = digest(["synthetic_311", "DEMO-002"], "request_hk")
    with duckdb.connect(str(assignments["db"]), read_only=True) as con:
        agencies = con.execute(
            "SELECT h.agency_bk FROM raw_vault.lnk_request_agency l JOIN raw_vault.hub_agency h USING(agency_hk) WHERE request_hk=? ORDER BY h.agency_bk",
            [hk],
        ).fetchall()
        problems = con.execute(
            "SELECT h.agency_bk,h.complaint_type FROM raw_vault.lnk_request_problem l JOIN raw_vault.hub_problem h USING(problem_hk) WHERE request_hk=? ORDER BY h.agency_bk",
            [hk],
        ).fetchall()
    assert agencies == [("DEP",), ("DSNY",)]
    assert problems == [("DEP", "Synthetic cleanup"), ("DSNY", "Synthetic cleanup")]


def test_missing_keys_flag_observations_without_unknown_members(assignments):
    with duckdb.connect(str(assignments["db"]), read_only=True) as con:
        records = con.sql(
            "SELECT request_bk,has_missing_agency,has_missing_problem,is_agency_link_eligible,is_problem_link_eligible,agency_hk,problem_hk FROM staging.int_request_assignment_observations WHERE batch_id='eligibility'"
        ).fetchall()
        by_key = {r[0]: r[1:] for r in records}
        for key in ["MISSING-AGENCY", "BLANK-AGENCY"]:
            assert by_key[key] == (True, False, False, False, None, None)
        for key in ["MISSING-PROBLEM", "BLANK-PROBLEM"]:
            values = by_key[key]
            assert values[:4] == (False, True, True, False)
            assert values[4] is not None and values[5] is None
        assert con.sql(
            "SELECT count(*) FROM raw_vault.hub_request WHERE request_bk IN ('MISSING-AGENCY','BLANK-AGENCY','MISSING-PROBLEM','BLANK-PROBLEM')"
        ).fetchone() == (4,)
        assert con.sql(
            "SELECT count(*) FROM raw_vault.hub_agency WHERE agency_bk IS NULL OR trim(agency_bk)=''"
        ).fetchone() == (0,)
        assert con.sql(
            "SELECT count(*) FROM raw_vault.hub_problem WHERE agency_bk='VALID-ONLY'"
        ).fetchone() == (0,)
        expected = con.sql(
            "SELECT count(*) FROM staging.stg_311_request_snapshots"
        ).fetchone()[0]
        assert con.sql(
            "SELECT count(*) FROM staging.int_request_assignment_observations"
        ).fetchone() == (expected,)


def test_hashes_and_problem_bk_match_independent_utf8_contract(assignments):
    with duckdb.connect(str(assignments["db"]), read_only=True) as con:
        agencies = con.sql(
            "SELECT agency_hk,record_source,agency_bk FROM raw_vault.hub_agency"
        ).fetchall()
        problems = con.sql(
            "SELECT problem_hk,problem_bk,record_source,agency_bk,complaint_type FROM raw_vault.hub_problem"
        ).fetchall()
        for hk, source, agency in agencies:
            assert hk == digest([source, agency], "agency_hk")
        for hk, bk, source, agency, problem in problems:
            assert hk == digest([source, agency, problem], "problem_hk")
            assert bk == "dv2:v1:problem_bk:2:" + "".join(
                f"S{len(v.encode('utf-8'))}:{v};" for v in [agency, problem]
            )
        for kind in ["agency", "problem"]:
            for hk, request, parent in con.execute(
                f"SELECT link_hk,request_hk,{kind}_hk FROM raw_vault.lnk_request_{kind}"
            ).fetchall():
                assert hk == digest([request, parent], f"lnk_request_{kind}")
        assert con.sql(
            "SELECT agency_bk,complaint_type FROM raw_vault.hub_problem WHERE agency_bk=' DEP '"
        ).fetchone() == (" DEP ", " Café;|: ")
        assert con.sql(
            "SELECT count(*) FROM raw_vault.hub_agency WHERE agency_bk IN ('001','1')"
        ).fetchone() == (2,)
        assert con.sql(
            "SELECT count(*) FROM raw_vault.hub_agency WHERE agency_bk IN ('DEP','dep') AND record_source='synthetic_311'"
        ).fetchone() == (2,)


def test_source_namespace_isolates_same_agency_and_problem(assignments):
    with duckdb.connect(str(assignments["db"]), read_only=True) as con:
        assert con.sql(
            "SELECT count(DISTINCT agency_hk) FROM raw_vault.hub_agency WHERE agency_bk='DEP'"
        ).fetchone() == (2,)
        assert con.sql(
            "SELECT count(DISTINCT problem_hk) FROM raw_vault.hub_problem WHERE agency_bk='DEP' AND complaint_type='Synthetic water issue'"
        ).fetchone() == (2,)
        assert con.sql(
            "SELECT count(DISTINCT problem_hk) FROM raw_vault.hub_problem WHERE complaint_type='Same'"
        ).fetchone() == (2,)


def test_earlier_relationship_adds_without_changing_load_times(assignments):
    assert (
        len(assignments["after_late"]["hub_agency"])
        == len(assignments["initial"]["hub_agency"]) + 1
    )
    assert (
        len(assignments["after_late"]["hub_problem"])
        == len(assignments["initial"]["hub_problem"]) + 1
    )
    for kind in ["agency", "problem"]:
        assert (
            len(assignments["after_late"][f"lnk_request_{kind}"])
            == len(assignments["initial"][f"lnk_request_{kind}"]) + 1
        )


@pytest.mark.parametrize(
    "problem",
    [
        "agency_conflict",
        "problem_conflict",
        "missing_request",
        "missing_agency",
        "missing_problem",
        "full_refresh",
    ],
)
def test_guards_leave_existing_assignment_rows_unchanged(
    assignments, tmp_path, problem
):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(assignments["db"], db)
    before = frozen(db)
    old = frozen(db, OLD_TABLES)
    selectors = ("+lnk_request_agency", "+lnk_request_problem")
    msg = ""
    if problem == "agency_conflict":
        load(
            db,
            "conflicting-agency",
            1,
            [row("DEMO-001", "OTHER", "Synthetic water issue")],
        )
        msg = "Conflicting complete assignments"
    elif problem == "problem_conflict":
        load(
            db, "conflicting-problem", 1, [row("DEMO-001", "DEP", "Different problem")]
        )
        msg = "Conflicting complete assignments"
    elif problem.startswith("missing_"):
        kind = "problem" if problem == "missing_problem" else "agency"
        records = [row("UNLOADED-REQUEST", "UNLOADED-AGENCY", "UNLOADED-PROBLEM")]
        if problem == "missing_agency":
            records = [row("DEMO-001", "UNLOADED-AGENCY", "UNLOADED-PROBLEM")]
        elif problem == "missing_problem":
            records = [row("DEMO-001", "DEP", "UNLOADED-PROBLEM")]
        load(db, "unloaded-parent", 6, records)
        selectors = (f"lnk_request_{kind}",)
        msg = "Missing or inconsistent assignment Hub parents"
    else:
        selectors = (
            "hub_agency",
            "hub_problem",
            "lnk_request_agency",
            "lnk_request_problem",
        )
        msg = "is insert-only"
    result = execute_dbt(
        assignments["project"],
        db,
        tmp_path / "profile",
        selectors=selectors,
        command="run",
        full_refresh=problem == "full_refresh",
    )
    assert result.returncode != 0
    assert msg in result.stdout + result.stderr
    assert frozen(db) == before
    assert frozen(db, OLD_TABLES) == old
