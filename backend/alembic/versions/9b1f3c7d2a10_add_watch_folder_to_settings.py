"""add watch folder to settings

Revision ID: 9b1f3c7d2a10
Revises: 3ec8da6900e1
Create Date: 2026-11-03 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9b1f3c7d2a10"
down_revision: str | Sequence[str] | None = "3ec8da6900e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Empty rather than seeded from the old WEBDAV_WATCH_FOLDER: the setting moved to the UI
    # deliberately, and ingest waits until it is set there.
    op.add_column(
        "app_settings",
        sa.Column("watch_folder_path", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "watch_folder_path")
