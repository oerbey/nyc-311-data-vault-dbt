"""Append immutable observations and atomically publish successful batches."""
import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ingestion.fixtures import synthetic_batches

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "nyc_open_data.db"
SOURCE_FIELDS = (
    "unique_key", "created_date", "closed_date", "agency", "agency_name",
    "complaint_type", "descriptor", "status", "due_date",
    "resolution_action_updated_date", "resolution_description", "borough",
    "incident_zip", "community_board", "open_data_channel_type",
)


class BatchConflictError(ValueError):
    """A previously used batch ID cannot accept this load."""


class BatchValidationError(ValueError):
    """One or more records failed the landing contract."""


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    accepted_rows: int
    replayed: bool


def _table(con, name):
    # Fully qualify names even when a custom database filename is "landing.db".
    catalog = con.execute("SELECT current_database()").fetchone()[0]
    return '"' + catalog.replace('"', '""') + '".landing.' + name


def _initialize(con):
    fields = ",\n".join(
        f'{field} VARCHAR' + (" NOT NULL CHECK (length(trim(unique_key)) > 0)"
                             if field == "unique_key" else "")
        for field in SOURCE_FIELDS
    )
    con.execute("CREATE SCHEMA IF NOT EXISTS landing")
    audit_table = _table(con, "ingestion_batch")
    snapshot_table = _table(con, "request_snapshots")
    con.execute(f"USE {audit_table.rsplit('.', 1)[0]}")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {audit_table} (
            batch_id VARCHAR PRIMARY KEY,
            source_id VARCHAR NOT NULL,
            record_source VARCHAR NOT NULL,
            mode VARCHAR NOT NULL CHECK (mode IN ('synthetic', 'live')),
            requested_filter VARCHAR NOT NULL,
            snapshot_at_utc TIMESTAMPTZ NOT NULL,
            started_at_utc TIMESTAMPTZ NOT NULL,
            finished_at_utc TIMESTAMPTZ,
            status VARCHAR NOT NULL CHECK (status IN ('running', 'success', 'failed')),
            page_count BIGINT NOT NULL CHECK (page_count >= 0),
            fetched_rows BIGINT NOT NULL CHECK (fetched_rows >= 0),
            accepted_rows BIGINT NOT NULL CHECK (accepted_rows >= 0),
            rejected_rows BIGINT NOT NULL CHECK (rejected_rows >= 0),
            checksum VARCHAR,
            failure_message VARCHAR
        )
    """)
    for column in ("expected_rows_before", "expected_rows_after"):
        con.execute(f"ALTER TABLE {audit_table} ADD COLUMN IF NOT EXISTS {column} BIGINT")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {snapshot_table} (
            batch_id VARCHAR NOT NULL REFERENCES ingestion_batch(batch_id),
            snapshot_at_utc TIMESTAMPTZ NOT NULL,
            {fields},
            record_source VARCHAR NOT NULL,
            PRIMARY KEY (batch_id, unique_key)
        )
    """)


def _validate_records(records):
    normalized = []
    invalid = set()
    reasons = set()
    keys = Counter(
        row.get("unique_key") for row in records
        if isinstance(row, Mapping) and isinstance(row.get("unique_key"), str)
    )
    for index, row in enumerate(records):
        if not isinstance(row, Mapping):
            invalid.add(index)
            reasons.add("records must be objects")
            continue
        key = row.get("unique_key")
        if not isinstance(key, str) or not key.strip():
            invalid.add(index)
            reasons.add("missing or empty request identifiers")
        elif keys[key] > 1:
            invalid.add(index)
            reasons.add("duplicate request identifiers")
        if set(row) - set(SOURCE_FIELDS):
            invalid.add(index)
            reasons.add("unsupported source fields")
        if any(value is not None and not isinstance(value, str) for value in row.values()):
            invalid.add(index)
            reasons.add("source values must be text or null")
        normalized.append({field: row.get(field) for field in SOURCE_FIELDS})
    message = "; ".join(sorted(reasons))
    return normalized, len(invalid), message


def _checksum(records):
    canonical = json.dumps(
        sorted(records, key=lambda row: row["unique_key"]),
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _insert_rows(con, batch_id, snapshot_at_utc, record_source, records):
    if not records:
        return
    columns = ("batch_id", "snapshot_at_utc", *SOURCE_FIELDS, "record_source")
    placeholders = ",".join("?" for _ in columns)
    con.executemany(
        f"INSERT INTO {_table(con, 'request_snapshots')} ({','.join(columns)}) VALUES ({placeholders})",
        [(batch_id, snapshot_at_utc, *(row[field] for field in SOURCE_FIELDS), record_source)
         for row in records],
    )


def load_batch(*, records, batch_id, snapshot_at_utc, source_id, record_source,
               mode, requested_filter, page_count=0, database_path=None):
    """Load one complete supplied observation batch; never fetch from a network.

    A successful identical replay does not change rows or audit timestamps.
    Failed/running IDs and conflicting successful IDs require a new batch ID.
    Unknown fields and non-text source values are rejected, not silently coerced.
    Only structural validation happens here; source dates retain raw strings.
    """
    for name, value in (("batch_id", batch_id), ("source_id", source_id),
                        ("record_source", record_source)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be nonempty text")
    if mode not in ("synthetic", "live"):
        raise ValueError("mode must be synthetic or live")
    if mode == "synthetic" and record_source != "synthetic_311":
        raise ValueError("synthetic batches must use synthetic_311 provenance")
    if not isinstance(requested_filter, str):
        raise TypeError("requested_filter must be text")
    if type(page_count) is not int or page_count < 0:
        raise ValueError("page_count must be a nonnegative integer")
    if (not isinstance(snapshot_at_utc, datetime)
            or snapshot_at_utc.utcoffset() is None):
        raise ValueError("snapshot_at_utc must be a timezone-aware datetime")
    snapshot_at_utc = snapshot_at_utc.astimezone(UTC)
    records = list(records)
    normalized, rejected_rows, validation_message = _validate_records(records)
    checksum = None if rejected_rows else _checksum(normalized)
    path = Path(database_path) if database_path is not None else DEFAULT_DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        _initialize(con)
        con.execute("SET TimeZone='UTC'")
        audit_table = _table(con, "ingestion_batch")
        existing = con.execute(f"""
            SELECT status, checksum, source_id, record_source, mode, requested_filter,
                   snapshot_at_utc, page_count, accepted_rows
            FROM {audit_table} WHERE batch_id = ?
        """, [batch_id]).fetchone()
        if existing:
            identity = (source_id, record_source, mode, requested_filter,
                        snapshot_at_utc, page_count)
            if (existing[0] == "success" and checksum is not None
                    and existing[1] == checksum and existing[2:8] == identity):
                return BatchResult(batch_id, existing[8], True)
            raise BatchConflictError(
                "Batch ID already used with different content/metadata or an unfinished "
                "or failed load; use a new batch ID."
            )
        con.execute(f"""
            INSERT INTO {audit_table} (
                batch_id, source_id, record_source, mode, requested_filter,
                snapshot_at_utc, started_at_utc, status, page_count, fetched_rows,
                accepted_rows, rejected_rows, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, 0, ?, ?)
        """, [batch_id, source_id, record_source, mode, requested_filter,
              snapshot_at_utc, datetime.now(UTC), page_count, len(records),
              rejected_rows, checksum])
        if rejected_rows:
            con.execute(f"""
                UPDATE {audit_table}
                SET status='failed', finished_at_utc=?, failure_message=? WHERE batch_id=?
            """, [datetime.now(UTC), validation_message, batch_id])
            raise BatchValidationError(
                f"Batch validation failed for {rejected_rows} record(s): {validation_message}"
            )
        _commit_records(con, batch_id, snapshot_at_utc, record_source, normalized)
    return BatchResult(batch_id, len(normalized), False)


def _commit_records(con, batch_id, snapshot_at_utc, record_source, normalized):
    audit_table = _table(con, "ingestion_batch")
    con.execute("BEGIN TRANSACTION")
    try:
        _insert_rows(con, batch_id, snapshot_at_utc, record_source, normalized)
        con.execute(f"""
            UPDATE {audit_table} SET status='success',
                finished_at_utc=?, accepted_rows=? WHERE batch_id=?
        """, [datetime.now(UTC), len(normalized), batch_id])
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")
        # Do not persist exception text: database errors can expose source values.
        con.execute(f"""
            UPDATE {audit_table} SET status='failed', finished_at_utc=?,
                failure_message=? WHERE batch_id=?
        """, [datetime.now(UTC), f"{type(exc).__name__}: snapshot transaction failed", batch_id])
        raise


def load_synthetic(database_path=None):
    """Load or replay the three fixed fixture batches sequentially."""
    return [load_batch(database_path=database_path, **batch) for batch in synthetic_batches()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["synthetic", "live"], required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default="2026-07-01", help="Exclusive upper date bound")
    parser.add_argument("--agencies", nargs="+", default=["DEP", "DSNY"])
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--max-rows", type=int, default=500000)
    parser.add_argument("--batch-id", help="Optional unique live batch ID; existing IDs cannot be reused")
    parser.add_argument("--count-only", action="store_true", help="Check live schema/count without writing a batch")
    args = parser.parse_args()
    if args.mode == "live":
        from ingestion.socrata import (
            Cohort,
            LiveExtractionError,
            inspect_cohort,
            load_live,
        )

        try:
            cohort = Cohort.from_strings(args.start_date, args.end_date, args.agencies)
            if args.count_only:
                print(f"Matching rows: {inspect_cohort(cohort)}")
                return
            result = load_live(
                cohort, database_path=args.database, page_size=args.page_size,
                max_rows=args.max_rows, batch_id=args.batch_id,
            )
        except (LiveExtractionError, BatchConflictError, ValueError) as exc:
            parser.exit(1, f"Live ingestion failed: {exc}\n")
        print(f"{result.batch_id}: loaded, {result.accepted_rows} rows")
        print(f"Database: {args.database.resolve()}")
        return
    if args.count_only or args.batch_id:
        parser.error("--count-only and --batch-id require --mode live")
    for result in load_synthetic(args.database):
        action = "replayed" if result.replayed else "loaded"
        print(f"{result.batch_id}: {action}, {result.accepted_rows} rows")
    print(f"Database: {args.database.resolve()}")


if __name__ == "__main__":
    main()
