"""A shared dbt cache entry is not proof that another transaction can see a table."""

import shutil
from pathlib import Path

import duckdb
import pytest
from test_raw_vault import execute_dbt

from ingestion.landing import load_synthetic

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["agency", "problem"])
def test_cached_uncommitted_relation_does_not_break_history_guard(tmp_path, kind):
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / "nyc_311_requests",
        project,
        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"),
    )
    db = tmp_path / "warehouse.db"
    load_synthetic(db)
    (project / "macros/seed_invisible_relation.sql").write_text(
        """
{% macro seed_invisible_relation() %}
    {% if execute %}
        {% set relation = api.Relation.create(database=this.database,
            schema='raw_vault', identifier='sat_lnk_request_KIND_effectivity', type='table') %}
        {% do adapter.cache_added(relation) %}
        {% set cached = adapter.get_relation(database=this.database, schema='raw_vault',
            identifier='sat_lnk_request_KIND_effectivity') %}
        {% if cached is none %}
            {{ exceptions.raise_compiler_error('Regression setup failed to seed cache') }}
        {% endif %}
    {% endif %}
    select 1 as cache_seeded
{% endmacro %}
""".replace("KIND", kind)
    )
    (project / "models/visibility_probe.sql").write_text("""
{{ config(materialized='view',schema='business_vault',pre_hook=[
    '{{ seed_invisible_relation() }}','{{ dv_effectivity_preflight() }}']) }}
-- depends_on: {{ ref('int_request_agency_effectivity_events') }}
-- depends_on: {{ ref('int_request_problem_effectivity_events') }}
select 1 as guard_passed
""")
    result = execute_dbt(
        project,
        db,
        tmp_path / "profile",
        selectors=(
            "+lnk_request_agency",
            "+lnk_request_problem",
            "+int_request_agency_effectivity_events",
            "+int_request_problem_effectivity_events",
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = execute_dbt(
        project,
        db,
        tmp_path / "profile",
        selectors=("visibility_probe",),
        command="run",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.sql("SELECT * FROM business_vault.visibility_probe").fetchall() == [
            (1,)
        ]
        assert con.sql(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='raw_vault' AND table_name LIKE 'sat_lnk_request_%_effectivity'"
        ).fetchone() == (0,)
