"""Execute the dbt staging model against isolated, network-free landing fixtures."""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
import yaml

from ingestion.fixtures import synthetic_batches
from ingestion.landing import SOURCE_FIELDS, load_batch, load_synthetic
from ingestion.socrata import DATE_FIELDS, load_live

ROOT = Path(__file__).resolve().parents[1]


def edge_records():
    cases = {
        "winter": {"created_date": "2026-01-01T12:00:00"},
        "summer": {"created_date": "2026-07-01T12:00:00"},
        "whitespace": {"created_date": " 2026-01-01T12:00:00.123456 "},
        "malformed": {"created_date": "not-a-date"},
        "invalid_calendar": {"created_date": "2026-02-30T12:00:00"},
        "invalid_hour": {"created_date": "2026-01-01T24:00:00"},
        "offset": {"created_date": "2026-01-01T12:00:00-05:00"},
        "date_only": {"created_date": "2026-01-01"},
        "missing": {"created_date": None},
        "blank": {"created_date": " \t"},
        "negative": {
            "created_date": "2026-01-02T12:00:00",
            "closed_date": "2026-01-01T12:00:00",
        },
        "ambiguous": {
            "created_date": "2026-11-01T01:30:00",
            "closed_date": "2026-11-01T02:30:00",
        },
        "nonexistent": {"created_date": "2026-03-08T02:30:00"},
        "fall_span": {
            "created_date": "2026-11-01T00:30:00",
            "closed_date": "2026-11-01T02:30:00",
        },
        "spring_span": {
            "created_date": "2026-03-08T01:59:00",
            "closed_date": "2026-03-08T03:01:00",
        },
        "optional_issues": {
            "due_date": "2026-03-08T02:30:00",
            "resolution_action_updated_date": "2026-11-01T01:30:00",
            "closed_date": "bad",
        },
        "optional_blank": {"due_date": "", "closed_date": "   "},
        "missing_attributes": {"agency": None, "complaint_type": None, "status": " "},
        "00001": {},
    }
    return [
        {
            "unique_key": key,
            "created_date": "2026-01-01T12:00:00",
            "agency": "DEP",
            "complaint_type": "Synthetic",
            "status": "Open",
            "incident_zip": "00123",
            **values,
        }
        for key, values in cases.items()
    ]


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("staging")
    db = folder / "warehouse.db"
    load_synthetic(db)
    load_batch(
        records=edge_records(),
        batch_id="edge-cases",
        snapshot_at_utc=datetime(2027, 1, 1, tzinfo=UTC),
        source_id="synthetic_311",
        record_source="synthetic_311",
        mode="synthetic",
        requested_filter="fictional staging edge cases",
        database_path=db,
    )

    # Include a live-mode observation through the production adapter, with no network.
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
        data = [edge_records()[0] | {"unique_key": "LIVE-FICTIONAL"}]
        return httpx.Response(
            200, json=data if request.url.params["$offset"] == "0" else []
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        load_live(database_path=db, batch_id="live-fictional", client=client)
    # Defensive filtering: even legacy/partial observations attached to failed or
    # running audits must never reach staging. These are deliberately invalid landing states.
    with duckdb.connect(str(db)) as con:
        for state in ["failed", "running"]:
            con.execute(
                f"""INSERT INTO landing.ingestion_batch BY NAME
                SELECT * REPLACE ('{state}-fixture' AS batch_id, '{state}' AS status)
                FROM landing.ingestion_batch WHERE batch_id=?""",
                [synthetic_batches()[0]["batch_id"]],
            )
            con.execute(
                f"""INSERT INTO landing.request_snapshots
                SELECT * REPLACE ('{state}-fixture' AS batch_id)
                FROM landing.request_snapshots WHERE batch_id=?""",
                [synthetic_batches()[0]["batch_id"]],
            )
    project = folder / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", "profiles.yml", ".user.yml"),
    )
    profiles = folder / "profiles"
    profiles.mkdir()
    (profiles / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "nyc_311_requests": {
                    "target": "test",
                    "outputs": {
                        "test": {
                            "type": "duckdb",
                            "path": str(db),
                            "schema": "test_default",
                            "threads": 2,
                        }
                    },
                }
            }
        )
    )
    env = os.environ | {"DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    result = subprocess.run(
        [
            str(Path(os.sys.executable).parent / "dbt"),
            "--no-send-anonymous-usage-stats",
            "--no-use-colors",
            "--log-path",
            str(folder / "logs"),
            "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(profiles),
            "--select",
            "stg_311_request_snapshots",
            "--target-path",
            str(folder / "target"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return db


def get_row(db, key):
    with duckdb.connect(str(db), read_only=True) as con:
        con.execute("SET TimeZone='UTC'")
        cursor = con.execute(
            "SELECT * FROM staging.stg_311_request_snapshots "
            "WHERE batch_id='edge-cases' AND unique_key=?",
            [key],
        )
        columns = [column[0] for column in cursor.description]
        return dict(zip(columns, cursor.fetchone(), strict=True))


def test_success_filter_grain_and_history(warehouse):
    with duckdb.connect(str(warehouse), read_only=True) as con:
        expected = con.sql("""SELECT count(*) FROM landing.request_snapshots r
            JOIN landing.ingestion_batch b USING(batch_id) WHERE b.status='success'""").fetchone()[
            0
        ]
        assert (
            con.sql(
                "SELECT count(*) FROM staging.stg_311_request_snapshots"
            ).fetchone()[0]
            == expected
        )
        assert expected == 9 + len(edge_records()) + 1
        assert (
            con.sql(
                "SELECT count(*) FROM staging.stg_311_request_snapshots WHERE batch_id IN ('failed-fixture','running-fixture')"
            ).fetchone()[0]
            == 0
        )
        assert con.sql(
            "SELECT status FROM staging.stg_311_request_snapshots WHERE unique_key='DEMO-001' ORDER BY snapshot_at_utc"
        ).fetchall() == [("Open",), ("Closed",), ("Open",)]
        assert con.sql(
            "SELECT DISTINCT mode FROM staging.stg_311_request_snapshots ORDER BY mode"
        ).fetchall() == [("live",), ("synthetic",)]
        assert con.sql(
            "SELECT table_type FROM information_schema.tables WHERE table_schema='staging' AND table_name='stg_311_request_snapshots'"
        ).fetchone() == ("VIEW",)


@pytest.mark.parametrize("key,hour", [("winter", 17), ("summer", 16)])
def test_explicit_nyc_utc_conversion(warehouse, key, hour):
    row = get_row(warehouse, key)
    assert row["created_at_nyc"].hour == 12
    assert row["created_at_nyc"].tzinfo is None
    assert row["created_at_utc"].hour == hour
    assert row["created_at_utc"].utcoffset().total_seconds() == 0
    assert row["created_date_time_status"] == "valid"
    assert row["has_date_quality_issue"] is False
    assert row["created_date_parse_failed"] is False


@pytest.mark.parametrize(
    "key", ["malformed", "invalid_calendar", "invalid_hour", "offset", "date_only"]
)
def test_malformed_dates_are_preserved_and_flagged(warehouse, key):
    row = get_row(warehouse, key)
    original = next(r for r in edge_records() if r["unique_key"] == key)
    assert row["created_date"] == original["created_date"]
    assert row["created_at_nyc"] is row["created_at_utc"] is None
    assert row["created_date_parse_failed"] is True
    assert row["created_date_time_status"] == "invalid_format"
    assert row["has_date_quality_issue"] is True
    assert row["is_lifecycle_evaluable"] is False
    assert row["closed_before_created"] is None


@pytest.mark.parametrize("key", ["missing", "blank"])
def test_missing_dates_are_distinct_from_parse_failure(warehouse, key):
    row = get_row(warehouse, key)
    assert row["created_date_time_status"] == "missing"
    assert row["created_date_parse_failed"] is False
    assert row["has_missing_required_fields"] is True
    assert row["created_at_utc"] is None


@pytest.mark.parametrize(
    "key,status",
    [("ambiguous", "ambiguous_local_time"), ("nonexistent", "nonexistent_local_time")],
)
def test_dst_gap_and_fold_are_never_guessed(warehouse, key, status):
    row = get_row(warehouse, key)
    assert row["created_at_nyc"] is not None
    assert row["created_at_utc"] is None
    assert row["created_date_time_status"] == status
    assert row["created_date_parse_failed"] is False
    assert row["has_date_quality_issue"] is True
    assert row["closed_before_created"] is None


def test_valid_dst_boundary_intervals_and_negative_lifecycle(warehouse):
    spring = get_row(warehouse, "spring_span")
    fall = get_row(warehouse, "fall_span")
    assert (spring["closed_at_utc"] - spring["created_at_utc"]).total_seconds() == 120
    assert (fall["closed_at_utc"] - fall["created_at_utc"]).total_seconds() == 10800
    assert spring["closed_before_created"] is fall["closed_before_created"] is False
    assert get_row(warehouse, "negative")["closed_before_created"] is True


def test_all_four_dates_have_independent_quality_flags(warehouse):
    row = get_row(warehouse, "optional_issues")
    assert row["created_date_time_status"] == "valid"
    assert row["closed_date_time_status"] == "invalid_format"
    assert row["due_date_time_status"] == "nonexistent_local_time"
    assert row["resolution_action_updated_date_time_status"] == "ambiguous_local_time"
    assert row["closed_date_parse_failed"] is True
    assert row["due_at_nyc"] is not None and row["due_at_utc"] is None
    assert row["resolution_action_updated_at_utc"] is None
    row = get_row(warehouse, "optional_blank")
    assert row["closed_date"] == "   " and row["due_date"] == ""
    assert row["closed_date_time_status"] == row["due_date_time_status"] == "missing"
    assert row["has_date_quality_issue"] is False
    assert row["has_missing_required_fields"] is False


def test_text_keys_zip_and_fractional_precision(warehouse):
    row = get_row(warehouse, "00001")
    assert row["request_bk"] == row["unique_key"] == "00001"
    assert row["incident_zip"] == "00123"
    assert row["agency_bk"] == "DEP"
    assert row["closed_before_created"] is None
    row = get_row(warehouse, "whitespace")
    assert row["created_date"].startswith(" ") and row["created_date"].endswith(" ")
    assert (
        row["created_at_nyc"].microsecond == row["created_at_utc"].microsecond == 123456
    )
    assert (
        get_row(warehouse, "missing_attributes")["has_missing_required_fields"] is True
    )


def test_utc_instants_are_session_timezone_independent(warehouse):
    with duckdb.connect(str(warehouse), read_only=True) as con:
        results = []
        for zone in ["UTC", "Europe/Riga", "America/Los_Angeles"]:
            con.execute(f"SET TimeZone='{zone}'")
            results.append(
                con.sql(
                    "SELECT created_at_nyc, created_at_utc FROM staging.stg_311_request_snapshots WHERE batch_id='edge-cases' AND unique_key='winter'"
                ).fetchone()
            )
        assert results[0] == results[1] == results[2]
