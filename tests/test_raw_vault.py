"""Real dbt incremental loads against temporary, network-free DuckDB fixtures."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
import yaml

from ingestion.fixtures import synthetic_batches
from ingestion.landing import SOURCE_FIELDS, load_batch
from ingestion.socrata import DATE_FIELDS, load_live

ROOT = Path(__file__).resolve().parents[1]


def digest(values, domain):
    encoded = "".join(
        "N;" if value is None else f"S{len(value.encode('utf-8'))}:{value};"
        for value in values
    )
    return hashlib.sha256(
        f"dv2:v1:{domain}:{len(values)}:{encoded}".encode()
    ).hexdigest()


def load_records(db, batch_id, day, records, hour=8):
    return load_batch(
        database_path=db,
        batch_id=batch_id,
        records=records,
        snapshot_at_utc=datetime(2026, 2, day, hour, tzinfo=UTC),
        source_id="synthetic_311",
        record_source="synthetic_311",
        mode="synthetic",
        requested_filter="fictional Vault edge cases",
    )


def execute_dbt(
    project,
    db,
    folder,
    selectors=("+sat_request_status",),
    full_refresh=False,
    command="build",
):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "nyc_311_requests": {
                    "target": "test",
                    "outputs": {
                        "test": {
                            "type": "duckdb",
                            "path": str(db),
                            "schema": "main",
                            "threads": 2,
                        }
                    },
                }
            }
        )
    )
    args = [
        str(Path(sys.executable).parent / "dbt"),
        "--no-send-anonymous-usage-stats",
        "--no-use-colors",
        "--log-path",
        str(folder / "logs"),
        command,
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(folder),
        "--select",
        *selectors,
        "--target-path",
        str(folder / "target"),
    ]
    if full_refresh:
        args.append("--full-refresh")
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"DBT_SEND_ANONYMOUS_USAGE_STATS": "false"},
    )


def frozen_tables(db):
    with duckdb.connect(str(db), read_only=True) as con:
        return {
            table: con.execute(
                f"SELECT * FROM raw_vault.{table} ORDER BY ALL"
            ).fetchall()
            for table in ["hub_request", "sat_request_status"]
        }


@pytest.fixture(scope="module")
def vault(tmp_path_factory):
    folder = tmp_path_factory.mktemp("vault")
    db = folder / "warehouse.db"
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", "profiles.yml", ".user.yml"),
    )
    probes = [
        ([None], "probe"),
        ([""], "probe"),
        (["N;"], "probe"),
        (["NULL"], "probe"),
        (["a"], "probe"),
        (["A"], "probe"),
        ([" a "], "probe"),
        (["é"], "probe"),
        (["e\u0301"], "probe"),
        (["a|b", "c"], "probe"),
        (["a", "b|c"], "probe"),
        (["a;b", "c"], "probe"),
        ([None, ""], "probe"),
        (["", None], "probe"),
        (["0001"], "probe"),
        (["1"], "probe"),
        (["a"], "other_domain"),
    ]
    statements = []
    for index, (values, domain) in enumerate(probes):
        expressions = [
            "NULL::varchar" if value is None else "'" + value.replace("'", "''") + "'"
            for value in values
        ]
        macro = (
            "{{ dv_sha256("
            + json.dumps(expressions, ensure_ascii=False)
            + ", "
            + repr(domain)
            + ") }}"
        )
        statements.append(f"SELECT {index} AS probe_id, {macro} AS digest")
    (project / "models/hash_probe.sql").write_text(
        "{{ config(materialized='view',schema='staging') }}\n"
        + "\nUNION ALL\n".join(statements)
    )
    first, second, third = synthetic_batches()
    load_batch(database_path=db, **first)
    load_records(
        db,
        "null-initial",
        1,
        [
            {"unique_key": "NULL-CASE", "status": None},
            {"unique_key": "00001", "status": "Open"},
            {"unique_key": "1", "status": "Open"},
        ],
    )

    # Use exactly the same request business key in live and synthetic sources.
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
        records = [{"unique_key": "DEMO-001", "status": "Live fictional status"}]
        return httpx.Response(
            200, json=records if request.url.params["$offset"] == "0" else []
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        load_live(database_path=db, batch_id="live-fictional", client=client)
    phases = []

    def build(selectors=("+sat_request_status",)):
        result = execute_dbt(project, db, folder / "profile", selectors=selectors)
        assert result.returncode == 0, result.stdout + result.stderr
        phases.append(frozen_tables(db))

    build(("+sat_request_status", "hash_probe"))
    initial = phases[-1]
    load_batch(database_path=db, **second)
    build()
    load_batch(database_path=db, **third)
    build()
    after_reversal = phases[-1]
    # Unchanged daily captures and exact fixture replay must not reset load timestamps.
    load_batch(database_path=db, **first)
    load_records(
        db, "unchanged-fourth", 4, [{"unique_key": "DEMO-001", "status": "Open"}]
    )
    build()
    after_unchanged = phases[-1]
    build()
    after_replay = phases[-1]
    # Identical same-time observations can choose different input tie provenance,
    # but existing warehouse provenance and first-load timestamps remain unchanged.
    load_records(
        db, "a-identical-tie", 3, [{"unique_key": "DEMO-001", "status": "Open"}]
    )
    build()
    after_tie = phases[-1]
    # NULL, empty, literal sentinel and return-to-NULL are distinct source states.
    load_records(db, "null-empty", 4, [{"unique_key": "NULL-CASE", "status": ""}])
    load_records(db, "null-literal", 5, [{"unique_key": "NULL-CASE", "status": "NULL"}])
    load_records(db, "null-return", 6, [{"unique_key": "NULL-CASE", "status": None}])
    # A late earlier different status can append a missing boundary without
    # retracting the already stored later Closed observation.
    load_records(
        db, "late-original", 3, [{"unique_key": "LATE-CASE", "status": "Closed"}]
    )
    build()
    before_late = phases[-1]
    load_records(db, "late-earlier", 1, [{"unique_key": "LATE-CASE", "status": "Open"}])
    build()
    return {
        "db": db,
        "project": project,
        "probes": probes,
        "initial": initial,
        "after_reversal": after_reversal,
        "after_unchanged": after_unchanged,
        "after_replay": after_replay,
        "after_tie": after_tie,
        "before_late": before_late,
        "after_late": phases[-1],
    }


def test_hashes_match_independent_utf8_encoding(vault):
    with duckdb.connect(str(vault["db"]), read_only=True) as con:
        actual = con.sql(
            "SELECT digest FROM staging.hash_probe ORDER BY probe_id"
        ).fetchall()
    expected = [digest(values, domain) for values, domain in vault["probes"]]
    assert [row[0] for row in actual] == expected
    assert len(set(expected)) == len(expected)
    assert all(len(value) == 64 and value == value.lower() for value in expected)


def test_hub_identity_is_source_scoped(vault):
    with duckdb.connect(str(vault["db"]), read_only=True) as con:
        rows = con.sql(
            "SELECT request_hk,record_source,request_bk FROM raw_vault.hub_request WHERE request_bk='DEMO-001' ORDER BY record_source"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] != rows[1][0]
    for hk, source, key in rows:
        assert hk == digest([source, key], "request_hk")


def test_reversals_and_unchanged_statuses(vault):
    hk = digest(["synthetic_311", "DEMO-001"], "request_hk")
    with duckdb.connect(str(vault["db"]), read_only=True) as con:
        states = con.execute(
            "SELECT status,hashdiff,snapshot_at_utc FROM raw_vault.sat_request_status WHERE request_hk=? ORDER BY snapshot_at_utc",
            [hk],
        ).fetchall()
        unchanged_hk = digest(["synthetic_311", "DEMO-003"], "request_hk")
        assert con.execute(
            "SELECT count(*) FROM raw_vault.sat_request_status WHERE request_hk=?",
            [unchanged_hk],
        ).fetchone() == (1,)
    assert [row[0] for row in states] == ["Open", "Closed", "Open"]
    assert states[0][1] == states[2][1]
    assert states[0][2] != states[2][2]


def test_first_load_timestamps_and_provenance_survive_replay(vault):
    assert (
        vault["after_reversal"]
        == vault["after_unchanged"]
        == vault["after_replay"]
        == vault["after_tie"]
    )
    initial_hubs = vault["initial"]["hub_request"]
    assert initial_hubs == vault["after_reversal"]["hub_request"]
    for row in vault["initial"]["sat_request_status"]:
        assert row in vault["after_reversal"]["sat_request_status"]


def test_null_empty_literal_and_return_are_separate_versions(vault):
    hk = digest(["synthetic_311", "NULL-CASE"], "request_hk")
    with duckdb.connect(str(vault["db"]), read_only=True) as con:
        states = con.execute(
            "SELECT status,hashdiff FROM raw_vault.sat_request_status WHERE request_hk=? ORDER BY snapshot_at_utc",
            [hk],
        ).fetchall()
    assert [row[0] for row in states] == [None, "", "NULL", None]
    assert states[0][1] == states[-1][1]
    assert len({row[1] for row in states}) == 3


def test_safe_late_capture_preserves_existing_versions(vault):
    for row in vault["before_late"]["sat_request_status"]:
        assert row in vault["after_late"]["sat_request_status"]
    assert vault["before_late"]["hub_request"] == vault["after_late"]["hub_request"]
    hk = digest(["synthetic_311", "LATE-CASE"], "request_hk")
    with duckdb.connect(str(vault["db"]), read_only=True) as con:
        rows = con.execute(
            "SELECT status,snapshot_at_utc,load_dts_utc FROM raw_vault.sat_request_status WHERE request_hk=? ORDER BY snapshot_at_utc",
            [hk],
        ).fetchall()
    assert [row[0] for row in rows] == ["Open", "Closed"]
    assert rows[0][1] < rows[1][1]
    assert rows[0][2] >= rows[1][2]


@pytest.mark.parametrize(
    "problem", ["conflicting_tie", "history_rewrite", "full_refresh", "missing_hub"]
)
def test_unsafe_changes_fail_without_mutating_vault(vault, tmp_path, problem):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(vault["db"], db)
    before = frozen_tables(db)
    selectors = ("+sat_request_status",)
    message = ""
    if problem == "conflicting_tie":
        load_records(
            db,
            "conflict",
            3,
            [
                {"unique_key": "DEMO-001", "status": "Closed"},
                {"unique_key": "NEW-KEY", "status": "Open"},
            ],
        )
        message = "Conflicting request statuses"
    elif problem == "history_rewrite":
        load_records(
            db,
            "late-retraction",
            2,
            [
                {"unique_key": "LATE-CASE", "status": "Closed"},
                {"unique_key": "NEW-KEY", "status": "Open"},
            ],
        )
        message = "Existing status history would require rewriting"
    elif problem == "missing_hub":
        load_records(db, "new-parent", 7, [{"unique_key": "NEW-KEY", "status": "Open"}])
        selectors = ("sat_request_status",)
        message = "Missing request Hub keys"
    else:
        message = "Raw Vault is insert-only"
    result = execute_dbt(
        vault["project"],
        db,
        tmp_path / "profile",
        selectors=selectors,
        full_refresh=problem == "full_refresh",
        command="run",
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert frozen_tables(db) == before


def test_request_keys_retain_leading_zeroes(vault):
    with duckdb.connect(str(vault["db"]), read_only=True) as con:
        rows = con.sql(
            "SELECT request_bk,request_hk FROM raw_vault.hub_request WHERE request_bk IN ('00001','1') ORDER BY request_bk"
        ).fetchall()
    assert [row[0] for row in rows] == ["00001", "1"]
    assert rows[0][1] != rows[1][1]


def test_status_is_unknown_before_first_observation(vault):
    hk = digest(["synthetic_311", "DEMO-001"], "request_hk")
    with duckdb.connect(str(vault["db"]), read_only=True) as con:

        def status_at(asof):
            return con.execute(
                "SELECT status FROM raw_vault.sat_request_status WHERE request_hk=? AND snapshot_at_utc<=? ORDER BY snapshot_at_utc DESC LIMIT 1",
                [hk, asof],
            ).fetchone()

        assert status_at(datetime(2026, 1, 20, tzinfo=UTC)) is None
        assert status_at(datetime(2026, 2, 1, 8, tzinfo=UTC)) == ("Open",)
        assert status_at(datetime(2026, 2, 2, 8, tzinfo=UTC)) == ("Closed",)
        assert status_at(datetime(2026, 2, 3, 8, tzinfo=UTC)) == ("Open",)
