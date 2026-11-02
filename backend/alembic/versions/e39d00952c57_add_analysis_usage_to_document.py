"""add analysis usage to document

Revision ID: e39d00952c57
Revises: 20fc23359655
Create Date: 2026-09-11 19:02:11.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e39d00952c57"
down_revision: str | Sequence[str] | None = "20fc23359655"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document", sa.Column("analysis_usage", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("document", "analysis_usage")
