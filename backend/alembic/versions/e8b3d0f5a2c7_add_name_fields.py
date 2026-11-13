"""add name fields to proposal and history entry

Revision ID: e8b3d0f5a2c7
Revises: d7a2c9e4f1b8
Create Date: 2026-11-13 14:00:00.000000

Existing proposals keep null fields, which is what marks a free name from before fields
existed (SPEC 7.1a): nothing to backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8b3d0f5a2c7"
down_revision: str | Sequence[str] | None = "d7a2c9e4f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIELDS = ("sender", "account", "title", "period", "number")


def upgrade() -> None:
    with op.batch_alter_table("proposal") as batch:
        for name in FIELDS:
            batch.add_column(sa.Column(name, sa.String(), nullable=True))
    with op.batch_alter_table("history_entry") as batch:
        batch.add_column(sa.Column("name_fields", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("history_entry") as batch:
        batch.drop_column("name_fields")
    with op.batch_alter_table("proposal") as batch:
        for name in reversed(FIELDS):
            batch.drop_column(name)
