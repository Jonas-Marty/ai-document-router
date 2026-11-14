"""When a document's latest analysis finished, successfully or not.

Revision ID: deeef6787ae0
Revises: 9b560ca43241
Create Date: 2026-11-14 13:29:33.142156

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "deeef6787ae0"
down_revision: str | Sequence[str] | None = "9b560ca43241"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("document", sa.Column("analysed_at", sa.DateTime(), nullable=True))
    # A document with a proposal was analysed when the proposal was made. Failed analyses
    # before this column kept no time and stay empty.
    op.execute(
        "UPDATE document SET analysed_at = "
        "(SELECT MAX(created_at) FROM proposal WHERE proposal.document_id = document.id)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("document", "analysed_at")
