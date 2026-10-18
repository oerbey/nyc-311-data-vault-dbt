"""Audit a live extraction before networking; share the landing transaction writer."""

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ingestion.landing import (
    DEFAULT_DATABASE_PATH,
    BatchConflictError,
    BatchResult,
    BatchValidationError,
    _checksum,
    _commit_records,
    _initialize,
    _table,
    _validate_records,
)


class LiveExtractionError(RuntimeError):
    """A controlled explanation without response payloads or credentials."""


class LiveCapture:
    """Single-owner extraction context; a used batch ID is never resumed."""

    def __init__(self, batch_id, requested_filter, database_path=None):
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("batch_id must be nonempty text")
        self.batch_id = batch_id
        self.requested_filter = requested_filter
        self.path = (
            Path(database_path) if database_path is not None else DEFAULT_DATABASE_PATH
        )
        self.page_count = 0
        self.fetched_rows = 0
        self.rejected_rows = 0
        self.expected_rows_before = None
        self.expected_rows_after = None
        self.snapshot_at_utc = None
        self.published = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        try:
            _initialize(self.con)
            self.audit = _table(self.con, "ingestion_batch")
            existing = self.con.execute(
                f"SELECT 1 FROM {self.audit} WHERE batch_id=?", [self.batch_id]
            ).fetchone()
            if existing:
                raise BatchConflictError(
                    "Live batch ID already used; use a new batch ID."
                )
            now = datetime.now(UTC)
            self.con.execute(
                f"""
                INSERT INTO {self.audit} (
                    batch_id, source_id, record_source, mode, requested_filter,
                    snapshot_at_utc, started_at_utc, status, page_count, fetched_rows,
                    accepted_rows, rejected_rows
                ) VALUES (?, 'erm2-nwe9', 'NYC311:erm2-nwe9', 'live', ?, ?, ?,
                          'running', 0, 0, 0, 0)
            """,
                [self.batch_id, self.requested_filter, now, now],
            )
            return self
        except BaseException:
            self.con.close()
            raise

    def checkpoint(self):
        """Persist progress, not resumable page content."""
        self.con.execute(
            f"""
            UPDATE {self.audit} SET page_count=?, fetched_rows=?, rejected_rows=?,
                expected_rows_before=?, expected_rows_after=? WHERE batch_id=?
        """,
            [
                self.page_count,
                self.fetched_rows,
                self.rejected_rows,
                self.expected_rows_before,
                self.expected_rows_after,
                self.batch_id,
            ],
        )

    def publish(self, records):
        normalized, self.rejected_rows, message = _validate_records(records)
        self.checkpoint()
        if self.rejected_rows:
            raise BatchValidationError(f"Invalid live records: {message}")
        if self.snapshot_at_utc is None:
            raise ValueError("Capture end time is required before publishing")
        self.con.execute(
            f"""
            UPDATE {self.audit} SET snapshot_at_utc=?, checksum=? WHERE batch_id=?
        """,
            [self.snapshot_at_utc, _checksum(normalized), self.batch_id],
        )
        _commit_records(
            self.con,
            self.batch_id,
            self.snapshot_at_utc,
            "NYC311:erm2-nwe9",
            normalized,
        )
        self.published = True
        return BatchResult(self.batch_id, len(normalized), False)

    def __exit__(self, exc_type, exc, traceback):
        try:
            if not self.published:
                self.checkpoint()
                # Persist only a controlled error category, never server payloads or tokens.
                category = exc_type.__name__ if exc_type else "UnpublishedCapture"
                message = (
                    str(exc)
                    if isinstance(exc, LiveExtractionError)
                    else f"{category}: live extraction not published"
                )
                ended_at = datetime.now(UTC)
                self.con.execute(
                    f"""
                    UPDATE {self.audit} SET status='failed', finished_at_utc=?,
                        snapshot_at_utc=?, failure_message=? WHERE batch_id=?
                """,
                    [
                        ended_at,
                        self.snapshot_at_utc or ended_at,
                        message,
                        self.batch_id,
                    ],
                )
        finally:
            self.con.close()
