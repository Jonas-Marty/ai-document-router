"""add vision model names to settings

Revision ID: 7772b8927127
Revises: 5ef814516744
Create Date: 2026-08-31 06:59:04.907256

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7772b8927127"
down_revision: str | Sequence[str] | None = "5ef814516744"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("app_settings", sa.Column("vision_model_names", sa.JSON(), nullable=True))
    # Backfilled, not left NULL: the column is a `list[str]` on the model, and the existing
    # settings row would otherwise read back as None and raise the first time anything
    # iterated it. Kept nullable so SQLite does not need a table rebuild.
    op.execute("UPDATE app_settings SET vision_model_names = '[]' WHERE vision_model_names IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("app_settings", "vision_model_names")
