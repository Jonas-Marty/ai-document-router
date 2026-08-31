"""add prompt text to proposal

Revision ID: 12e7dc714309
Revises: 63bbab2daa20
Create Date: 2026-08-31 18:17:54.075660

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "12e7dc714309"
down_revision: str | Sequence[str] | None = "63bbab2daa20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("proposal", sa.Column("prompt_text", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("proposal", "prompt_text")
