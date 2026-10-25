"""Explicit reporting projections, read-only snapshots, and pooled calculations."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATABASE_ENV = "NYC311_DASHBOARD_DATABASE"
MAX_ROWS = 1_000_000
BUILD_COMMAND = (
    "dbt build --profiles-dir . --select +mart_request_trends "
    "+mart_agency_performance +mart_historical_status"
)
QUERIES = {
    "population": """SELECT r.request_hk,p.observed_agency_bk,p.observed_complaint_type,
        p.observed_borough,r.created_cohort_month_nyc,r.resolution_hours,
        r.request_count,r.duration_eligible_count,r.duration_ineligible_count,
        r.created_event_eligible_count,r.closed_event_eligible_count,
        r.duration_quality_status,r.is_lifecycle_observed,r.snapshot_at_utc
        FROM marts.mart_resolution_times r
        JOIN marts.int_request_trend_population p USING (request_hk)
        WHERE r.record_source=? AND r.mode=? ORDER BY r.request_hk""",
    "trends": """SELECT event_date_nyc,observed_agency_bk,observed_complaint_type,
        observed_borough,created_request_count,closed_request_count
        FROM marts.mart_request_trends WHERE record_source=? AND mode=? ORDER BY ALL""",
    "agency": """SELECT observed_agency_bk,created_cohort_month_nyc,retained_request_count,
        usable_closure_date_count,duration_eligible_count,duration_ineligible_count,
        reported_closure_date_share,duration_coverage_share,mean_resolution_hours,
        median_resolution_hours,p90_resolution_hours
        FROM marts.mart_agency_performance WHERE record_source=? AND mode=? ORDER BY ALL""",
    "status": """SELECT as_of_utc,is_agency_observed,agency_hk,agency_bk,
        is_status_observed,status,status_class,request_count,
        status_observed_request_count,status_unknown_request_count,
        status_null_request_count,status_blank_request_count,
        min_selected_status_capture_at_utc,max_selected_status_capture_at_utc,
        min_selected_agency_capture_at_utc,max_selected_agency_capture_at_utc
        FROM marts.mart_historical_status WHERE record_source=? AND mode=? ORDER BY ALL""",
}


class DashboardUnavailable(RuntimeError):
    """A usable reporting snapshot could not be read."""


def database_path():
    value = os.environ.get(DATABASE_ENV)
    path = Path(value).expanduser() if value else ROOT / "data/nyc_open_data.db"
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


@contextmanager
def connect(path):
    path = Path(path)
    if not path.is_file():
        raise DashboardUnavailable(
            "Database not found. Load data and build the reporting marts first."
        )
    try:
        with duckdb.connect(str(path), read_only=True) as con:
            con.execute("SET TimeZone='UTC'")
            con.execute("BEGIN TRANSACTION")
            yield con
            con.execute("COMMIT")
    except duckdb.Error as exc:
        raise DashboardUnavailable(
            "Reporting data could not be read. Close any warehouse writer, then build or verify the marts."
        ) from exc


def namespaces(path):
    with connect(path) as con:
        return con.execute("""SELECT DISTINCT record_source,mode FROM marts.mart_resolution_times
            ORDER BY CASE WHEN mode='synthetic' THEN 0 ELSE 1 END,record_source,mode""").fetchall()


def read_data(path, namespace, max_rows=MAX_ROWS):
    """All four projections share one transaction; no connection or result cache survives."""
    with connect(path) as con:
        frames = {}
        for name, sql in QUERIES.items():
            frame = con.execute(
                f"SELECT * FROM ({sql}) LIMIT ?", [*namespace, max_rows + 1]
            ).fetchdf()
            if len(frame) > max_rows:
                raise DashboardUnavailable(
                    "Reporting data exceeds the dashboard row limit; narrow the warehouse cohort."
                )
            frames[name] = frame
        expected = con.execute(
            "SELECT count(*) FROM marts.mart_resolution_times WHERE record_source=? AND mode=?",
            list(namespace),
        ).fetchone()[0]
        if (
            len(frames["population"]) != expected
            or frames["population"].request_hk.duplicated().any()
        ):
            raise DashboardUnavailable(
                "Reporting population is inconsistent. Verify the marts before reporting."
            )
        return frames


def exact_label(value):
    """UI labels are lossless; NULL never collides with a literal source string."""
    return (
        "Unknown (NULL)"
        if pd.isna(value)
        else json.dumps(str(value), ensure_ascii=False)
    )


def options(frame, column):
    values = [None if pd.isna(v) else v for v in frame[column].unique()]
    return sorted(values, key=lambda v: (v is not None, str(v)))


def select_values(frame, column, values):
    has_null = any(v is None for v in values)
    return frame[
        frame[column].isin([v for v in values if v is not None])
        | (frame[column].isna() & has_null)
    ].copy()


def context_filter(frame, selections):
    for col, values in selections.items():
        frame = select_values(frame, col, values)
    return frame.copy()


def duration_summary(frame):
    values = frame.loc[frame.duration_eligible_count == 1, "resolution_hours"].dropna()
    total = int(frame.request_count.sum())
    eligible = int(frame.duration_eligible_count.sum())
    closures = int(frame.closed_event_eligible_count.sum())
    return {
        "retained_requests": total,
        "eligible_durations": eligible,
        "excluded_durations": int(frame.duration_ineligible_count.sum()),
        "usable_closure_dates": closures,
        "duration_coverage_share": eligible / total if total else None,
        "reported_closure_date_share": closures / total if total else None,
        "mean_hours": float(values.mean()) if len(values) else None,
        "median_hours": float(values.median()) if len(values) else None,
        "p90_hours": float(values.quantile(0.9, interpolation="linear"))
        if len(values)
        else None,
    }


def agency_summary(frame):
    rows = []
    for key in options(frame, "observed_agency_bk"):
        group = select_values(frame, "observed_agency_bk", [key])
        rows.append({"agency": exact_label(key), **duration_summary(group)})
    return pd.DataFrame(rows)


def status_label(row):
    if not row["is_status_observed"]:
        return "Unobserved"
    if pd.isna(row["status"]):
        return "Observed NULL"
    return exact_label(row["status"])


def export_json(frame, namespace, query_id, filters, definitions, derived=None):
    """Export exactly the displayed aggregate rows with scope and reproducible query."""
    derived_data = (
        None
        if derived is None
        else {
            "rows": json.loads(
                derived.to_json(
                    orient="records",
                    date_format="iso",
                    date_unit="us",
                    double_precision=15,
                )
            ),
            "source_query_id": "population",
            "sql": QUERIES["population"],
            "method": "Apply the same source/agency/cohort filters to request-level rows; group by exact agency, sum counters, and compute mean/median/linear p90 over eligible resolution_hours. Shares divide summed numerators by retained requests.",
        }
    )
    return json.dumps(
        {
            "record_source": namespace[0],
            "mode": namespace[1],
            "query_id": query_id,
            "sql": QUERIES[query_id],
            "parameters": list(namespace),
            "filters": filters,
            "definitions": definitions,
            "derived_statistics": derived_data,
            "rows": json.loads(
                frame.to_json(
                    orient="records",
                    date_format="iso",
                    date_unit="us",
                    double_precision=15,
                )
            ),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
