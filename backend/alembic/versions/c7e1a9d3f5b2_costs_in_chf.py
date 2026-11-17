"""label every cost recorded so far, and every connection, as CHF

Revision ID: c7e1a9d3f5b2
Revises: b4d8f2a6c1e9
Create Date: 2026-11-17 09:00:00.000000

A one-off at the owner's request: the only provider billed so far bills in CHF, but costs
from before currencies were kept have none, and connections started in USD when the setting
was added, so what was recorded since reads USD. Amounts stay as they are -- the prices were
entered in CHF -- only the label changes. Nothing is converted at a rate.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7e1a9d3f5b2"
down_revision: str | Sequence[str] | None = "b4d8f2a6c1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHF = "CHF"


def _relabel(usage: dict[str, object]) -> bool:
    """CHF on each stage with a cost and on the total; False when nothing had a cost."""
    changed = False
    for key in ("extraction", "filing"):
        stage = usage.get(key)
        if isinstance(stage, dict) and stage.get("cost") is not None:
            stage["currency"] = CHF
            changed = True
    # A total over a cost without a currency and one in USD was "mixed" and had no amount;
    # with both now CHF it adds up again.
    costs = [
        stage["cost"]
        for stage in (usage.get("extraction"), usage.get("filing"))
        if isinstance(stage, dict) and isinstance(stage.get("cost"), int | float)
    ]
    if costs:
        usage["cost"] = sum(costs)
        usage["currency"] = CHF
        changed = True
    return changed


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE ai_endpoint SET currency = :chf"), {"chf": CHF})
    for table in ("ai_call", "lab_result"):
        bind.execute(
            sa.text(f"UPDATE {table} SET currency = :chf WHERE cost IS NOT NULL"), {"chf": CHF}
        )
    for document_id, stored in bind.execute(
        sa.text("SELECT id, analysis_usage FROM document WHERE analysis_usage IS NOT NULL")
    ).all():
        usage = stored if isinstance(stored, dict) else json.loads(stored)
        if isinstance(usage, dict) and _relabel(usage):
            bind.execute(
                sa.text("UPDATE document SET analysis_usage = :usage WHERE id = :id"),
                {"usage": json.dumps(usage), "id": document_id},
            )


def downgrade() -> None:
    # What each label was before cannot be told apart afterwards, and nothing depends on it.
    pass
