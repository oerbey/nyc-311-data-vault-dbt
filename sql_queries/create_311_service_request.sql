-- Run through output/jupyter-notebook/run_sql_queries.ipynb.
-- api_service_requests is the registered DataFrame from the API connection notebook.
-- This replaces the exploratory sample table, not a historical Vault table.
CREATE OR REPLACE TABLE "311_service_requests" AS
SELECT *
FROM api_service_requests;
