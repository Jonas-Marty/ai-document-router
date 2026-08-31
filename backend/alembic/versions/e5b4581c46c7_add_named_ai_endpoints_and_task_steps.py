"""add named ai endpoints and task steps

Revision ID: e5b4581c46c7
Revises: 12e7dc714309
Create Date: 2026-08-31 18:51:22.567668

"""

from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "e5b4581c46c7"
down_revision: str | Sequence[str] | None = "12e7dc714309"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_endpoint",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_endpoint_name"), "ai_endpoint", ["name"], unique=True)
    op.create_table(
        "ai_task_step",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task", sa.Enum("extraction", "filing", name="aitask"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("endpoint_id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["endpoint_id"], ["ai_endpoint.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_task_step_endpoint_id"), "ai_task_step", ["endpoint_id"])
    op.create_index(op.f("ix_ai_task_step_task"), "ai_task_step", ["task"])

    op.add_column("document", sa.Column("extracted_markdown", sa.String(), nullable=True))
    op.add_column("document", sa.Column("extraction_model", sa.String(), nullable=True))
    op.add_column("document", sa.Column("extraction_error", sa.String(), nullable=True))

    _carry_over_the_configured_endpoint()


def _carry_over_the_configured_endpoint() -> None:
    """Turn the single configured endpoint into a named one with a filing step.

    Without this an upgrade silently stops filing: the poller reads its model from the task
    chain now, and an empty chain means every document fails until someone opens Settings.
    """
    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT ai_endpoint_url, ai_model_name, ai_api_key_encrypted FROM app_settings")
    ).first()
    if row is None or not row[0]:
        return

    base_url, model_name, api_key = row[0], row[1], row[2]
    endpoint_id = str(uuid4())
    connection.execute(
        sa.text(
            "INSERT INTO ai_endpoint (id, name, base_url, api_key_encrypted, created_at) "
            "VALUES (:id, :name, :base_url, :api_key, :created_at)"
        ),
        {
            "id": endpoint_id,
            "name": urlparse(base_url).hostname or "Default",
            "base_url": base_url,
            "api_key": api_key,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
        },
    )

    if model_name:
        connection.execute(
            sa.text(
                "INSERT INTO ai_task_step (id, task, position, endpoint_id, model_name) "
                "VALUES (:id, 'filing', 0, :endpoint_id, :model_name)"
            ),
            {"id": str(uuid4()), "endpoint_id": endpoint_id, "model_name": model_name},
        )


def downgrade() -> None:
    op.drop_column("document", "extraction_error")
    op.drop_column("document", "extraction_model")
    op.drop_column("document", "extracted_markdown")
    op.drop_index(op.f("ix_ai_task_step_task"), table_name="ai_task_step")
    op.drop_index(op.f("ix_ai_task_step_endpoint_id"), table_name="ai_task_step")
    op.drop_table("ai_task_step")
    op.drop_index(op.f("ix_ai_endpoint_name"), table_name="ai_endpoint")
    op.drop_table("ai_endpoint")
