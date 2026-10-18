"""Fictional source observations with fixed identities and capture times."""
from copy import deepcopy
from datetime import UTC, datetime

SYNTHETIC_FILTER = "synthetic demo: 3 fictional requests created in January 2026"


def synthetic_batches():
    """Return fresh records for three daily observations (nine rows total)."""
    base = [
        {
            "unique_key": "DEMO-001", "created_date": "2026-01-10T09:00:00",
            "agency": "DEP", "agency_name": "Department of Environmental Protection",
            "complaint_type": "Synthetic water issue", "status": "Open",
            "borough": "QUEENS", "incident_zip": "00123",
            "community_board": "01 QUEENS", "open_data_channel_type": "ONLINE",
        },
        {
            "unique_key": "DEMO-002", "created_date": "2026-01-11T10:00:00",
            "agency": "DEP", "agency_name": "Department of Environmental Protection",
            "complaint_type": "Synthetic cleanup", "status": "Open",
            "borough": "BROOKLYN", "incident_zip": "11201",
        },
        {
            "unique_key": "DEMO-003", "created_date": "2026-01-12T11:00:00",
            "agency": "DSNY", "agency_name": "Department of Sanitation",
            "complaint_type": "Synthetic collection", "status": "Closed",
            "closed_date": "2026-01-13T12:00:00", "descriptor": "",
            "borough": "BRONX", "incident_zip": None,
        },
    ]
    batches = []
    for day in range(1, 4):
        records = deepcopy(base)
        if day == 2:
            records[0].update(status="Closed", closed_date="2026-02-01T15:00:00")
        if day >= 2:
            records[1].update(agency="DSNY", agency_name="Department of Sanitation")
        batches.append({
            "batch_id": f"synthetic-2026020{day}-v1",
            "snapshot_at_utc": datetime(2026, 2, day, 8, tzinfo=UTC),
            "source_id": "synthetic_311",
            "record_source": "synthetic_311",
            "mode": "synthetic",
            "requested_filter": SYNTHETIC_FILTER,
            "page_count": 0,
            "records": records,
        })
    return batches
