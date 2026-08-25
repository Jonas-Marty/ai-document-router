#!/bin/sh
set -eu

# Migrations run here rather than in the FastAPI lifespan so that a schema failure stops the
# container outright instead of leaving a running app serving errors against a stale schema.
# `alembic upgrade head` is idempotent, so a restart with nothing to apply is a no-op.
echo "Applying database migrations..."
alembic upgrade head

# Single worker, deliberately. The APScheduler poller (app/jobs/poller.py) starts in the
# application lifespan, so every additional worker is another poller ingesting the same
# WebDAV watch folder and issuing its own LLM calls for the same documents. This is a
# single-user tool; one worker is also plenty. Scale by making the poller a separate
# process, never by raising this number.
echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
