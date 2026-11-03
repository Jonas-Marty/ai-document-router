"""add proposal batch size to app_settings

Revision ID: 7c1e9a4b2d50
Revises: 2435d6064434
Create Date: 2026-11-03 20:00:00.000000

Per user, replacing the global POLLER_PROPOSAL_BATCH of 5; everyone starts at 10.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c1e9a4b2d50"
down_revision: str | Sequence[str] | None = "2435d6064434"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(
            sa.Column("proposal_batch_size", sa.Integer(), nullable=False, server_default="10")
        )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("proposal_batch_size")
