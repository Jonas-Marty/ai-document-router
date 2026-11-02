"""add ai instructions to settings

Revision ID: 20fc23359655
Revises: bbdba993f743
Create Date: 2026-09-11 18:43:18.336963

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20fc23359655"
down_revision: str | Sequence[str] | None = "bbdba993f743"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("filing_instructions", sa.String(), nullable=True))
    op.add_column("app_settings", sa.Column("extraction_instructions", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "extraction_instructions")
    op.drop_column("app_settings", "filing_instructions")
