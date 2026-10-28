"""Verify the offline fixture's retained data and full-build replay in CI."""

import subprocess
import sys
from pathlib import Path

import duckdb


def snapshot(db):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        names = con.sql("""SELECT table_schema, table_name FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY ALL""").fetchall()
        return {
            (schema, table): con.execute(
                f'SELECT * FROM "{schema}"."{table}" ORDER BY ALL'
            ).fetchall()
            for schema, table in names
        }


def main():
    db = Path(sys.argv[1]).resolve()
    with duckdb.connect(str(db), read_only=True) as con:
        counts = con.sql("""SELECT count(*), count(*) FILTER (WHERE status='success'
            AND mode='synthetic' AND record_source='synthetic_311')
            FROM landing.ingestion_batch""").fetchone()
        if counts != (3, 3):
            raise RuntimeError(
                f"Expected three successful synthetic batches; got {counts}"
            )
        for relation, expected in [
            ("landing.request_snapshots", 9),
            ("raw_vault.hub_request", 3),
            ("marts.mart_resolution_times", 3),
        ]:
            actual = con.execute(f"SELECT count(*) FROM {relation}").fetchone()[0]
            if actual != expected:
                raise RuntimeError(
                    f"Expected {expected} rows in {relation}; got {actual}"
                )
        statuses = con.sql("""SELECT s.status FROM raw_vault.sat_request_status s
            JOIN raw_vault.hub_request h USING (request_hk)
            WHERE h.record_source='synthetic_311' AND h.request_bk='DEMO-001'
            ORDER BY s.snapshot_at_utc""").fetchall()
        if statuses != [("Open",), ("Closed",), ("Open",)]:
            raise RuntimeError(f"Unexpected DEMO-001 status history: {statuses}")
        agencies = con.sql("""SELECT a.agency_bk FROM raw_vault.lnk_request_agency l
            JOIN raw_vault.hub_request r USING (request_hk)
            JOIN raw_vault.hub_agency a USING (agency_hk)
            WHERE r.record_source='synthetic_311' AND r.request_bk='DEMO-002'
            ORDER BY a.agency_bk""").fetchall()
        if agencies != [("DEP",), ("DSNY",)]:
            raise RuntimeError(f"Unexpected DEMO-002 agency history: {agencies}")
    before = snapshot(db)
    subprocess.run(["make", "demo", f"DEMO_DATABASE={db}"], check=True)
    if snapshot(db) != before:
        raise RuntimeError("Synthetic replay changed retained rows or provenance")
    print(
        "Synthetic fixture and full-build replay passed; stored values and provenance are stable."
    )


if __name__ == "__main__":
    main()
