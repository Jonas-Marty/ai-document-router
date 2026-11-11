"""add rerun override to document

Revision ID: a3f6d2b8c914
Revises: 7c1e9a4b2d50
Create Date: 2026-11-11 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3f6d2b8c914"
down_revision: str | Sequence[str] | None = "7c1e9a4b2d50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document") as batch:
        batch.add_column(sa.Column("rerun_endpoint_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("rerun_model_name", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document") as batch:
        batch.drop_column("rerun_model_name")
        batch.drop_column("rerun_endpoint_id")
