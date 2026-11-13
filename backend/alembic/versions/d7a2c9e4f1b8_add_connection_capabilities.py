"""add connection capabilities and call cost

Revision ID: d7a2c9e4f1b8
Revises: b5d8e1f7c326
Create Date: 2026-11-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7a2c9e4f1b8"
down_revision: str | Sequence[str] | None = "b5d8e1f7c326"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.add_column(sa.Column("context_limit", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "json_mode",
                sa.Enum("auto", "json_schema", "json_object", "none", name="aijsonmode"),
                nullable=False,
                server_default="auto",
            )
        )
        batch.add_column(sa.Column("price_input_per_million", sa.Float(), nullable=True))
        batch.add_column(sa.Column("price_output_per_million", sa.Float(), nullable=True))
    with op.batch_alter_table("ai_call") as batch:
        batch.add_column(sa.Column("cost", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_call") as batch:
        batch.drop_column("cost")
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.drop_column("price_output_per_million")
        batch.drop_column("price_input_per_million")
        batch.drop_column("json_mode")
        batch.drop_column("context_limit")
