"""add reply timeout to ai_endpoint

Revision ID: 2435d6064434
Revises: 4dfe231bd0e6
Create Date: 2026-11-03 18:30:00.000000

Every endpoint starts at 120 seconds, up from the 30 that applied to all of them before:
long enough for a local model loading cold, short enough that a stuck one still falls
through to the next step.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2435d6064434"
down_revision: str | Sequence[str] | None = "4dfe231bd0e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.add_column(
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120")
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.drop_column("timeout_seconds")
