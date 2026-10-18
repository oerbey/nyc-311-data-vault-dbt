"""Landing contract checks use temporary files and fictional data only."""
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb
import nbformat
import pytest
from IPython.core.interactiveshell import InteractiveShell

from ingestion import landing
from ingestion.fixtures import synthetic_batches

ROOT = Path(__file__).resolve().parents[1]


def query(path, sql, params=None):
    with duckdb.connect(str(path)) as con:
        return con.execute(sql, params or []).fetchall()


def first_batch(**changes):
    return synthetic_batches()[0] | changes


def test_clean_history_and_existing_table(tmp_path):
    db = tmp_path / "warehouse.db"
    with duckdb.connect(str(db)) as con:
        con.execute('CREATE TABLE "311_service_requests" AS SELECT 42 AS sentinel')
    results = landing.load_synthetic(db)
    assert len(results) == 3
    assert not any(result.replayed for result in results)
    assert query(db, "SELECT status, accepted_rows, page_count FROM landing.ingestion_batch") == [
        ("success", 3, 0), ("success", 3, 0), ("success", 3, 0)]
    assert query(db, "SELECT count(*) FROM landing.request_snapshots") == [(9,)]
    assert query(db, 'SELECT * FROM "311_service_requests"') == [(42,)]
    assert query(db, """SELECT status FROM landing.request_snapshots
        WHERE unique_key='DEMO-001' ORDER BY snapshot_at_utc""") == [
        ("Open",), ("Closed",), ("Open",)]
    assert query(db, """SELECT agency FROM landing.request_snapshots
        WHERE unique_key='DEMO-002' ORDER BY snapshot_at_utc""") == [
        ("DEP",), ("DSNY",), ("DSNY",)]
    assert query(db, """SELECT count(DISTINCT status) FROM landing.request_snapshots
        WHERE unique_key='DEMO-003'""") == [(1,)]
    assert query(db, "SELECT DISTINCT record_source FROM landing.request_snapshots") == [
        ("synthetic_311",)]


def test_replay_preserves_audit_and_ignores_input_order(tmp_path):
    db = tmp_path / "warehouse.db"
    landing.load_synthetic(db)
    before = query(db, "SELECT * FROM landing.ingestion_batch ORDER BY batch_id")
    for batch in synthetic_batches():
        batch["records"].reverse()
        assert landing.load_batch(database_path=db, **batch).replayed
    assert before == query(db, "SELECT * FROM landing.ingestion_batch ORDER BY batch_id")
    assert query(db, "SELECT count(*) FROM landing.request_snapshots") == [(9,)]


@pytest.mark.parametrize("change", ["content", "snapshot", "filter", "source", "pages", "provenance"])
def test_conflicting_replay_is_immutable(tmp_path, change):
    db = tmp_path / "warehouse.db"
    batch = first_batch()
    landing.load_batch(database_path=db, **batch)
    before = query(db, "SELECT * FROM landing.ingestion_batch")
    if change == "content":
        batch["records"][0]["status"] = "Closed"
    elif change == "snapshot":
        batch["snapshot_at_utc"] += timedelta(hours=1)
    elif change == "filter":
        batch["requested_filter"] = "changed"
    elif change == "source":
        batch["source_id"] = "changed"
    elif change == "pages":
        batch["page_count"] = 1
    else:
        batch.update(mode="live", record_source="NYC311:erm2-nwe9")
    with pytest.raises(landing.BatchConflictError):
        landing.load_batch(database_path=db, **batch)
    assert before == query(db, "SELECT * FROM landing.ingestion_batch")
    assert query(db, "SELECT count(*) FROM landing.request_snapshots") == [(3,)]


@pytest.mark.parametrize("records,rejected", [
    ([{}], 1), ([{"unique_key": None}], 1), ([{"unique_key": "  "}], 1),
    ([{"unique_key": "x"}, {"unique_key": "x"}], 2),
    ([{"unique_key": "x"}, {"unique_key": "x", "status": "Closed"}], 2),
    ([{"unique_key": "x", "incident_zip": 123}], 1),
    ([{"unique_key": "x", "incident_address": "excluded"}], 1),
    ([None], 1),
])
def test_invalid_batches_have_audit_but_no_rows(tmp_path, records, rejected):
    db = tmp_path / "warehouse.db"
    batch = first_batch(records=records)
    with pytest.raises(landing.BatchValidationError):
        landing.load_batch(database_path=db, **batch)
    assert query(db, """SELECT status, fetched_rows, accepted_rows, rejected_rows,
        finished_at_utc IS NOT NULL, failure_message IS NOT NULL
        FROM landing.ingestion_batch""") == [("failed", len(records), 0, rejected, True, True)]
    assert query(db, "SELECT count(*) FROM landing.request_snapshots") == [(0,)]
    with pytest.raises(landing.BatchConflictError):
        landing.load_batch(database_path=db, **first_batch())


def test_partial_insert_rolls_back_and_new_id_can_retry(tmp_path, monkeypatch):
    db = tmp_path / "warehouse.db"
    original = landing._insert_rows

    def fail_after_insert(con, batch_id, snapshot, source, records):
        original(con, batch_id, snapshot, source, records[:1])
        raise RuntimeError("sensitive payload must not reach audit")

    with monkeypatch.context() as scoped:
        scoped.setattr(landing, "_insert_rows", fail_after_insert)
        with pytest.raises(RuntimeError):
            landing.load_batch(database_path=db, **first_batch())
    assert query(db, "SELECT count(*) FROM landing.request_snapshots") == [(0,)]
    assert query(db, """SELECT status, accepted_rows, rejected_rows, failure_message
        FROM landing.ingestion_batch""") == [
        ("failed", 0, 0, "RuntimeError: snapshot transaction failed")]
    assert not landing.load_batch(database_path=db, **first_batch(batch_id="retry-new-id")).replayed


def test_running_batch_requires_new_id(tmp_path):
    db = tmp_path / "warehouse.db"
    with duckdb.connect(str(db)) as con:
        landing._initialize(con)
        con.execute("""INSERT INTO landing.ingestion_batch (
            batch_id, source_id, record_source, mode, requested_filter, snapshot_at_utc,
            started_at_utc, status, page_count, fetched_rows, accepted_rows, rejected_rows)
            VALUES (?, 'synthetic_311', 'synthetic_311', 'synthetic', '', ?, ?,
                    'running', 0, 0, 0, 0)""",
                    [first_batch()["batch_id"], datetime.now(UTC), datetime.now(UTC)])
    with pytest.raises(landing.BatchConflictError):
        landing.load_batch(database_path=db, **first_batch())
    assert query(db, "SELECT status FROM landing.ingestion_batch") == [("running",)]


def test_source_values_and_utc_capture_are_preserved(tmp_path):
    db = tmp_path / "warehouse.db"
    snapshot = datetime(2026, 2, 1, 10, tzinfo=timezone(timedelta(hours=2)))
    record = {"unique_key": "00001", "incident_zip": "00123",
              "created_date": "invalid-date-retained", "closed_date": None,
              "descriptor": "", "status": "Open"}
    landing.load_batch(database_path=db, **first_batch(records=[record], snapshot_at_utc=snapshot))
    row = query(db, """SELECT unique_key, incident_zip, created_date, closed_date,
        descriptor, due_date, snapshot_at_utc FROM landing.request_snapshots""")[0]
    assert row[:6] == ("00001", "00123", "invalid-date-retained", None, "", None)
    assert row[6] == datetime(2026, 2, 1, 8, tzinfo=UTC)
    assert landing.load_batch(database_path=db, **first_batch(
        records=[record], snapshot_at_utc=snapshot.astimezone(UTC))).replayed


def test_empty_batch_and_naive_capture(tmp_path):
    db = tmp_path / "warehouse.db"
    with pytest.raises(ValueError, match="timezone-aware"):
        landing.load_batch(database_path=db, **first_batch(snapshot_at_utc=datetime(2026, 2, 1)))
    result = landing.load_batch(database_path=db, **first_batch(records=[]))
    assert result.accepted_rows == 0
    assert landing.load_batch(database_path=db, **first_batch(records=[])).replayed


def test_database_constraints(tmp_path):
    db = tmp_path / "warehouse.db"
    landing.load_synthetic(db)
    with duckdb.connect(str(db)) as con:
        with pytest.raises(duckdb.ConstraintException):
            con.execute("""INSERT INTO landing.request_snapshots
                (batch_id,snapshot_at_utc,unique_key,record_source)
                VALUES ('no-parent',current_timestamp,'x','synthetic_311')""")
        with pytest.raises(duckdb.ConstraintException):
            con.execute("""INSERT INTO landing.request_snapshots
                SELECT * FROM landing.request_snapshots LIMIT 1""")


def test_fixture_mutation_is_not_shared():
    batches = synthetic_batches()
    batches[0]["records"][0]["status"] = "mutated"
    assert synthetic_batches()[0]["records"][0]["status"] == "Open"
    assert batches[1]["records"][0]["status"] == "Closed"


def test_default_path_is_anchored_to_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert landing.DEFAULT_DATABASE_PATH == ROOT / "data/nyc_open_data.db"


def test_cli_and_replay(tmp_path):
    db = tmp_path / "cli.db"
    command = [sys.executable, "-m", "ingestion.landing", "--mode", "synthetic",
               "--database", str(db)]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    assert first.stdout.count(": loaded, 3 rows") == 3
    assert second.stdout.count(": replayed, 3 rows") == 3


@pytest.mark.parametrize("folder", [".", "output/jupyter-notebook"])
def test_walkthrough_offline(tmp_path, monkeypatch, folder):
    notebook = nbformat.read(ROOT / "output/jupyter-notebook/landing_zone_walkthrough.ipynb", 4)
    nbformat.validate(notebook)
    monkeypatch.chdir(ROOT / folder)
    monkeypatch.setenv("NYC311_DEMO_DATABASE", str(tmp_path / "notebook.db"))
    shell = InteractiveShell.instance()
    # Deny network from the notebook while retaining an in-process kernel.
    with patch("socket.socket.connect", side_effect=AssertionError("Network is forbidden")):
        for cell in notebook.cells:
            if cell.cell_type == "code":
                result = shell.run_cell(cell.source)
                assert result.error_before_exec is None
                assert result.error_in_exec is None
    assert query(tmp_path / "notebook.db", "SELECT count(*) FROM landing.request_snapshots") == [(9,)]
    assert all(not cell.get("outputs") for cell in notebook.cells)


def test_database_catalog_can_share_schema_name(tmp_path):
    db = tmp_path / "landing.db"
    landing.load_synthetic(db)
    assert query(db, 'SELECT count(*) FROM "landing".landing.request_snapshots') == [(9,)]
