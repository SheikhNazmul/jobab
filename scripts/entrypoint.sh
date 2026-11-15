#!/bin/sh
# Container start: bring the database schema up to date, then run the app.
# Set RUN_MIGRATIONS=0 to skip migrations (for a read-only replica, say).
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Running database migrations…"
  alembic upgrade head
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
