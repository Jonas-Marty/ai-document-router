"""add a currency to connections and to the costs recorded with them

Revision ID: a3c9e1f7b2d4
Revises: deeef6787ae0
Create Date: 2026-11-14 20:30:00.000000

Connections start in USD, which they can be switched from. Costs already recorded keep a
null currency: what they were billed in was never written down, and guessing it now would
label history with a currency it may not have had.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3c9e1f7b2d4"
down_revision: str | Sequence[str] | None = "deeef6787ae0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.add_column(sa.Column("currency", sa.String(), nullable=False, server_default="USD"))
    with op.batch_alter_table("ai_call") as batch:
        batch.add_column(sa.Column("currency", sa.String(), nullable=True))
    with op.batch_alter_table("lab_result") as batch:
        batch.add_column(sa.Column("currency", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lab_result") as batch:
        batch.drop_column("currency")
    with op.batch_alter_table("ai_call") as batch:
        batch.drop_column("currency")
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.drop_column("currency")
