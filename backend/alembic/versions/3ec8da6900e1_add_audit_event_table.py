"""add audit event table

Revision ID: 3ec8da6900e1
Revises: e39d00952c57
Create Date: 2026-09-11 19:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3ec8da6900e1"
down_revision: str | Sequence[str] | None = "e39d00952c57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_event_action"), "audit_event", ["action"], unique=False)
    op.create_index(
        op.f("ix_audit_event_document_id"), "audit_event", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_event_occurred_at"), "audit_event", ["occurred_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_event_occurred_at"), table_name="audit_event")
    op.drop_index(op.f("ix_audit_event_document_id"), table_name="audit_event")
    op.drop_index(op.f("ix_audit_event_action"), table_name="audit_event")
    op.drop_table("audit_event")
