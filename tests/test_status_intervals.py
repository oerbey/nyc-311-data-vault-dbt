"""Observed interval endpoints/as-of semantics over immutable Raw Vault versions."""

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
MODEL = "bv_request_status_intervals"
RAW = [
    "hub_request",
    "hub_agency",
    "hub_problem",
    "lnk_request_agency",
    "lnk_request_problem",
    "sat_request_status",
    "sat_request_lifecycle",
    "sat_request_resolution",
    "sat_request_location",
    "sat_agency_details",
    "sat_lnk_request_agency_effectivity",
    "sat_lnk_request_problem_effectivity",
]


def status_row(key, status):
    return row(
        key, "INTERVAL-AGENCY", agency_name="Fictional interval agency", status=status
    )


def intervals(db, key):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cur = con.execute(
            "SELECT * FROM business_vault.bv_request_status_intervals WHERE request_hk=? ORDER BY observed_from",
            [digest(["synthetic_311", key], "request_hk")],
        )
        return [
            dict(zip([c[0] for c in cur.description], r, strict=True))
            for r in cur.fetchall()
        ]


def asof(db, key, instant):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(
            "SELECT status FROM business_vault.bv_request_status_intervals WHERE request_hk=? "
            "AND observed_from<=? AND (next_observed_from IS NULL OR ?<next_observed_from)",
            [digest(["synthetic_311", key], "request_hk"), instant, instant],
        ).fetchall()


@pytest.fixture(scope="module")
def temporal(tmp_path_factory):
    folder = tmp_path_factory.mktemp("status-intervals")
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
            status_row("MISSING", None),
            status_row("MIDDLE-LATE", "Open"),
            status_row("EXACT", " é;|:\n ") | {"created_date": "bad"},
        ],
    )
    load(db, "empty-status", 2, [status_row("MISSING", "")])
    load(db, "literal-status", 3, [status_row("MISSING", "NULL")])
    load(
        db,
        "fourth",
        4,
        [
            status_row("MISSING", None),
            status_row("MIDDLE-LATE", "Closed"),
            status_row("EARLIER-LATE", "Closed"),
        ],
    )
    load(db, "intraday-open", 6, [status_row("INTRADAY", "Open")], hour=9)
    load(db, "intraday-closed", 6, [status_row("INTRADAY", "Closed")], hour=10)
    for name, instant, status in [
        ("dst-before", datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "Open"),
        ("dst-after", datetime(2026, 3, 8, 7, 1, tzinfo=UTC), "Closed"),
    ]:
        load_batch(
            database_path=db,
            batch_id=name,
            records=[status_row("DST", status)],
            snapshot_at_utc=instant,
            source_id="synthetic_311",
            record_source="synthetic_311",
            mode="synthetic",
            requested_filter="fictional UTC capture boundaries",
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
            json=[
                row(
                    "DEMO-001",
                    "DEP",
                    agency_name="Live fictional agency",
                    status="Live reported state",
                )
            ]
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
            "+sat_agency_details",
            "+sat_lnk_request_agency_effectivity",
            "+sat_lnk_request_problem_effectivity",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    original = frozen(db, RAW)

    def build(selectors=(MODEL,)):
        result = execute_dbt(project, db, folder / "profile", selectors=selectors)
        assert result.returncode == 0, result.stdout + result.stderr

    build()
    initial = intervals(db, "DEMO-001")
    middle_before = intervals(db, "MIDDLE-LATE")
    early_before = intervals(db, "EARLIER-LATE")
    assert frozen(db, RAW) == original
    build()
    assert frozen(db, RAW) == original
    assert intervals(db, "DEMO-001") == initial
    # Landing observations, including unchanged/tied statuses, are not interval input.
    load(db, "a-identical-tie", 3, [synthetic_batches()[2]["records"][0]])
    load(db, "unchanged", 5, [synthetic_batches()[2]["records"][2]])
    assert intervals(db, "DEMO-001") == initial
    build(("+" + MODEL,))
    assert frozen(db, RAW) == original
    assert intervals(db, "DEMO-001") == initial
    load(db, "late-middle", 3, [status_row("MIDDLE-LATE", "In progress")])
    load(db, "late-earlier", 2, [status_row("EARLIER-LATE", "Open")])
    assert intervals(db, "MIDDLE-LATE") == middle_before
    assert intervals(db, "EARLIER-LATE") == early_before
    build(("+" + MODEL,))
    after_late = frozen(db, RAW)
    assert (
        len(after_late["sat_request_status"]) == len(original["sat_request_status"]) + 2
    )
    for table in RAW:
        assert all(r in after_late[table] for r in original[table])
        if table != "sat_request_status":
            assert after_late[table] == original[table]
    # Load another Raw Vault status without rebuilding the Business Vault view.
    load(
        db,
        "future-status",
        5,
        [synthetic_batches()[2]["records"][0] | {"status": "Closed"}],
    )
    build(("+sat_request_status",))
    dynamic = intervals(db, "DEMO-001")
    assert [r["status"] for r in dynamic] == ["Open", "Closed", "Open", "Closed"]
    final_raw = frozen(db, RAW)
    build()
    build()
    assert intervals(db, "DEMO-001") == dynamic
    assert frozen(db, RAW) == final_raw
    return {
        "db": db,
        "project": project,
        "original": original,
        "middle_before": middle_before,
        "early_before": early_before,
        "final_raw": final_raw,
    }


def test_reversals_and_half_open_endpoints(temporal):
    history = intervals(temporal["db"], "DEMO-001")
    assert [r["status"] for r in history] == ["Open", "Closed", "Open", "Closed"]
    assert [r["observed_from"].day for r in history] == [1, 2, 3, 5]
    assert [
        r["next_observed_from"].day if r["next_observed_from"] else None
        for r in history
    ] == [2, 3, 5, None]
    assert history[0]["hashdiff"] == history[2]["hashdiff"]
    assert history[2]["batch_id"] == "synthetic-20260203-v1"


def test_asof_boundaries_before_first_and_open_ended_latest(temporal):
    for instant, expected in [
        (datetime(2026, 1, 10, 9, tzinfo=UTC), []),
        (datetime(2026, 2, 1, 7, 59, 59, 999999, tzinfo=UTC), []),
        (datetime(2026, 2, 1, 8, tzinfo=UTC), [("Open",)]),
        (datetime(2026, 2, 2, 7, 59, 59, 999999, tzinfo=UTC), [("Open",)]),
        (datetime(2026, 2, 2, 8, tzinfo=UTC), [("Closed",)]),
        (datetime(2026, 2, 3, 8, tzinfo=UTC), [("Open",)]),
        (datetime(2026, 2, 5, 8, tzinfo=UTC), [("Closed",)]),
        (datetime(2099, 1, 1, tzinfo=UTC), [("Closed",)]),
    ]:
        assert asof(temporal["db"], "DEMO-001", instant) == expected


def test_observed_null_empty_and_literal_states_remain_distinct(temporal):
    history = intervals(temporal["db"], "MISSING")
    assert [r["status"] for r in history] == [None, "", "NULL", None]
    assert asof(temporal["db"], "MISSING", datetime(2026, 1, 31, tzinfo=UTC)) == []
    assert asof(temporal["db"], "MISSING", datetime(2026, 2, 1, 8, tzinfo=UTC)) == [
        (None,)
    ]
    assert intervals(temporal["db"], "EXACT")[0]["status"] == " é;|:\n "


def test_safe_late_versions_adjust_derived_ends_without_raw_rewrites(temporal):
    before = temporal["middle_before"]
    after = intervals(temporal["db"], "MIDDLE-LATE")
    assert before[0]["next_observed_from"].day == 4
    assert after[0]["next_observed_from"].day == 3
    assert after[0]["load_dts_utc"] == before[0]["load_dts_utc"]
    assert after[-1] == before[-1]
    assert [r["status"] for r in after] == ["Open", "In progress", "Closed"]
    early = intervals(temporal["db"], "EARLIER-LATE")
    assert early[0]["observed_from"].day == 2
    assert early[0]["load_dts_utc"] > early[1]["load_dts_utc"]
    assert early[1] == temporal["early_before"][0]
    assert (
        asof(temporal["db"], "EARLIER-LATE", datetime(2026, 2, 1, 8, tzinfo=UTC)) == []
    )


def test_all_endpoints_and_provenance_match_independent_python(temporal):
    with duckdb.connect(str(temporal["db"]), read_only=True) as con:
        source = con.sql(
            "SELECT request_hk,snapshot_at_utc,status,hashdiff,batch_id,record_source,mode,load_dts_utc FROM raw_vault.sat_request_status ORDER BY request_hk,snapshot_at_utc"
        ).fetchall()
        expected = []
        for i, r in enumerate(source):
            end = (
                source[i + 1][1]
                if i + 1 < len(source) and source[i + 1][0] == r[0]
                else None
            )
            expected.append((r[0], r[1], end, *r[2:]))
        actual = con.sql(
            "SELECT request_hk,observed_from,next_observed_from,status,hashdiff,batch_id,record_source,mode,load_dts_utc FROM business_vault.bv_request_status_intervals ORDER BY request_hk,observed_from"
        ).fetchall()
        assert actual == expected
        assert con.sql(
            "SELECT table_type FROM information_schema.tables WHERE table_schema='business_vault' AND table_name='bv_request_status_intervals'"
        ).fetchone() == ("VIEW",)
        types = dict(
            con.sql(
                "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='business_vault' AND table_name='bv_request_status_intervals'"
            ).fetchall()
        )
        assert all(
            types[c] == "TIMESTAMP WITH TIME ZONE"
            for c in ["observed_from", "next_observed_from", "load_dts_utc"]
        )
    assert frozen(temporal["db"], RAW) == temporal["final_raw"]


def test_intraday_dst_and_session_timezone_independence(temporal):
    history = intervals(temporal["db"], "INTRADAY")
    assert [r["observed_from"].hour for r in history] == [9, 10]
    assert asof(temporal["db"], "INTRADAY", datetime(2026, 2, 6, 10, tzinfo=UTC)) == [
        ("Closed",)
    ]
    dst = intervals(temporal["db"], "DST")
    assert (
        dst[0]["next_observed_from"] - dst[0]["observed_from"]
    ).total_seconds() == 120
    with duckdb.connect(str(temporal["db"]), read_only=True) as con:
        results = []
        for zone in ["UTC", "Europe/Riga", "America/New_York"]:
            con.execute(f"SET TimeZone='{zone}'")
            results.append(
                con.sql(
                    "SELECT * FROM business_vault.bv_request_status_intervals ORDER BY request_hk,observed_from"
                ).fetchall()
            )
        assert results[0] == results[1] == results[2]


def test_source_namespaces_do_not_share_timelines(temporal):
    with duckdb.connect(str(temporal["db"]), read_only=True) as con:
        assert con.execute(
            "SELECT status,record_source,mode,next_observed_from FROM business_vault.bv_request_status_intervals WHERE request_hk=?",
            [digest(["NYC311:erm2-nwe9", "DEMO-001"], "request_hk")],
        ).fetchall() == [("Live reported state", "NYC311:erm2-nwe9", "live", None)]
    assert len(intervals(temporal["db"], "DEMO-001")) == 4


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "null_capture",
        "null_metadata",
        "hash",
        "missing_hub",
        "parent_mode",
        "hub_hash",
    ],
)
def test_bad_raw_inputs_fail_without_mutation(temporal, tmp_path, case):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(temporal["db"], db)
    hk = digest(["synthetic_311", "EXACT"], "request_hk")
    with duckdb.connect(str(db)) as con:
        if case == "duplicate":
            con.execute(
                "INSERT INTO raw_vault.sat_request_status SELECT * FROM raw_vault.sat_request_status WHERE request_hk=?",
                [hk],
            )
            message = "unique Raw Vault request/capture versions"
        elif case in ["null_capture", "null_metadata"]:
            column = "snapshot_at_utc" if case == "null_capture" else "batch_id"
            con.execute(
                f"UPDATE raw_vault.sat_request_status SET {column}=NULL WHERE request_hk=?",
                [hk],
            )
            message = "non-null Raw Vault keys"
        elif case == "hash":
            con.execute(
                "UPDATE raw_vault.sat_request_status SET hashdiff='bad-hash' WHERE request_hk=?",
                [hk],
            )
            message = "canonical Raw Vault status hashes"
        elif case == "missing_hub":
            con.execute("DELETE FROM raw_vault.hub_request WHERE request_hk=?", [hk])
            message = "consistent Raw Vault request Hub parents"
        elif case == "parent_mode":
            con.execute(
                "UPDATE raw_vault.hub_request SET mode='live' WHERE request_hk=?", [hk]
            )
            message = "consistent Raw Vault request Hub parents"
        else:
            con.execute(
                "UPDATE raw_vault.hub_request SET request_bk='changed-key' WHERE request_hk=?",
                [hk],
            )
            message = "consistent Raw Vault request Hub parents"
    before = frozen(db, RAW)
    result = execute_dbt(
        temporal["project"], db, tmp_path / "profile", selectors=(MODEL,), command="run"
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, RAW) == before


@pytest.mark.parametrize("case", ["conflicting_capture", "history_retraction"])
def test_upstream_guards_keep_intervals_and_raw_history_unchanged(
    temporal, tmp_path, case
):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(temporal["db"], db)
    before = frozen(db, RAW)
    before_intervals = intervals(db, "DEMO-001")
    if case == "conflicting_capture":
        load(
            db,
            "conflict",
            2,
            [synthetic_batches()[1]["records"][0] | {"status": "Open"}],
        )
        message = "Conflicting request statuses"
    else:
        load(db, "retraction", 2, [status_row("MIDDLE-LATE", "In progress")])
        message = "Existing status history would require rewriting"
    result = execute_dbt(
        temporal["project"],
        db,
        tmp_path / "profile",
        selectors=("+" + MODEL,),
        command="run",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen(db, RAW) == before
    assert intervals(db, "DEMO-001") == before_intervals


def test_empty_fresh_build_has_no_invented_intervals(tmp_path):
    db = tmp_path / "warehouse.db"
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    load(db, "empty", 1, [])
    result = execute_dbt(project, db, tmp_path / "profile", selectors=("+" + MODEL,))
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.sql(
            "SELECT count(*) FROM business_vault.bv_request_status_intervals"
        ).fetchone() == (0,)
        raw_tables = {
            r[0]
            for r in con.sql(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='raw_vault'"
            ).fetchall()
        }
        assert raw_tables == {"hub_request", "sat_request_status"}
