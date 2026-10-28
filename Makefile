# Offline demo targets. Run from the repository root.
PYTHON ?= .venv/bin/python
DBT ?= .venv/bin/dbt
DEMO_DATABASE ?= $(CURDIR)/data/demo/nyc_open_data.db

export NYC311_DEMO_PYTHON := $(PYTHON)
export NYC311_DEMO_DBT := $(DBT)
export NYC311_DEMO_DATABASE := $(DEMO_DATABASE)
export NYC311_DASHBOARD_DATABASE := $(DEMO_DATABASE)
export DBT_SEND_ANONYMOUS_USAGE_STATS := false

.PHONY: demo demo-load demo-build demo-dashboard lint test

demo: demo-load
	"$$NYC311_DEMO_DBT" build --project-dir nyc_311_requests --profiles-dir config/demo

demo-load:
	"$$NYC311_DEMO_PYTHON" -m ingestion.landing --mode synthetic --database "$$NYC311_DEMO_DATABASE"

demo-build:
	"$$NYC311_DEMO_DBT" build --project-dir nyc_311_requests --profiles-dir config/demo

demo-dashboard:
	"$$NYC311_DEMO_PYTHON" -m streamlit run dashboard/app.py --server.address 127.0.0.1 --browser.gatherUsageStats false

lint:
	"$$NYC311_DEMO_PYTHON" -m ruff check dashboard ingestion tests scripts

test:
	"$$NYC311_DEMO_PYTHON" -m pytest -q tests
