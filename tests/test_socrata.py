"""Network-free live adapter tests, including failed extraction publication guards."""

from datetime import date

import duckdb
import httpx
import pytest

from ingestion import landing, socrata
from ingestion.capture import LiveCapture
from ingestion.landing import SOURCE_FIELDS, BatchConflictError
from ingestion.socrata import (
    Cohort,
    LiveExtractionError,
    SocrataAPI,
    inspect_cohort,
    load_live,
)


def metadata():
    return {
        "id": "erm2-nwe9",
        "columns": [
            {
                "fieldName": field,
                "dataTypeName": "calendar_date"
                if field in socrata.DATE_FIELDS
                else "text",
            }
            for field in SOURCE_FIELDS
        ],
    }


def rows(n=3):
    return [
        {
            "unique_key": f"{i:03}",
            "created_date": "2026-01-01T00:00:00",
            "agency": "DEP",
            "status": "Open",
            "incident_zip": "00123",
        }
        for i in range(n)
    ]


class FakeSource:
    def __init__(self, records=None):
        self.records = rows() if records is None else records
        self.counts = [len(self.records), len(self.records)]
        self.metadata = metadata()
        self.page_override = None
        self.requests = []
        self.count_calls = 0
        self.page_calls = 0

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == "data.cityofnewyork.us"
        if request.url.path.startswith("/api/views/"):
            return httpx.Response(200, json=self.metadata)
        params = request.url.params
        assert "created_date >= '2026-01-01T00:00:00'" in params["$where"]
        assert "created_date < '2026-07-01T00:00:00'" in params["$where"]
        if params["$select"].startswith("count("):
            value = self.counts[min(self.count_calls, len(self.counts) - 1)]
            self.count_calls += 1
            return httpx.Response(200, json=[{"row_count": str(value)}])
        self.page_calls += 1
        assert params["$order"] == "unique_key ASC"
        assert params["$select"].split(",") == list(SOURCE_FIELDS)
        offset, limit = int(params["$offset"]), int(params["$limit"])
        data = (
            self.page_override(offset, limit)
            if self.page_override
            else self.records[offset : offset + limit]
        )
        return httpx.Response(200, json=data)


def audit(db):
    with duckdb.connect(str(db)) as con:
        return con.sql(
            "SELECT status, fetched_rows, accepted_rows, rejected_rows, page_count, "
            "expected_rows_before, expected_rows_after, failure_message "
            "FROM landing.ingestion_batch WHERE mode='live'"
        ).fetchall()


def snapshots(db):
    with duckdb.connect(str(db)) as con:
        return con.sql(
            "SELECT count(*) FROM landing.request_snapshots WHERE record_source='NYC311:erm2-nwe9'"
        ).fetchone()[0]


def test_complete_extract_and_existing_synthetic_history(tmp_path):
    db = tmp_path / "warehouse.db"
    landing.load_synthetic(db)
    source = FakeSource()
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        result = load_live(
            database_path=db, page_size=2, batch_id="live-test", client=client
        )
    assert result.accepted_rows == 3
    assert audit(db) == [("success", 3, 3, 0, 3, 3, 3, None)]
    with duckdb.connect(str(db)) as con:
        assert (
            con.sql("SELECT count(*) FROM landing.request_snapshots").fetchone()[0]
            == 12
        )
        assert (
            con.sql(
                "SELECT count(*) FROM landing.request_snapshots WHERE snapshot_at_utc IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert con.sql(
            "SELECT incident_zip, closed_date FROM landing.request_snapshots WHERE unique_key='000'"
        ).fetchone() == ("00123", None)
        start, capture, end = con.sql(
            "SELECT started_at_utc,snapshot_at_utc,finished_at_utc FROM landing.ingestion_batch WHERE mode='live'"
        ).fetchone()
        assert start <= capture <= end


def test_existing_batch_id_fails_before_network(tmp_path):
    db = tmp_path / "warehouse.db"
    source = FakeSource()
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        load_live(database_path=db, batch_id="same", client=client)
        before = len(source.requests)
        with pytest.raises(BatchConflictError):
            load_live(database_path=db, batch_id="same", client=client)
        assert len(source.requests) == before
    assert len(audit(db)) == 1
    assert snapshots(db) == 3


@pytest.mark.parametrize("problem", ["missing", "type", "identity", "malformed"])
def test_schema_drift_is_audited_before_data_fetch(tmp_path, problem):
    db = tmp_path / "warehouse.db"
    source = FakeSource()
    if problem == "missing":
        source.metadata["columns"].pop()
    elif problem == "type":
        source.metadata["columns"][0]["dataTypeName"] = "number"
    elif problem == "identity":
        source.metadata["id"] = "different"
    else:
        source.metadata = []
    with (
        httpx.Client(transport=httpx.MockTransport(source)) as client,
        pytest.raises(LiveExtractionError),
    ):
        load_live(database_path=db, client=client)
    assert source.count_calls == source.page_calls == 0
    assert audit(db)[0][0] == "failed"
    assert snapshots(db) == 0


@pytest.mark.parametrize(
    "problem",
    [
        "changed_count",
        "short_extract",
        "extra_rows",
        "duplicate",
        "invalid_key",
        "nontext",
        "unexpected_field",
        "wrong_shape",
    ],
)
def test_incomplete_or_invalid_pages_never_publish(tmp_path, problem):
    db = tmp_path / "warehouse.db"
    source = FakeSource()
    if problem == "changed_count":
        source.counts = [3, 4]
    elif problem == "short_extract":
        source.counts = [4, 4]
    elif problem == "extra_rows":
        source.counts = [2, 2]
    elif problem == "duplicate":
        source.records[2] = source.records[0].copy()
    elif problem == "invalid_key":
        source.records[0]["unique_key"] = []
    elif problem == "nontext":
        source.records[0]["incident_zip"] = 123
    elif problem == "unexpected_field":
        source.records[0]["incident_address"] = "excluded"
    else:
        source.page_override = lambda offset, limit: {"error": "bad"}
    with (
        httpx.Client(transport=httpx.MockTransport(source)) as client,
        pytest.raises(LiveExtractionError),
    ):
        load_live(database_path=db, page_size=2, client=client)
    row = audit(db)[0]
    assert row[0] == "failed" and row[2] == 0
    assert snapshots(db) == 0


def test_guard_refuses_large_cohort_without_truncating(tmp_path):
    source = FakeSource()
    db = tmp_path / "warehouse.db"
    with (
        httpx.Client(transport=httpx.MockTransport(source)) as client,
        pytest.raises(LiveExtractionError, match="above max_rows"),
    ):
        load_live(database_path=db, max_rows=2, client=client)
    assert source.page_calls == 0
    assert audit(db)[0][:3] == ("failed", 0, 0)


def test_short_server_pages_still_fetch_all(tmp_path):
    source = FakeSource()
    source.page_override = lambda offset, limit: source.records[offset : offset + 1]
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        assert (
            load_live(
                database_path=tmp_path / "warehouse.db", page_size=2, client=client
            ).accepted_rows
            == 3
        )
    assert source.page_calls == 4


def test_empty_cohort_reconciles(tmp_path):
    source = FakeSource([])
    db = tmp_path / "warehouse.db"
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        assert load_live(database_path=db, client=client).accepted_rows == 0
    assert audit(db)[0] == ("success", 0, 0, 0, 1, 0, 0, None)


@pytest.mark.parametrize("failure", ["429", "503", "timeout"])
def test_retry_then_success(failure):
    attempts = []
    waits = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("secret-request-token", request=request)
            return httpx.Response(int(failure), headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert SocrataAPI(client, waits.append).get_json(socrata.API_ENDPOINT) == {
            "ok": True
        }
    assert len(attempts) == 2 and len(waits) == 1


@pytest.mark.parametrize(
    "code,attempt_count", [(400, 1), (403, 1), (302, 1), (429, 3), (503, 3)]
)
def test_http_errors_are_bounded_and_redacted(code, attempt_count):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(code, json={"message": "sensitive-response"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(LiveExtractionError) as exc,
    ):
        SocrataAPI(client, lambda delay: None).get_json(socrata.API_ENDPOINT)
    assert "sensitive" not in str(exc.value)
    assert len(attempts) == attempt_count


@pytest.mark.parametrize("retry_after", ["999", "nan", "-1", "invalid"])
def test_unreasonable_retry_after_stops(retry_after):
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": retry_after})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(LiveExtractionError),
    ):
        SocrataAPI(client, lambda _: pytest.fail("must not sleep")).get_json(
            socrata.API_ENDPOINT
        )


def test_retry_after_http_date():
    def handler(request):
        return httpx.Response(
            429, headers={"Retry-After": "Wed, 01 Jan 2020 00:00:00 GMT"}
        )

    waits = []
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(LiveExtractionError),
    ):
        SocrataAPI(client, waits.append).get_json(socrata.API_ENDPOINT)
    assert waits == [0, 0]


def test_failed_page_retains_progress_and_safe_failure(tmp_path):
    source = FakeSource()

    def handler(request):
        if request.url.params.get("$offset") == "2":
            return httpx.Response(403, text="sensitive-response")
        return source(request)

    db = tmp_path / "warehouse.db"
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(LiveExtractionError),
    ):
        load_live(database_path=db, page_size=2, client=client)
    assert audit(db)[0][:5] == ("failed", 2, 0, 0, 1)
    assert "sensitive" not in audit(db)[0][-1]
    assert snapshots(db) == 0


def test_token_header_and_count_only_has_no_database(tmp_path, monkeypatch):
    source = FakeSource()
    original = httpx.Client

    def factory(**kwargs):
        assert kwargs["headers"]["X-App-Token"] == "test-token-never-log"
        assert kwargs["timeout"].connect == 10
        return original(**kwargs, transport=httpx.MockTransport(source))

    monkeypatch.setenv("SOCRATA_APP_TOKEN", "test-token-never-log")
    monkeypatch.setattr(httpx, "Client", factory)
    monkeypatch.setattr(socrata, "PROJECT_ROOT", tmp_path)
    assert inspect_cohort() == 3
    assert source.page_calls == 0
    assert not list(tmp_path.iterdir())
    assert all(
        request.headers["X-App-Token"] == "test-token-never-log"
        for request in source.requests
    )


def test_count_json_and_network_failures_are_safe():
    for response in [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json=[{"row_count": "-1"}]),
    ]:
        with (
            httpx.Client(
                transport=httpx.MockTransport(lambda req, result=response: result)
            ) as client,
            pytest.raises(LiveExtractionError),
        ):
            SocrataAPI(client).count(Cohort())

    def timeout(request):
        raise httpx.ConnectError("sensitive-token", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(timeout)) as client,
        pytest.raises(LiveExtractionError, match="three attempts") as exc,
    ):
        SocrataAPI(client, lambda _: None).get_json(socrata.API_ENDPOINT)
    assert "sensitive" not in str(exc.value)


def test_atomic_writer_shared_and_rolls_back_live(tmp_path, monkeypatch):
    source = FakeSource()
    original = landing._insert_rows

    def fail(con, batch_id, snapshot, record_source, records):
        original(con, batch_id, snapshot, record_source, records[:1])
        raise RuntimeError("injected")

    monkeypatch.setattr(landing, "_insert_rows", fail)
    db = tmp_path / "warehouse.db"
    with (
        httpx.Client(transport=httpx.MockTransport(source)) as client,
        pytest.raises(RuntimeError),
    ):
        load_live(database_path=db, client=client)
    assert audit(db)[0][0] == "failed"
    assert snapshots(db) == 0


def test_running_audit_exists_before_first_request(tmp_path):
    db = tmp_path / "warehouse.db"
    with LiveCapture("running-id", "cohort", db) as capture:
        assert capture.con.sql(
            "SELECT status FROM landing.ingestion_batch"
        ).fetchone() == ("running",)
    assert audit(db)[0][0] == "failed"


@pytest.mark.parametrize(
    "options", [{"page_size": 0}, {"page_size": 5001}, {"max_rows": 0}]
)
def test_invalid_config_does_not_create_database(tmp_path, options):
    db = tmp_path / "warehouse.db"
    with pytest.raises(ValueError):
        load_live(database_path=db, **options)
    assert not db.exists()


def test_cohort_validates_dates_codes_and_canonicalizes():
    assert Cohort(agencies=("DSNY", "DEP", "DEP")).agencies == ("DEP", "DSNY")
    for args in [
        (date(2026, 2, 1), date(2026, 1, 1), ("DEP",)),
        (date(2026, 1, 1), date(2026, 2, 1), ("DEP' OR 1=1",)),
    ]:
        with pytest.raises(ValueError):
            Cohort(*args)


def test_live_cli_count_only(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["landing", "--mode", "live", "--count-only"])
    monkeypatch.setattr(socrata, "inspect_cohort", lambda cohort: 123)
    monkeypatch.setattr(
        socrata, "load_live", lambda *a, **k: pytest.fail("Must not load")
    )
    landing.main()
    assert "Matching rows: 123" in capsys.readouterr().out


def test_live_cli_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "landing",
            "--mode",
            "live",
            "--page-size",
            "100",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-01-02",
        ],
    )

    def fake_load(cohort, **options):
        assert cohort.end_date == date(2026, 1, 2)
        assert options["page_size"] == 100
        return landing.BatchResult("live-cli", 2, False)

    monkeypatch.setattr(socrata, "load_live", fake_load)
    landing.main()
    assert "live-cli: loaded, 2 rows" in capsys.readouterr().out


def test_live_cli_failure_is_nonzero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["landing", "--mode", "live", "--count-only"])

    def fail(cohort):
        raise LiveExtractionError("Socrata HTTP 403; batch not published")

    monkeypatch.setattr(socrata, "inspect_cohort", fail)
    with pytest.raises(SystemExit) as exc:
        landing.main()
    assert exc.value.code == 1
    assert "HTTP 403" in capsys.readouterr().err
