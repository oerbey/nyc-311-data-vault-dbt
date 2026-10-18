"""Network-free observed Link effectivity, including replay and late evidence."""

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
TABLES = [f"sat_lnk_request_{k}_effectivity" for k in ["agency", "problem"]]
OLD = [
    "hub_request",
    "sat_request_status",
    "hub_agency",
    "hub_problem",
    "lnk_request_agency",
    "lnk_request_problem",
]
SELECTORS = tuple("+" + t for t in TABLES)


def events(db, kind, key):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        return con.execute(
            f"SELECT h.agency_bk,e.active_flag,e.observed_from,e.hashdiff,e.batch_id "
            f"FROM raw_vault.sat_lnk_request_{kind}_effectivity e "
            f"JOIN raw_vault.lnk_request_{kind} l USING(link_hk) "
            f"JOIN raw_vault.hub_{kind} h USING({kind}_hk) "
            "WHERE e.request_hk=? ORDER BY observed_from,active_flag",
            [digest(["synthetic_311", key], "request_hk")],
        ).fetchall()


@pytest.fixture(scope="module")
def effectivity(tmp_path_factory):
    folder = tmp_path_factory.mktemp("effectivity")
    db = folder / "warehouse.db"
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    for batch in synthetic_batches():
        load_batch(database_path=db, **batch)

    # Same fictional key under a second source must have an independent timeline.
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
            json=[row("DEMO-002", " DEP;|é ", "Category")]
            if request.url.params["$offset"] == "0"
            else [],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        load_live(database_path=db, batch_id="namespace-fixture", client=client)
    # Establish status identity early without an assignment, avoiding any status rewrite.
    load(db, "early-request", 1, [row("LATE", None, None)])
    load(db, "known-later", 4, [row("LATE", "LATE-B", "Category")])
    result = execute_dbt(
        project,
        db,
        folder / "profile",
        selectors=(
            "+sat_request_status",
            "+lnk_request_agency",
            "+lnk_request_problem",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    old = frozen(db, OLD)

    def build():
        result = execute_dbt(project, db, folder / "profile", selectors=SELECTORS)
        assert result.returncode == 0, result.stdout + result.stderr

    build()
    initial = frozen(db, TABLES)
    assert frozen(db, OLD) == old
    build()
    assert frozen(db, TABLES) == initial
    # Incomplete ties, disappearance, empty captures and failed batches cannot end Links.
    load(db, "missing-fields", 4, [row("DEMO-002", None, None)])
    load(db, "empty-capture", 4, [])
    with pytest.raises(ValueError):
        load(
            db,
            "failed-replacement",
            4,
            [row("DEMO-002", "OTHER"), row("DEMO-002", "OTHER")],
        )
    load(db, "a-identical-tie", 2, [row("DEMO-002", "DSNY", "Synthetic cleanup")])
    load(db, "incomplete-tie", 2, [row("DEMO-002", None, None)])
    build()
    assert frozen(db, TABLES) == initial
    # Return to prior Link inserts another activation, never a duplicate identity.
    load(db, "return", 5, [row("DEMO-002", "DEP", "Synthetic cleanup")])
    # Agency eligible while problem missing: problem timeline stays last-known.
    load(db, "agency-only", 6, [row("DEMO-002", "DSNY", None)])
    load(db, "problem-known", 7, [row("DEMO-002", "DSNY", "Synthetic cleanup")])
    load(db, "category-only", 8, [row("DEMO-002", "DSNY", "New category")])
    load(db, "intraday-first", 8, [row("INTRADAY", " A;|é ", "Category")], hour=9)
    load(db, "intraday-second", 8, [row("INTRADAY", "a;|é", "Category")], hour=10)
    build()
    before_late = frozen(db, TABLES)
    # Additive late evidence: old later activation stays, new earlier Link gains
    # activation and deactivation at the old capture, without retracting rows.
    load(db, "late-earlier", 2, [row("LATE", "LATE-A", "Category")])
    build()
    after_late = frozen(db, TABLES)
    for table in TABLES:
        assert all(r in after_late[table] for r in before_late[table])
        assert len(after_late[table]) == len(before_late[table]) + 2
    build()
    assert frozen(db, TABLES) == after_late
    return {"db": db, "project": project, "initial": initial}


@pytest.mark.parametrize("kind", ["agency", "problem"])
def test_reassignment_return_and_partial_evidence(effectivity, kind):
    actual = [r for r in events(effectivity["db"], kind, "DEMO-002") if r[2].day <= 7]
    assert [(a, active, at.day) for a, active, at, *_ in actual] == [
        ("DEP", True, 1),
        ("DEP", False, 2),
        ("DSNY", True, 2),
        ("DSNY", False, 5),
        ("DEP", True, 5),
        ("DEP", False, 6 if kind == "agency" else 7),
        ("DSNY", True, 6 if kind == "agency" else 7),
    ]
    for _, active, at, hashdiff, batch in actual:
        assert at.tzinfo is not None
        assert hashdiff == digest(
            ["true" if active else "false"], f"request_{kind}_effectivity"
        )
        if at.day == 2:
            # Lexically earlier identical evidence must not reset stored provenance.
            assert batch == "synthetic-20260202-v1"
    assert actual[0][3] == actual[4][3]


@pytest.mark.parametrize("kind", ["agency", "problem"])
def test_unchanged_and_unknown_before_first_capture(effectivity, kind):
    actual = events(effectivity["db"], kind, "DEMO-003")
    assert len(actual) == 1 and actual[0][1] is True
    assert actual[0][2] == datetime(2026, 2, 1, 8, tzinfo=UTC)
    with duckdb.connect(str(effectivity["db"]), read_only=True) as con:
        assert con.execute(
            f"SELECT count(*) FROM raw_vault.sat_lnk_request_{kind}_effectivity "
            "WHERE observed_from < '2026-02-01'",
        ).fetchone() == (0,)


@pytest.mark.parametrize("kind", ["agency", "problem"])
def test_safe_late_replacement_has_observed_deactivation(effectivity, kind):
    assert [
        (a, active, at.day)
        for a, active, at, *_ in events(effectivity["db"], kind, "LATE")
    ] == [("LATE-A", True, 2), ("LATE-A", False, 4), ("LATE-B", True, 4)]


@pytest.mark.parametrize("case", ["tie", "rewrite", "missing_parent", "full_refresh"])
def test_guards_preserve_both_effectivity_tables(effectivity, tmp_path, case):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(effectivity["db"], db)
    before = frozen(db, TABLES)
    selectors = tuple(TABLES)
    if case == "tie":
        load(db, "conflicting", 2, [row("DEMO-002", "OTHER", "New")])
        message = "Conflicting complete assignments"
    elif case == "rewrite":
        # Moving an existing activation earlier would retract its stored boundary.
        load(db, "would-retract", 3, [row("LATE", "LATE-B", "Category")])
        message = "Existing assignment effectivity would require rewriting"
    elif case == "missing_parent":
        load(db, "unbuilt-link", 8, [row("DEMO-001", "UNBUILT", "New")])
        message = "Missing or inconsistent effectivity Link parents"
    else:
        message = "is insert-only"
    result = execute_dbt(
        effectivity["project"],
        db,
        tmp_path / "profile",
        selectors=selectors,
        command="run",
        full_refresh=case == "full_refresh",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, TABLES) == before


@pytest.mark.parametrize("kind", ["agency", "problem"])
def test_asof_reconstructs_one_observed_assignment(effectivity, kind):
    with duckdb.connect(str(effectivity["db"]), read_only=True) as con:
        for day, expected in [
            (1, "DEP"),
            (2, "DSNY"),
            (4, "DSNY"),
            (5, "DEP"),
            (6, "DSNY" if kind == "agency" else "DEP"),
            (7, "DSNY"),
        ]:
            actual = con.execute(
                f"WITH latest AS (SELECT * FROM raw_vault.sat_lnk_request_{kind}_effectivity "
                "WHERE observed_from<=? QUALIFY row_number() OVER (PARTITION BY link_hk ORDER BY observed_from DESC)=1) "
                f"SELECT h.agency_bk FROM latest e JOIN raw_vault.lnk_request_{kind} l USING(link_hk) "
                f"JOIN raw_vault.hub_{kind} h USING({kind}_hk) "
                "WHERE e.request_hk=? AND active_flag",
                [
                    datetime(2026, 2, day, 8, tzinfo=UTC),
                    digest(["synthetic_311", "DEMO-002"], "request_hk"),
                ],
            ).fetchall()
            assert actual == [(expected,)]
        second = con.execute(
            f"SELECT record_source,mode,count(*) FROM raw_vault.sat_lnk_request_{kind}_effectivity "
            "WHERE request_hk=? GROUP BY ALL",
            [digest(["NYC311:erm2-nwe9", "DEMO-002"], "request_hk")],
        ).fetchall()
        assert second == [("NYC311:erm2-nwe9", "live", 1)]


@pytest.mark.parametrize("kind", ["agency", "problem"])
def test_intraday_boundaries_preserve_exact_keys(effectivity, kind):
    actual = events(effectivity["db"], kind, "INTRADAY")
    assert [(a, active, at.hour) for a, active, at, *_ in actual] == [
        (" A;|é ", True, 9),
        (" A;|é ", False, 10),
        ("a;|é", True, 10),
    ]


def test_problem_change_does_not_change_agency_effectivity(effectivity):
    agency = events(effectivity["db"], "agency", "DEMO-002")
    problem = events(effectivity["db"], "problem", "DEMO-002")
    assert not [r for r in agency if r[2].day == 8]
    assert [(r[0], r[1]) for r in problem if r[2].day == 8] == [
        ("DSNY", False),
        ("DSNY", True),
    ]
