"""archive knowledge: name template, filing rules, known senders and accounts

Revision ID: b7e2c4a9d1f6
Revises: a3d6e1f8c2b4
Create Date: 2026-11-13 19:00:00.000000

Nothing to backfill: senders and accounts are learned from approvals from now on (SPEC
6.3c), and every user starts with the template names had until now.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e2c4a9d1f6"
down_revision: str | Sequence[str] | None = "a3d6e1f8c2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TEMPLATE = '["date", "sender", "account", "title", "period", "number"]'


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(
            sa.Column(
                "name_template",
                sa.JSON(),
                nullable=False,
                server_default=sa.text(f"'{DEFAULT_TEMPLATE}'"),
            )
        )
        batch.add_column(sa.Column("filing_rules", sa.String(), nullable=True))

    op.create_table(
        "known_sender",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("short_name", sa.String(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_known_sender_user_id", "known_sender", ["user_id"])
    op.create_table(
        "known_account",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("sender_id", sa.String(), sa.ForeignKey("known_sender.id"), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_known_account_user_id", "known_account", ["user_id"])
    op.create_index("ix_known_account_sender_id", "known_account", ["sender_id"])


def downgrade() -> None:
    op.drop_index("ix_known_account_sender_id", table_name="known_account")
    op.drop_index("ix_known_account_user_id", table_name="known_account")
    op.drop_table("known_account")
    op.drop_index("ix_known_sender_user_id", table_name="known_sender")
    op.drop_table("known_sender")
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("filing_rules")
        batch.drop_column("name_template")
