"""add ai_call usage table

Revision ID: 4dfe231bd0e6
Revises: c4d8e1f2a9b3
Create Date: 2026-11-03 16:57:16.663533

One row per model call from now on. What was spent before is carried over from the audit
log, where every analysis and comparison stored its usage per stage: one row per stage,
with `requests` set to that stage's call count. Those rows know the connection and model by
name only (the stored label is "Connection · model"), have no durations, and cannot say
which calls failed or ran as a fallback -- the figures that did not exist yet stay zero.
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4dfe231bd0e6"
down_revision: str | Sequence[str] | None = "c4d8e1f2a9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEPARATOR = " · "


def upgrade() -> None:
    op.create_table(
        "ai_call",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=True),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("endpoint_id", sa.String(), nullable=True),
        sa.Column("connection", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("without_usage", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("fallback", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("timed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_call_occurred_at"), "ai_call", ["occurred_at"], unique=False)
    op.create_index(op.f("ix_ai_call_run_id"), "ai_call", ["run_id"], unique=False)
    op.create_index(op.f("ix_ai_call_user_id"), "ai_call", ["user_id"], unique=False)
    _carry_over(op.get_bind())


def _carry_over(connection: sa.Connection) -> None:
    endpoints = {
        (user_id, name): endpoint_id
        for endpoint_id, user_id, name in connection.execute(
            sa.text("SELECT id, user_id, name FROM ai_endpoint")
        )
    }
    events = connection.execute(
        sa.text(
            "SELECT id, user_id, occurred_at, document_id, action, details FROM audit_event "
            "WHERE user_id IS NOT NULL "
            "AND action IN ('document.analyzed', 'document.compared')"
        )
    ).all()
    rows = []
    for event_id, user_id, occurred_at, document_id, action, details in events:
        data = json.loads(details) if isinstance(details, str) else (details or {})
        summaries = [data.get("usage")]
        methods = data.get("methods")
        if isinstance(methods, list):
            summaries += [m.get("usage") for m in methods if isinstance(m, dict)]
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            for task in ("extraction", "filing"):
                stage = summary.get(task)
                if not isinstance(stage, dict):
                    continue
                label = str(stage.get("model_label") or "")
                name, _, model = label.partition(_SEPARATOR)
                rows.append(
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": user_id,
                        "occurred_at": occurred_at,
                        "run_id": event_id,
                        "document_id": document_id,
                        "feature": "comparison" if action == "document.compared" else task,
                        "endpoint_id": endpoints.get((user_id, name)),
                        "connection": name or label,
                        "model": model or label,
                        "requests": _int(stage.get("calls")),
                        "prompt_tokens": _int(stage.get("prompt_tokens")),
                        "completion_tokens": _int(stage.get("completion_tokens")),
                        "cached_tokens": 0,
                        "without_usage": _int(stage.get("unreported_calls")),
                        "failed": 0,
                        "fallback": 0,
                        "duration_ms": 0,
                        "timed": 0,
                    }
                )
    if rows:
        op.bulk_insert(sa.table("ai_call", *(sa.column(key) for key in rows[0])), rows)


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_call_user_id"), table_name="ai_call")
    op.drop_index(op.f("ix_ai_call_run_id"), table_name="ai_call")
    op.drop_index(op.f("ix_ai_call_occurred_at"), table_name="ai_call")
    op.drop_table("ai_call")
