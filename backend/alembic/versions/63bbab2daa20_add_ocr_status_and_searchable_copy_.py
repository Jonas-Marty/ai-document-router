"""add ocr status and searchable copy setting

Revision ID: 63bbab2daa20
Revises: 7772b8927127
Create Date: 2026-08-31 07:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "63bbab2daa20"
down_revision: str | Sequence[str] | None = "7772b8927127"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("document", sa.Column("ocr_status", sa.String(), nullable=True))
    op.add_column("document", sa.Column("ocr_error", sa.String(), nullable=True))
    op.add_column("app_settings", sa.Column("store_ocr_text", sa.Boolean(), nullable=True))

    # Backfilled rather than left NULL: both columns are non-optional on the model, so an
    # existing row would read back as None and compare unequal to every OcrStatus member.
    # Left nullable so SQLite does not have to rebuild the table.
    op.execute("UPDATE document SET ocr_status = 'not_needed' WHERE ocr_status IS NULL")

    # Documents already in the queue that failed their proposal are the ones this feature
    # exists for -- they are almost all "no text layer found". Marked pending so the poller
    # picks them up on its next tick instead of leaving a backlog that only new scans
    # benefit from. It re-reads each one before spending minutes in ocrmypdf, so the few
    # that failed for some other reason cost a download and nothing more.
    op.execute(
        """
        UPDATE document
        SET ocr_status = 'pending'
        WHERE proposal_status = 'failed'
          AND status IN ('pending', 'skipped')
          AND mime_type = 'application/pdf'
        """
    )

    # On by default, matching the model: someone who has been filing scans wants the ones
    # they file next to be searchable without having to go and find a switch first.
    op.execute("UPDATE app_settings SET store_ocr_text = 1 WHERE store_ocr_text IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("app_settings", "store_ocr_text")
    op.drop_column("document", "ocr_error")
    op.drop_column("document", "ocr_status")
