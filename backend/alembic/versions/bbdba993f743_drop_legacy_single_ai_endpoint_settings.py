"""drop legacy single ai endpoint settings

Revision ID: bbdba993f743
Revises: e5b4581c46c7
Create Date: 2026-08-31 19:04:04.202270

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bbdba993f743"
down_revision: str | Sequence[str] | None = "e5b4581c46c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the one-endpoint settings. e5b4581c46c7 has already copied them into ai_endpoint,
    and leaving them would be a second place to configure the AI that nothing reads."""
    op.drop_column("app_settings", "ai_endpoint_url")
    op.drop_column("app_settings", "ai_model_name")
    op.drop_column("app_settings", "ai_api_key_encrypted")
    op.drop_column("app_settings", "vision_model_names")


def downgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("vision_model_names", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "app_settings", sa.Column("ai_api_key_encrypted", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "app_settings", sa.Column("ai_model_name", sa.String(), nullable=False, server_default="")
    )
    op.add_column(
        "app_settings", sa.Column("ai_endpoint_url", sa.String(), nullable=False, server_default="")
    )
