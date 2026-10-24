"""Bounded, observed-time PIT integration checks, entirely offline."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
import yaml
from test_assignments import frozen, load
from test_raw_vault import digest, execute_dbt

from ingestion.fixtures import synthetic_batches
from ingestion.landing import load_batch

ROOT = Path(__file__).resolve().parents[1]
MODEL = "pit_request_status_asof"
RAW = ["hub_request", "sat_request_status"]
INSTANTS = [
    "2026-01-31T00:00:00Z",
    "2026-02-01T08:00:00Z",
    "2026-02-02T07:59:59.999999Z",
    "2026-02-02T08:00:00Z",
    "2026-02-03T08:00:00Z",
    "2026-02-04T08:00:00Z",
    "2026-03-08T06:59:00Z",
    "2026-03-08T07:00:00Z",
    "2026-03-08T07:01:00Z",
]


def configure(project, value):
    path = project / "dbt_project.yml"
    config = yaml.safe_load(path.read_text())
    config.setdefault("vars", {})["pit_status_as_of_instants"] = value
    path.write_text(yaml.safe_dump(config))


def rows(db):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.sql(
            "SELECT * FROM business_vault.pit_request_status_asof ORDER BY request_hk,as_of_utc"
        ).fetchall()


def assert_independent(db, instants):
    with duckdb.connect(str(db), read_only=True) as con:
        hubs = con.sql(
            "SELECT request_hk,record_source,mode FROM raw_vault.hub_request ORDER BY request_hk"
        ).fetchall()
        versions = con.sql(
            "SELECT request_hk,snapshot_at_utc,load_dts_utc,hashdiff,batch_id,status FROM raw_vault.sat_request_status ORDER BY snapshot_at_utc"
        ).fetchall()
        expected = []
        for hk, source, mode in hubs:
            for instant in sorted(set(instants)):
                eligible = [v for v in versions if v[0] == hk and v[1] <= instant]
                v = eligible[-1] if eligible else None
                expected.append(
                    (
                        hk,
                        instant,
                        source,
                        mode,
                        v is not None,
                        *(v[1:] if v else [None] * 5),
                    )
                )
        assert rows(db) == expected


@pytest.fixture(scope="module")
def pit(tmp_path_factory):
    folder = tmp_path_factory.mktemp("status-pit")
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
        "missing-first",
        1,
        [
            {"unique_key": "MISSING", "status": None},
            {"unique_key": "LATE", "status": "Open"},
        ],
    )
    load(db, "missing-empty", 2, [{"unique_key": "MISSING", "status": ""}])
    load(db, "missing-unicode", 3, [{"unique_key": "MISSING", "status": " é;|:\n "}])
    load(db, "late-last", 4, [{"unique_key": "LATE", "status": "Closed"}])
    for batch, instant, status in [
        ("dst-before", datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "Open"),
        ("dst-after", datetime(2026, 3, 8, 7, 1, tzinfo=UTC), "Closed"),
    ]:
        load_batch(
            database_path=db,
            batch_id=batch,
            records=[{"unique_key": "DST", "status": status}],
            snapshot_at_utc=instant,
            source_id="synthetic_311",
            record_source="synthetic_311",
            mode="synthetic",
            requested_filter="fictional DST",
        )
    load_batch(
        database_path=db,
        batch_id="live-namespace",
        records=[{"unique_key": "DEMO-001", "status": "Live state"}],
        snapshot_at_utc=datetime(2026, 2, 2, 8, tzinfo=UTC),
        source_id="erm2-nwe9",
        record_source="NYC311:erm2-nwe9",
        mode="live",
        requested_filter="fictional namespace",
    )

    # Supply reconciled counters for this deliberately offline live-namespace fixture.
    with duckdb.connect(str(db)) as con:
        con.execute(
            "UPDATE landing.ingestion_batch SET expected_rows_before=1, expected_rows_after=1, page_count=1 WHERE batch_id='live-namespace'"
        )

    def build(selectors=(MODEL,)):
        result = execute_dbt(project, db, folder / "profile", selectors=selectors)
        assert result.returncode == 0, result.stdout + result.stderr

    build(("+bv_request_status_intervals",))
    original = frozen(db, RAW)
    build()
    initial = rows(db)
    build()
    assert rows(db) == initial
    assert frozen(db, RAW) == original
    load(db, "late-middle", 3, [{"unique_key": "LATE", "status": "In progress"}])
    assert rows(db) == initial  # Landing-only evidence cannot change a PIT.
    build(("+sat_request_status",))
    after = rows(db)  # Views reflect accepted Raw versions without a PIT rebuild.
    assert after != initial
    assert all(
        v in frozen(db, RAW)["sat_request_status"]
        for v in original["sat_request_status"]
    )
    assert frozen(db, RAW)["hub_request"] == original["hub_request"]
    final_raw = frozen(db, RAW)
    build()
    assert rows(db) == after
    assert frozen(db, RAW) == final_raw
    return {
        "db": db,
        "project": project,
        "original": original,
        "initial": initial,
        "final_raw": final_raw,
    }


def test_grid_and_provenance_independently_reconstructed(pit):
    assert_independent(pit["db"], [datetime.fromisoformat(t) for t in INSTANTS])
    assert frozen(pit["db"], RAW) == pit["final_raw"]


def test_reversal_boundary_unknown_and_observed_null(pit):
    result = rows(pit["db"])

    def states(key, source="synthetic_311"):
        hk = digest([source, key], "request_hk")
        return [(r[4], r[-1]) for r in result if r[0] == hk]

    assert states("DEMO-001") == [
        (False, None),
        (True, "Open"),
        (True, "Open"),
        (True, "Closed"),
        *[(True, "Open")] * 5,
    ]
    assert states("MISSING")[:5] == [
        (False, None),
        (True, None),
        (True, None),
        (True, ""),
        (True, " é;|:\n "),
    ]
    assert (
        states("DEMO-001", "NYC311:erm2-nwe9")[:5]
        == [(False, None)] * 3 + [(True, "Live state")] * 2
    )
    assert states("DST")[-3:] == [(True, "Open"), (True, "Open"), (True, "Closed")]


def test_types_session_timezone_and_view_materialization(pit):
    with duckdb.connect(str(pit["db"]), read_only=True) as con:
        original = con.sql(
            "SELECT * FROM business_vault.pit_request_status_asof ORDER BY ALL"
        ).fetchall()
        con.execute("SET TimeZone='America/New_York'")
        assert (
            con.sql(
                "SELECT * FROM business_vault.pit_request_status_asof ORDER BY ALL"
            ).fetchall()
            == original
        )
        types = dict(
            con.sql(
                "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='business_vault' AND table_name='pit_request_status_asof'"
            ).fetchall()
        )
        for c in ["as_of_utc", "status_snapshot_at_utc", "status_load_dts_utc"]:
            assert types[c] == "TIMESTAMP WITH TIME ZONE"
        assert con.sql(
            "SELECT table_type FROM information_schema.tables WHERE table_schema='business_vault' AND table_name='pit_request_status_asof'"
        ).fetchone() == ("VIEW",)


def clone(pit, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(pit["db"], db)
    project = tmp_path / "project"
    shutil.copytree(pit["project"], project)
    return db, project


@pytest.mark.parametrize(
    "calendar",
    [
        None,
        [],
        ["2026-02-02T08:00:00Z", "2026-02-02T03:00:00-05:00"],
        ["2099-01-01T00:00:00Z"],
    ],
)
def test_default_empty_deduplicated_and_future_calendars(pit, tmp_path, calendar):
    db, project = clone(pit, tmp_path)
    configure(project, calendar)
    before = frozen(db, RAW)
    result = execute_dbt(project, db, tmp_path / "profile", selectors=(MODEL,))
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        instants = (
            [
                r[0]
                for r in con.sql(
                    "SELECT DISTINCT snapshot_at_utc FROM raw_vault.sat_request_status"
                ).fetchall()
            ]
            if calendar is None
            else [datetime.fromisoformat(t) for t in calendar]
        )
    assert_independent(db, instants)
    assert frozen(db, RAW) == before


@pytest.mark.parametrize(
    "calendar",
    [
        "2026-02-01",
        {"bad": "value"},
        [None],
        ["2026-02-01"],
        ["2026-02-01T08:00:00"],
        ["2026-02-01T08:00:00Z'; DROP TABLE anything"],
        ["2026-02-30T08:00:00Z"],
        ["2026-02-01T08:00:00Z"] * 367,
    ],
)
def test_invalid_calendar_rejected_without_mutation(pit, tmp_path, calendar):
    db, project = clone(pit, tmp_path)
    configure(project, calendar)
    before, previous = frozen(db, RAW), rows(db)
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=(MODEL,), command="run"
    )
    assert result.returncode != 0
    assert frozen(db, RAW) == before
    assert rows(db) == previous


@pytest.mark.parametrize(
    "case", ["calendar_cap", "row_cap", "duplicate_hub", "corrupt_hash"]
)
def test_input_and_size_guards_preserve_existing_view(pit, tmp_path, case):
    db, project = clone(pit, tmp_path)
    configure(project, None)
    if case in ["calendar_cap", "row_cap"]:
        result = execute_dbt(project, db, tmp_path / "profile", selectors=(MODEL,))
        assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db)) as con:
        if case == "calendar_cap":
            # The runtime limit also protects already-built views after Raw growth.
            con.execute(
                "INSERT INTO raw_vault.sat_request_status SELECT request_hk, snapshot_at_utc + i * INTERVAL '1 day', status, hashdiff, batch_id, record_source, mode, load_dts_utc FROM raw_vault.sat_request_status, range(1,370) t(i) WHERE batch_id='dst-before'"
            )
        elif case == "row_cap":
            con.execute(
                "INSERT INTO raw_vault.hub_request SELECT sha256('dv2:v1:request_hk:2:S13:synthetic_311;S' || length('bulk-' || i) || ':bulk-' || i || ';'), 'bulk-' || i, 'synthetic_311', 'synthetic', current_timestamp FROM range(200000) t(i)"
            )
        elif case == "duplicate_hub":
            con.execute(
                "INSERT INTO raw_vault.hub_request SELECT * FROM raw_vault.hub_request LIMIT 1"
            )
        else:
            con.execute("UPDATE raw_vault.sat_request_status SET hashdiff='bad'")
    before = frozen(db, RAW)
    result = execute_dbt(
        project, db, tmp_path / "profile", selectors=(MODEL,), command="run"
    )
    assert result.returncode != 0
    expected = {
        "calendar_cap": "exceeds 366",
        "row_cap": "exceeds 1000000",
        "duplicate_hub": "unique canonical request Hub",
        "corrupt_hash": "canonical Raw Vault status hashes",
    }[case]
    assert expected in result.stdout + result.stderr
    assert frozen(db, RAW) == before
    if case in ["calendar_cap", "row_cap"]:
        with (
            duckdb.connect(str(db), read_only=True) as con,
            pytest.raises(duckdb.InvalidInputException, match=expected),
        ):
            con.execute(
                "SELECT * FROM business_vault.pit_request_status_asof"
            ).fetchall()


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
    assert rows(db) == []


def test_hub_without_loaded_status_stays_unknown(pit, tmp_path):
    db, project = clone(pit, tmp_path)
    hk = digest(["synthetic_311", "HUB-ONLY"], "request_hk")
    with duckdb.connect(str(db)) as con:
        con.execute(
            "INSERT INTO raw_vault.hub_request VALUES (?, 'HUB-ONLY', 'synthetic_311', 'synthetic', current_timestamp)",
            [hk],
        )
    result = execute_dbt(project, db, tmp_path / "profile", selectors=(MODEL,))
    assert result.returncode == 0, result.stdout + result.stderr
    unknown = [r for r in rows(db) if r[0] == hk]
    assert len(unknown) == len(INSTANTS)
    assert all(r[4:] == (False, None, None, None, None, None) for r in unknown)


def test_calendar_at_limit_is_supported(pit, tmp_path):
    from datetime import timedelta

    db, project = clone(pit, tmp_path)
    instants = [
        datetime(2026, 2, 1, tzinfo=UTC) + timedelta(minutes=i) for i in range(366)
    ]
    configure(project, [t.isoformat() for t in instants])
    result = execute_dbt(project, db, tmp_path / "profile", selectors=(MODEL,))
    assert result.returncode == 0, result.stdout + result.stderr
    assert_independent(db, instants)
