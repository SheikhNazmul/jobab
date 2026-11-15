-- Runs once, the first time the Postgres container starts.
-- pgvector powers optional meaning-based product search; the app also runs
-- without it (word search still works).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS sales_agent;
