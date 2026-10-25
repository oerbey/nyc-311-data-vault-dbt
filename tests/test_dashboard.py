"""Offline dashboard calculations, filter semantics and Streamlit interactions."""

import hashlib
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from test_assignments import load
from test_lifecycle_metrics import record
from test_raw_vault import execute_dbt
from test_status_pit import configure

from dashboard.data import (
    DATABASE_ENV,
    QUERIES,
    DashboardUnavailable,
    agency_summary,
    context_filter,
    database_path,
    duration_summary,
    exact_label,
    export_json,
    namespaces,
    options,
    read_data,
    select_values,
    status_label,
)
from ingestion.fixtures import synthetic_batches
from ingestion.landing import load_batch

ROOT = Path(__file__).resolve().parents[1]
NS = ("synthetic_311", "synthetic")
INSTANTS = [
    "2026-01-31T00:00:00Z",
    "2026-02-01T08:00:00Z",
    "2026-02-02T08:00:00Z",
    "2026-02-03T08:00:00Z",
]


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("dashboard")
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
        "edge-cases",
        1,
        [
            record("ZERO", agency="AGG", closed_date="2026-01-01T12:00:00"),
            record("ONE", agency="AGG", closed_date="2026-01-01T13:00:00"),
            record("THREE", agency="AGG", closed_date="2026-01-01T15:00:00"),
            record("NINE", agency="AGG", closed_date="2026-01-01T21:00:00"),
            record("MISSING", agency="AGG", closed_date=None),
            record("NEGATIVE", agency="AGG", closed_date="2026-01-01T11:00:00"),
            record(
                "UNKNOWN", agency=None, created_date=None, closed_date=None, status=None
            ),
            record("EMPTY", agency="", closed_date=None, status=""),
            record("BLANK", agency=" \t", closed_date=None, status=" \t"),
            record(
                "EXACT",
                agency=" É;| ",
                complaint_type=" 'DROP;| ",
                borough=None,
                status=" Open ",
            ),
            record("LITERAL", agency="Unknown (NULL)", status="Unobserved"),
            record(
                "DST",
                agency="DST",
                created_date="2026-03-08T02:30:00",
                closed_date="2026-03-09T00:00:00",
            ),
        ],
    )
    load_batch(
        database_path=db,
        batch_id="live-namespace",
        records=[record("ONE", agency="AGG")],
        snapshot_at_utc=datetime(2026, 2, 2, 8, tzinfo=UTC),
        source_id="erm2-nwe9",
        record_source="NYC311:erm2-nwe9",
        mode="live",
        requested_filter="fictional namespace",
    )
    with duckdb.connect(str(db)) as con:
        con.execute(
            "UPDATE landing.ingestion_batch SET page_count=1,expected_rows_before=1,expected_rows_after=1 WHERE batch_id='live-namespace'"
        )
    result = execute_dbt(
        project,
        db,
        folder / "profile",
        selectors=(
            "+mart_request_trends",
            "+mart_agency_performance",
            "+mart_historical_status",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return db


def test_path_is_anchored_to_repository(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(DATABASE_ENV, raising=False)
    assert database_path() == ROOT / "data/nyc_open_data.db"
    monkeypatch.setenv(DATABASE_ENV, "data/alternate.db")
    assert database_path() == ROOT / "data/alternate.db"
    monkeypatch.setenv(DATABASE_ENV, str(tmp_path / "alternate.db"))
    assert database_path() == tmp_path / "alternate.db"


def test_missing_database_never_created(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(DashboardUnavailable, match="Database not found"):
        namespaces(path)
    assert not path.exists()


def test_snapshot_matches_governed_marts_and_is_read_only(warehouse):
    before = hashlib.sha256(warehouse.read_bytes()).hexdigest()
    assert namespaces(warehouse)[0] == NS
    data = read_data(warehouse, NS)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        for key, sql in QUERIES.items():
            expected = con.execute(sql, list(NS)).fetchdf()
            pd.testing.assert_frame_equal(data[key], expected)
    assert hashlib.sha256(warehouse.read_bytes()).hexdigest() == before
    assert not Path(str(warehouse) + ".wal").exists()
    # A write connection can reopen after the dashboard read; no cached reader is left.
    with duckdb.connect(str(warehouse)) as con:
        assert con.sql("SELECT 1").fetchone() == (1,)


def test_namespace_parameterization_and_no_implicit_mixing(warehouse):
    live = read_data(warehouse, ("NYC311:erm2-nwe9", "live"))
    assert len(live["population"]) == 1
    missing = read_data(
        warehouse, ("';DROP TABLE raw_vault.hub_request;--", "synthetic")
    )
    assert all(frame.empty for frame in missing.values())
    assert len(read_data(warehouse, NS)["population"]) == 15


def test_oversized_results_fail_instead_of_truncating(warehouse):
    with pytest.raises(DashboardUnavailable, match="row limit"):
        read_data(warehouse, NS, max_rows=1)


def test_pooled_statistics_zero_negative_and_missing(warehouse):
    p = select_values(
        read_data(warehouse, NS)["population"], "observed_agency_bk", ["AGG"]
    )
    result = duration_summary(p)
    assert result == {
        "retained_requests": 6,
        "eligible_durations": 4,
        "excluded_durations": 2,
        "usable_closure_dates": 5,
        "duration_coverage_share": 4 / 6,
        "reported_closure_date_share": 5 / 6,
        "mean_hours": 3.25,
        "median_hours": 2.0,
        "p90_hours": pytest.approx(7.2),
    }
    assert agency_summary(p).iloc[0].median_hours == 2.0
    # Split-cohort quantiles cannot be pooled by averaging their medians.
    split = p.copy()
    split.loc[split.resolution_hours == 9, "created_cohort_month_nyc"] = pd.Timestamp(
        "2026-02-01"
    )
    assert duration_summary(split)["median_hours"] == 2.0


def test_empty_and_all_ineligible_have_null_statistics(warehouse):
    p = read_data(warehouse, NS)["population"]
    for group in [p.iloc[:0], select_values(p, "observed_agency_bk", [None])]:
        result = duration_summary(group)
        assert result["eligible_durations"] == 0
        assert all(
            result[f] is None for f in ["mean_hours", "median_hours", "p90_hours"]
        )
    assert duration_summary(p.iloc[:0])["duration_coverage_share"] is None


def test_exact_null_empty_whitespace_unicode_filtering(warehouse):
    p = read_data(warehouse, NS)["population"]
    for value in [None, "", " \t", " É;| ", "Unknown (NULL)"]:
        assert len(select_values(p, "observed_agency_bk", [value])) == 1
    assert select_values(p, "observed_agency_bk", []).empty
    assert len({exact_label(v) for v in [None, "", " \t", "Unknown (NULL)"]}) == 4
    assert None in options(p, "created_cohort_month_nyc")
    assert (
        len(
            context_filter(
                p, {"observed_agency_bk": [None], "created_cohort_month_nyc": [None]}
            )
        )
        == 1
    )


def test_status_unknown_null_blank_and_returned_agency(warehouse):
    s = read_data(warehouse, NS)["status"]
    earliest = s[s.as_of_utc == pd.Timestamp(INSTANTS[0])]
    assert (
        earliest.request_count.sum()
        == earliest.status_unknown_request_count.sum()
        == 15
    )
    assert set(earliest.apply(status_label, axis=1)) == {"Unobserved"}
    selected = s[s.as_of_utc == pd.Timestamp(INSTANTS[-1])]
    labels = set(selected.apply(status_label, axis=1))
    assert {"Observed NULL", '""', '" \\t"', '" Open "', '"Unobserved"'} <= labels
    # DEMO-002 moves to DSNY while its Open status capture remains unchanged.
    assert selected[selected.agency_bk == "DSNY"].request_count.sum() == 2
    assert selected.status_observed_request_count.sum() == 15
    assert [
        int(
            s[
                (s.as_of_utc == pd.Timestamp(t)) & (s.status == "Closed")
            ].request_count.sum()
        )
        for t in INSTANTS
    ] == [0, 1, 2, 1]


def test_export_preserves_filtered_rows_scope_nulls_and_query(warehouse):
    data = read_data(warehouse, NS)
    selected = select_values(data["trends"], "observed_agency_bk", [None, " É;| "])
    result = json.loads(
        export_json(
            selected,
            NS,
            "trends",
            {"observed_agency_bk": [None, " É;| "]},
            "test definition",
        )
    )
    assert result["rows"] == json.loads(
        selected.to_json(
            orient="records", date_format="iso", date_unit="us", double_precision=15
        )
    )
    assert result["parameters"] == list(NS) and result["sql"] == QUERIES["trends"]
    assert result["filters"]["observed_agency_bk"] == [None, " É;| "]
    assert all("request_hk" not in r for r in result["rows"])
    precise = pd.DataFrame({"as_of_utc": [pd.Timestamp("2026-02-01T08:00:00.123456Z")]})
    encoded = json.loads(export_json(precise, NS, "status", {}, "capture precision"))
    assert encoded["rows"][0]["as_of_utc"] == "2026-02-01T08:00:00.123456Z"


@pytest.fixture
def app(warehouse, monkeypatch):
    monkeypatch.setenv(DATABASE_ENV, str(warehouse))
    result = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=20).run()
    assert not result.exception
    return result


def metrics(app):
    return {m.label: m.value for m in app.metric}


def select(app, label):
    return next(s for s in app.selectbox if s.label == label)


def multi(app, label):
    return next(s for s in app.multiselect if s.label == label)


def test_app_defaults_to_synthetic_and_matches_event_mart(app, warehouse):
    assert app.sidebar.selectbox[0].value == NS
    df = read_data(warehouse, NS)["trends"]
    assert metrics(app)["Usable creation events"] == str(df.created_request_count.sum())
    assert metrics(app)["Usable closure events"] == str(df.closed_request_count.sum())
    for name in ["Agency", "Problem", "Borough"]:
        select(app, "Compare event volumes by").set_value(name).run()
        assert not app.exception


def test_event_filters_and_empty_selection(app, warehouse):
    multi(app, "Agency context").set_value(["AGG"]).run()
    assert metrics(app) == {"Usable creation events": "6", "Usable closure events": "5"}
    app.date_input[0].set_value((date(2026, 1, 1), date(2026, 1, 1))).run()
    assert not app.exception
    multi(app, "Agency context").set_value([]).run()
    assert not app.exception and len(app.metric) == 0
    assert any("No usable event dates" in item.value for item in app.info)


def test_event_date_scope_and_incomplete_range(app):
    # Global fixture spans Jan 1 through source dates in March; only Jan 1 is retained here.
    app.date_input[0].set_value((date(2026, 1, 1), date(2026, 1, 1))).run()
    assert not app.exception
    assert metrics(app)["Usable creation events"] == "10"
    app.date_input[0].set_value((date(2026, 1, 1),)).run()
    assert not app.exception and any("both ends" in m.value for m in app.info)


def test_duration_filters_quantiles_and_unknown_cohort(app):
    app.sidebar.radio[0].set_value("Agency & duration").run()
    multi(app, "Agency context").set_value(["AGG"]).run()
    assert not app.exception
    assert metrics(app) == {
        "Retained requests": "6",
        "Eligible durations": "4",
        "Excluded durations": "2",
        "Median eligible duration": "2.00 h",
    }
    multi(app, "Agency context").set_value([None]).run()
    multi(app, "NYC creation month").set_value([None]).run()
    assert not app.exception
    assert metrics(app)["Retained requests"] == "1"
    assert metrics(app)["Median eligible duration"] == "—"
    multi(app, "Agency context").set_value([]).run()
    assert not app.exception and metrics(app)["Retained requests"] == "0"


def test_status_asof_agency_and_coverage_controls(app):
    app.sidebar.radio[0].set_value("Observed status").run()
    assert not app.exception and metrics(app)["Observed status"] == "15"
    select(app, "As-of instant (UTC)").set_value(pd.Timestamp(INSTANTS[0])).run()
    assert (
        metrics(app)["Observed status"] == "0"
        and metrics(app)["Unknown status"] == "15"
    )
    assert metrics(app)["Status coverage"] == "0.0%"
    select(app, "As-of instant (UTC)").set_value(pd.Timestamp(INSTANTS[-1])).run()
    multi(app, "Observed-as-of agency").set_value(["DSNY"]).run()
    assert not app.exception and metrics(app)["Retained identities at instant"] == "2"
    multi(app, "Observed-as-of agency").set_value([]).run()
    assert not app.exception and metrics(app)["Status coverage"] == "—"


def test_namespace_switch_and_refresh_keep_filters_separate(app):
    app.sidebar.selectbox[0].set_value(("NYC311:erm2-nwe9", "live")).run()
    assert not app.exception and metrics(app)["Usable creation events"] == "1"
    app.sidebar.radio[0].set_value("Agency & duration").run()
    assert metrics(app)["Retained requests"] == "1"
    app.sidebar.button[0].click().run()
    assert not app.exception and metrics(app)["Retained requests"] == "1"
    app.sidebar.selectbox[0].set_value(NS).run()
    assert not app.exception and metrics(app)["Retained requests"] == "15"


def test_no_source_network_or_database_writes(app, warehouse, monkeypatch):
    import socket

    before = hashlib.sha256(warehouse.read_bytes()).hexdigest()

    def blocked(*args, **kwargs):
        raise AssertionError("The dashboard must not use the network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    for view in ["Event trends", "Agency & duration", "Observed status"]:
        app.sidebar.radio[0].set_value(view).run()
        assert not app.exception
    assert hashlib.sha256(warehouse.read_bytes()).hexdigest() == before


def test_app_missing_database_actionable_error(monkeypatch, tmp_path):
    db = tmp_path / "absent.db"
    monkeypatch.setenv(DATABASE_ENV, str(db))
    app = AppTest.from_file(str(ROOT / "dashboard/app.py")).run()
    assert not app.exception and "Database not found" in app.error[0].value
    assert "dbt build" in app.code[0].value and not db.exists()


def test_missing_views_are_not_synthetic_fallback(warehouse, monkeypatch, tmp_path):
    db = tmp_path / "warehouse.db"
    shutil.copyfile(warehouse, db)
    with duckdb.connect(str(db)) as con:
        con.execute("DROP VIEW marts.mart_request_trends")
    monkeypatch.setenv(DATABASE_ENV, str(db))
    app = AppTest.from_file(str(ROOT / "dashboard/app.py")).run()
    assert (
        not app.exception and "Reporting data could not be read" in app.error[0].value
    )
    assert not app.metric


def test_empty_population_and_empty_calendar(warehouse, monkeypatch):
    from dashboard import data

    monkeypatch.setenv(DATABASE_ENV, str(warehouse))
    original = data.read_data

    def no_calendar(*args, **kwargs):
        result = original(*args, **kwargs)
        result["status"] = result["status"].iloc[:0]
        return result

    monkeypatch.setattr(data, "read_data", no_calendar)
    app = AppTest.from_file(str(ROOT / "dashboard/app.py")).run()
    app.sidebar.radio[0].set_value("Observed status").run()
    assert not app.exception and any(
        "No status PIT instants" in i.value for i in app.info
    )
    monkeypatch.setattr(data, "namespaces", lambda path: [])
    app.run()
    assert not app.exception and any("population is empty" in i.value for i in app.info)


@pytest.mark.parametrize("ns", [NS, ("NYC311:erm2-nwe9", "live")])
def test_summary_reconciles_with_independent_sql(warehouse, ns):
    result = duration_summary(read_data(warehouse, ns)["population"])
    with duckdb.connect(str(warehouse), read_only=True) as con:
        r = con.execute(
            """SELECT count(*),sum(duration_eligible_count),
            sum(duration_ineligible_count),sum(closed_event_eligible_count),
            avg(resolution_hours),median(resolution_hours),quantile_cont(resolution_hours,0.9)
            FROM marts.mart_resolution_times WHERE record_source=? AND mode=?""",
            list(ns),
        ).fetchone()
    for field, expected in zip(
        [
            "retained_requests",
            "eligible_durations",
            "excluded_durations",
            "usable_closure_dates",
            "mean_hours",
            "median_hours",
            "p90_hours",
        ],
        r,
        strict=True,
    ):
        assert (
            result[field] == pytest.approx(expected)
            if expected is not None
            else result[field] is None
        )


def test_status_totals_are_per_instant_and_not_summed(warehouse):
    for ns, n in [(NS, 15), (("NYC311:erm2-nwe9", "live"), 1)]:
        frame = read_data(warehouse, ns)["status"]
        for _, group in frame.groupby("as_of_utc"):
            assert group.request_count.sum() == n
            assert (
                group.status_observed_request_count.sum()
                + group.status_unknown_request_count.sum()
                == n
            )
        assert frame.request_count.sum() == n * len(INSTANTS)


def test_empty_event_date_range_retains_empty_state(app):
    app.date_input[0].set_value((date(2026, 1, 14), date(2026, 1, 15))).run()
    assert not app.exception
    assert metrics(app) == {"Usable creation events": "0", "Usable closure events": "0"}
    assert any("No event observations" in i.value for i in app.info)


def test_reset_restores_view_defaults(app):
    multi(app, "Agency context").set_value([]).run()
    assert not app.metric
    app.sidebar.button[1].click().run()
    assert not app.exception and metrics(app)["Usable creation events"] == "13"


def test_duration_export_includes_pooled_plot_values_and_lineage(warehouse):
    data = read_data(warehouse, NS)
    p = select_values(data["population"], "observed_agency_bk", ["AGG"])
    detail = select_values(data["agency"], "observed_agency_bk", ["AGG"])
    exported = json.loads(
        export_json(
            detail,
            NS,
            "agency",
            {"observed_agency_bk": ["AGG"]},
            "duration",
            agency_summary(p),
        )
    )
    derived = exported["derived_statistics"]
    assert derived["rows"][0]["median_hours"] == 2.0
    assert (
        derived["source_query_id"] == "population"
        and derived["sql"] == QUERIES["population"]
    )
    assert "request_hk" not in derived["rows"][0]
