"""Bounded Socrata extraction with schema checks, retries and count reconciliation."""

import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from ingestion.capture import LiveCapture, LiveExtractionError
from ingestion.landing import PROJECT_ROOT, SOURCE_FIELDS, _validate_records

API_ENDPOINT = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
METADATA_ENDPOINT = "https://data.cityofnewyork.us/api/views/erm2-nwe9.json"
DATE_FIELDS = {
    "created_date",
    "closed_date",
    "due_date",
    "resolution_action_updated_date",
}


@dataclass(frozen=True)
class Cohort:
    start_date: date = date(2026, 1, 1)
    end_date: date = date(2026, 7, 1)
    agencies: tuple[str, ...] = ("DEP", "DSNY")

    def __post_init__(self):
        if type(self.start_date) is not date or type(self.end_date) is not date:
            raise ValueError("Cohort bounds must be dates")
        if self.start_date >= self.end_date:
            raise ValueError("Start date must precede the exclusive end date")
        if not self.agencies or any(
            not isinstance(agency, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", agency)
            for agency in self.agencies
        ):
            raise ValueError("Agencies must be nonempty uppercase agency codes")
        object.__setattr__(self, "agencies", tuple(sorted(set(self.agencies))))

    @classmethod
    def from_strings(cls, start_date, end_date, agencies):
        return cls(
            date.fromisoformat(start_date),
            date.fromisoformat(end_date),
            tuple(agencies),
        )

    @property
    def where(self):
        agencies = ",".join(f"'{agency}'" for agency in self.agencies)
        return (
            f"created_date >= '{self.start_date.isoformat()}T00:00:00' AND "
            f"created_date < '{self.end_date.isoformat()}T00:00:00' AND "
            f"agency IN ({agencies})"
        )


DEFAULT_COHORT = Cohort()


class SocrataAPI:
    def __init__(self, client, sleep=time.sleep):
        self.client = client
        self.sleep = sleep

    def get_json(self, url, params=None):
        for attempt in range(3):
            try:
                response = self.client.get(url, params=params)
            except httpx.TransportError:
                if attempt == 2:
                    raise LiveExtractionError(
                        "Network request failed after three attempts"
                    ) from None
                self.sleep(2**attempt)
                continue
            retryable = response.status_code == 429 or 500 <= response.status_code < 600
            if retryable and attempt < 2:
                value = response.headers.get("Retry-After")
                if value is None:
                    delay = 2**attempt
                else:
                    try:
                        delay = float(value)
                    except ValueError:
                        try:
                            retry_at = parsedate_to_datetime(value)
                            if retry_at.utcoffset() is None:
                                raise ValueError("Missing timezone")
                            delay = max(
                                0, (retry_at - datetime.now(UTC)).total_seconds()
                            )
                        except (TypeError, ValueError, OverflowError):
                            raise LiveExtractionError(
                                "Invalid Retry-After; retry the batch later"
                            ) from None
                if not math.isfinite(delay) or not 0 <= delay <= 30:
                    raise LiveExtractionError(
                        "Server requests a longer wait; retry the batch later"
                    )
                self.sleep(delay)
                continue
            if not 200 <= response.status_code < 300:
                raise LiveExtractionError(
                    f"Socrata HTTP {response.status_code}; batch not published"
                )
            try:
                return response.json()
            except ValueError:
                raise LiveExtractionError("Socrata returned invalid JSON") from None
        raise AssertionError("Unreachable retry state")

    def validate_schema(self):
        metadata = self.get_json(METADATA_ENDPOINT)
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("columns"), list
        ):
            raise LiveExtractionError("Invalid dataset metadata")
        if metadata.get("id") != "erm2-nwe9":
            raise LiveExtractionError("Unexpected dataset identity")
        columns = {
            column.get("fieldName"): column.get("dataTypeName")
            for column in metadata["columns"]
            if isinstance(column, dict)
        }
        missing = sorted(set(SOURCE_FIELDS) - columns.keys())
        if missing:
            raise LiveExtractionError("Required fields missing: " + ", ".join(missing))
        for field in SOURCE_FIELDS:
            allowed = (
                {"calendar_date", "floating_timestamp"}
                if field in DATE_FIELDS
                else {"text"}
            )
            if columns[field] not in allowed:
                raise LiveExtractionError(f"Unexpected source type for {field}")

    def count(self, cohort):
        result = self.get_json(
            API_ENDPOINT,
            {
                "$select": "count(*) AS row_count",
                "$where": cohort.where,
            },
        )
        if (
            not isinstance(result, list)
            or len(result) != 1
            or not isinstance(result[0], dict)
        ):
            raise LiveExtractionError("Invalid source count response")
        value = result[0].get("row_count")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            raise LiveExtractionError("Invalid source row count")
        return int(value)

    def page(self, cohort, offset, page_size):
        result = self.get_json(
            API_ENDPOINT,
            {
                "$select": ",".join(SOURCE_FIELDS),
                "$where": cohort.where,
                "$order": "unique_key ASC",
                "$limit": page_size,
                "$offset": offset,
            },
        )
        if not isinstance(result, list) or any(
            not isinstance(row, dict) for row in result
        ):
            raise LiveExtractionError("Expected a JSON array of request objects")
        if len(result) > page_size:
            raise LiveExtractionError("Source exceeded the requested page size")
        return result


@contextmanager
def _api(client=None, sleep=time.sleep):
    if client is not None:
        yield SocrataAPI(client, sleep)
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    headers = {"Accept": "application/json"}
    if token := os.getenv("SOCRATA_APP_TOKEN"):
        headers["X-App-Token"] = token
    with httpx.Client(
        headers=headers,
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=1),
    ) as owned:
        yield SocrataAPI(owned, sleep)


def inspect_cohort(cohort=DEFAULT_COHORT, *, client=None, sleep=time.sleep):
    """Validate metadata and count a cohort without opening/writing a database."""
    with _api(client, sleep) as api:
        api.validate_schema()
        return api.count(cohort)


def load_live(
    cohort=DEFAULT_COHORT,
    *,
    database_path=None,
    page_size=5000,
    max_rows=500000,
    batch_id=None,
    client=None,
    sleep=time.sleep,
):
    """Capture the full bounded cohort; publish only reconciled observations.

    Count agreement detects common incomplete/changing extracts, not a transactional
    point-in-time snapshot. Equal-count value changes can occur during pagination.
    Repeat the same cohort for later observations; never use created_date as CDC.
    """
    if type(page_size) is not int or not 1 <= page_size <= 5000:
        raise ValueError("page_size must be an integer between 1 and 5000")
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    batch_id = batch_id if batch_id is not None else f"live-{uuid4()}"
    with LiveCapture(batch_id, cohort.where, database_path) as capture:
        with _api(client, sleep) as api:
            api.validate_schema()
            before = api.count(cohort)
            capture.expected_rows_before = before
            capture.checkpoint()
            if before > max_rows:
                raise LiveExtractionError(
                    f"Cohort has {before:,} rows, above max_rows={max_rows:,}; "
                    "narrow the dates or explicitly raise --max-rows"
                )
            records = []
            seen = set()
            while True:
                page = api.page(cohort, len(records), page_size)
                capture.page_count += 1
                capture.fetched_rows += len(page)
                capture.checkpoint()
                if not page:
                    break
                if capture.fetched_rows > max_rows or capture.fetched_rows > before:
                    raise LiveExtractionError(
                        "Extraction exceeded the expected or configured row count"
                    )
                _, rejected, _ = _validate_records(page)
                keys = [row.get("unique_key") for row in page]
                if rejected:
                    capture.rejected_rows = rejected
                    raise LiveExtractionError(
                        "Invalid records or duplicate identifiers in API page"
                    )
                duplicates = sum(key in seen for key in keys)
                if duplicates:
                    capture.rejected_rows = duplicates
                    raise LiveExtractionError("Duplicate identifiers across API pages")
                seen.update(keys)
                records.extend(page)
            capture.expected_rows_after = api.count(cohort)
            capture.snapshot_at_utc = datetime.now(UTC)
            capture.checkpoint()
            if before != capture.expected_rows_after or len(records) != before:
                raise LiveExtractionError(
                    "Source counts changed or extraction is incomplete; retry with a new batch ID"
                )
        return capture.publish(records)
