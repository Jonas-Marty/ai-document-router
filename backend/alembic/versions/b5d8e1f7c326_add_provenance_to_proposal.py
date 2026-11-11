"""add provenance to proposal

Revision ID: b5d8e1f7c326
Revises: a3f6d2b8c914
Create Date: 2026-11-11 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5d8e1f7c326"
down_revision: str | Sequence[str] | None = "a3f6d2b8c914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("proposal") as batch:
        batch.add_column(sa.Column("endpoint_name", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("chosen_by_user", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("fallback_from", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("proposal") as batch:
        batch.drop_column("fallback_from")
        batch.drop_column("chosen_by_user")
        batch.drop_column("endpoint_name")
