"""record where each AI call came from, how its run ended, and its configuration

Revision ID: b4d8f2a6c1e9
Revises: a3c9e1f7b2d4
Create Date: 2026-11-15 10:00:00.000000

Calls recorded before this are matched to the audit event their analysis wrote in the same
transaction (same document, within a few seconds): that event says whether it was ready or
failed, and for a ready one, the configuration. Comparisons are known by their feature. A
call that matches nothing -- a Lab run, or the first half of an analysis that stopped to
wait -- keeps a null source and counts as "other" on the cost page.
"""

import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "b4d8f2a6c1e9"
down_revision: str | Sequence[str] | None = "a3c9e1f7b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("source", "outcome", "configuration_id", "configuration_name")
MATCH_SECONDS = 10


def _when(value: object) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def upgrade() -> None:
    with op.batch_alter_table("ai_call") as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.String(), nullable=True))

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE ai_call SET source = 'comparison' WHERE feature = 'comparison'"))
    runs = bind.execute(
        sa.text(
            "SELECT run_id, user_id, document_id, MIN(occurred_at) FROM ai_call "
            "WHERE feature != 'comparison' AND document_id IS NOT NULL GROUP BY run_id"
        )
    ).all()
    configurations: dict[tuple[str, str], str] = {
        (row[0], row[1]): row[2]
        for row in bind.execute(sa.text("SELECT user_id, name, id FROM ai_configuration")).all()
    }
    for run_id, user_id, document_id, occurred_at in runs:
        when = _when(occurred_at)
        events = bind.execute(
            sa.text(
                "SELECT occurred_at, details FROM audit_event "
                "WHERE document_id = :document AND action = 'document.analyzed'"
            ),
            {"document": document_id},
        ).all()
        nearest = min(
            events,
            key=lambda event: abs((_when(event[0]) - when).total_seconds()),
            default=None,
        )
        if nearest is None or abs((_when(nearest[0]) - when).total_seconds()) > MATCH_SECONDS:
            continue
        details = nearest[1] if isinstance(nearest[1], dict) else json.loads(nearest[1] or "{}")
        outcome = details.get("outcome")
        name = details.get("configuration")
        bind.execute(
            sa.text(
                "UPDATE ai_call SET source = 'analysis', outcome = :outcome, "
                "configuration_name = :name, configuration_id = :id WHERE run_id = :run"
            ),
            {
                "outcome": outcome if outcome in ("ready", "failed") else None,
                "name": name if isinstance(name, str) else None,
                "id": configurations.get((user_id, name)) if isinstance(name, str) else None,
                "run": run_id,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_call") as batch:
        for name in reversed(COLUMNS):
            batch.drop_column(name)
