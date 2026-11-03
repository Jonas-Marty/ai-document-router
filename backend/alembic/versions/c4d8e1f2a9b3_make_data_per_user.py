"""make data per user

Revision ID: c4d8e1f2a9b3
Revises: 9b1f3c7d2a10
Create Date: 2026-11-03 12:00:00.000000

Every row that belonged to "the instance" now belongs to a user. Existing data goes to the
admin -- the account that has been using this as a single-user tool -- and so does the
Nextcloud connection that used to come from WEBDAV_BASE_URL / WEBDAV_USERNAME /
WEBDAV_PASSWORD, read from the environment here once and encrypted like the AI keys.

An instance nobody ever signed in to has nothing worth keeping: settings could not be saved
and endpoints could not be added without a session. Its rows are dropped rather than left
without an owner, and the first account starts from a clean slate.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from cryptography.fernet import Fernet

from alembic import op

revision: str = "c4d8e1f2a9b3"
down_revision: str | Sequence[str] | None = "9b1f3c7d2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNED = ("proposal", "history_entry", "document", "ai_task_step", "ai_endpoint", "app_settings")


def _owner(connection: sa.Connection) -> str | None:
    row = connection.execute(
        sa.text('SELECT id FROM "user" ORDER BY is_admin DESC, created_at ASC LIMIT 1')
    ).first()
    return row[0] if row else None


def upgrade() -> None:
    connection = op.get_bind()
    owner = _owner(connection)

    if owner is None:
        for table in _OWNED:
            connection.execute(sa.text(f"DELETE FROM {table}"))

    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(sa.Column("user_id", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("webdav_base_url", sa.String(), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("webdav_username", sa.String(), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("webdav_password_encrypted", sa.LargeBinary(), nullable=True))
    for table in ("document", "ai_endpoint", "ai_task_step", "audit_event"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("user_id", sa.String(), nullable=True))

    if owner is not None:
        for table in ("app_settings", "document", "ai_endpoint", "ai_task_step"):
            connection.execute(sa.text(f"UPDATE {table} SET user_id = :owner"), {"owner": owner})
        connection.execute(
            sa.text("UPDATE audit_event SET user_id = COALESCE(actor_user_id, :owner)"),
            {"owner": owner},
        )
        _seed_connection_from_environment(connection, owner)

    with op.batch_alter_table("app_settings") as batch:
        batch.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch.create_index("ix_app_settings_user_id", ["user_id"], unique=True)
        batch.create_foreign_key("fk_app_settings_user_id", "user", ["user_id"], ["id"])

    with op.batch_alter_table("document") as batch:
        batch.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch.drop_index("ix_document_webdav_path")
        batch.create_index("ix_document_webdav_path", ["webdav_path"], unique=False)
        batch.create_index("ix_document_user_id", ["user_id"], unique=False)
        batch.create_unique_constraint("uq_document_user_path", ["user_id", "webdav_path"])
        batch.create_foreign_key("fk_document_user_id", "user", ["user_id"], ["id"])

    with op.batch_alter_table("ai_endpoint") as batch:
        batch.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch.drop_index("ix_ai_endpoint_name")
        batch.create_index("ix_ai_endpoint_name", ["name"], unique=False)
        batch.create_index("ix_ai_endpoint_user_id", ["user_id"], unique=False)
        batch.create_unique_constraint("uq_ai_endpoint_user_name", ["user_id", "name"])
        batch.create_foreign_key("fk_ai_endpoint_user_id", "user", ["user_id"], ["id"])

    with op.batch_alter_table("ai_task_step") as batch:
        batch.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch.create_index("ix_ai_task_step_user_id", ["user_id"], unique=False)
        batch.create_foreign_key("fk_ai_task_step_user_id", "user", ["user_id"], ["id"])

    with op.batch_alter_table("audit_event") as batch:
        batch.create_index("ix_audit_event_user_id", ["user_id"], unique=False)


def _seed_connection_from_environment(connection: sa.Connection, owner: str) -> None:
    """The old instance-wide Nextcloud login becomes the admin's own, so filing keeps working
    across the deploy. Skipped when the variables are gone or SECRET_KEY cannot encrypt.

    WEBDAV_WATCH_FOLDER too, when the watch folder has not been set in Settings yet: the
    previous revision moved it there empty, and this is the last chance to read the old value.
    """
    watch_folder = os.environ.get("WEBDAV_WATCH_FOLDER", "").strip()
    if watch_folder.startswith("/"):
        connection.execute(
            sa.text(
                "UPDATE app_settings SET watch_folder_path = :watch "
                "WHERE user_id = :owner AND watch_folder_path = ''"
            ),
            {"watch": watch_folder.rstrip("/") or "/", "owner": owner},
        )

    base_url = os.environ.get("WEBDAV_BASE_URL", "").strip()
    username = os.environ.get("WEBDAV_USERNAME", "").strip()
    password = os.environ.get("WEBDAV_PASSWORD", "")
    secret_key = os.environ.get("SECRET_KEY", "")
    if not (base_url and username and password and secret_key):
        return
    try:
        encrypted = Fernet(secret_key.encode()).encrypt(password.encode())
    except ValueError:
        return
    connection.execute(
        sa.text(
            "UPDATE app_settings SET webdav_base_url = :url, webdav_username = :username, "
            "webdav_password_encrypted = :password WHERE user_id = :owner"
        ),
        {"url": base_url, "username": username, "password": encrypted, "owner": owner},
    )


def downgrade() -> None:
    with op.batch_alter_table("audit_event") as batch:
        batch.drop_index("ix_audit_event_user_id")
        batch.drop_column("user_id")
    with op.batch_alter_table("ai_task_step") as batch:
        batch.drop_constraint("fk_ai_task_step_user_id", type_="foreignkey")
        batch.drop_index("ix_ai_task_step_user_id")
        batch.drop_column("user_id")
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.drop_constraint("fk_ai_endpoint_user_id", type_="foreignkey")
        batch.drop_constraint("uq_ai_endpoint_user_name", type_="unique")
        batch.drop_index("ix_ai_endpoint_user_id")
        batch.drop_index("ix_ai_endpoint_name")
        batch.create_index("ix_ai_endpoint_name", ["name"], unique=True)
        batch.drop_column("user_id")
    with op.batch_alter_table("document") as batch:
        batch.drop_constraint("fk_document_user_id", type_="foreignkey")
        batch.drop_constraint("uq_document_user_path", type_="unique")
        batch.drop_index("ix_document_user_id")
        batch.drop_index("ix_document_webdav_path")
        batch.create_index("ix_document_webdav_path", ["webdav_path"], unique=True)
        batch.drop_column("user_id")
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_constraint("fk_app_settings_user_id", type_="foreignkey")
        batch.drop_index("ix_app_settings_user_id")
        batch.drop_column("webdav_password_encrypted")
        batch.drop_column("webdav_username")
        batch.drop_column("webdav_base_url")
        batch.drop_column("user_id")
