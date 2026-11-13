"""configurations replace task chains; readings stored per setup

Revision ID: f1c4a8b2d9e3
Revises: e8b3d0f5a2c7
Create Date: 2026-11-13 16:00:00.000000

Each user's chains and instructions become configurations (SPEC 6.3a): one per filing step,
the first the default, so a hosted fallback survives as a configuration to pick by hand
rather than vanishing. A configuration past the first reads with the extraction step at its
own position when there was one, and with the text layer otherwise -- which is what the old
chain fell back to when the first machine was off.

Readings already on documents move to the reading table under a setup key no current
configuration produces: they stay visible on the review screen but are not reused, since
nothing records which prompt and parameters made them.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "f1c4a8b2d9e3"
down_revision: str | Sequence[str] | None = "e8b3d0f5a2c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READING_METHOD = sa.Enum("text_layer", "tesseract", "vision", name="readingmethod")
THINKING = ("default", "off", "low", "medium", "high")
MIGRATED_SETUP_KEY = "migrated"


def upgrade() -> None:
    op.create_table(
        "ai_configuration",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("reading_method", READING_METHOD, nullable=False),
        sa.Column("reading_endpoint_id", sa.String(), nullable=True),
        sa.Column("reading_model_name", sa.String(), nullable=True),
        sa.Column("reading_system_prompt", sa.String(), nullable=True),
        sa.Column("reading_instructions", sa.String(), nullable=True),
        sa.Column("reading_temperature", sa.Float(), nullable=False),
        sa.Column("reading_max_tokens", sa.Integer(), nullable=False),
        sa.Column("reading_thinking", sa.Enum(*THINKING, name="aithinking"), nullable=False),
        sa.Column("filing_endpoint_id", sa.String(), nullable=True),
        sa.Column("filing_model_name", sa.String(), nullable=True),
        sa.Column(
            "filing_strategy",
            sa.Enum("one_call", "two_calls", name="filingstrategy"),
            nullable=False,
        ),
        sa.Column("filing_system_prompt", sa.String(), nullable=True),
        sa.Column("folder_system_prompt", sa.String(), nullable=True),
        sa.Column("filing_instructions", sa.String(), nullable=True),
        sa.Column("filing_temperature", sa.Float(), nullable=False),
        sa.Column("filing_max_tokens", sa.Integer(), nullable=False),
        sa.Column("filing_thinking", sa.Enum(*THINKING, name="aithinking"), nullable=False),
        sa.Column("max_examples", sa.Integer(), nullable=False),
        sa.Column("max_folders", sa.Integer(), nullable=False),
        sa.Column("max_text_chars", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["reading_endpoint_id"], ["ai_endpoint.id"]),
        sa.ForeignKeyConstraint(["filing_endpoint_id"], ["ai_endpoint.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_ai_configuration_user_name"),
    )
    op.create_index("ix_ai_configuration_user_id", "ai_configuration", ["user_id"])

    op.create_table(
        "reading",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("setup_key", sa.String(), nullable=False),
        sa.Column("method", READING_METHOD, nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "setup_key", name="uq_reading_document_setup"),
    )
    op.create_index("ix_reading_user_id", "reading", ["user_id"])
    op.create_index("ix_reading_document_id", "reading", ["document_id"])

    with op.batch_alter_table("document") as batch:
        batch.add_column(sa.Column("configuration_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("reading_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("waiting_reason", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_document_configuration_id", "ai_configuration", ["configuration_id"], ["id"]
        )
    with op.batch_alter_table("proposal") as batch:
        batch.add_column(sa.Column("system_prompt", sa.String(), nullable=True))
        batch.add_column(sa.Column("configuration_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("configuration_name", sa.String(), nullable=True))
        batch.add_column(sa.Column("reading_label", sa.String(), nullable=True))

    connection = op.get_bind()
    _configurations_from_chains(connection)
    _readings_from_documents(connection)

    with op.batch_alter_table("proposal") as batch:
        batch.drop_column("fallback_from")
        batch.drop_column("chosen_by_user")
    with op.batch_alter_table("document") as batch:
        batch.drop_column("rerun_model_name")
        batch.drop_column("rerun_endpoint_id")
        batch.drop_column("extraction_error")
        batch.drop_column("extraction_model")
        batch.drop_column("extracted_markdown")
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("extraction_instructions")
        batch.drop_column("filing_instructions")
    op.drop_index("ix_ai_task_step_task", table_name="ai_task_step")
    op.drop_index("ix_ai_task_step_endpoint_id", table_name="ai_task_step")
    op.drop_index("ix_ai_task_step_user_id", table_name="ai_task_step")
    op.drop_table("ai_task_step")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _configurations_from_chains(connection: sa.engine.Connection) -> None:
    steps = connection.execute(
        sa.text(
            "SELECT s.user_id, s.task, s.endpoint_id, s.model_name, e.name "
            "FROM ai_task_step s JOIN ai_endpoint e ON e.id = s.endpoint_id "
            "ORDER BY s.user_id, s.task, s.position"
        )
    ).all()
    chains: dict[str, dict[str, list[tuple[str, str, str]]]] = {}
    for user_id, task, endpoint_id, model_name, endpoint_name in steps:
        chains.setdefault(user_id, {"extraction": [], "filing": []})[task].append(
            (endpoint_id, model_name, endpoint_name)
        )

    instructions = {
        user_id: (filing, extraction)
        for user_id, filing, extraction in connection.execute(
            sa.text(
                "SELECT user_id, filing_instructions, extraction_instructions FROM app_settings"
            )
        ).all()
    }

    for user_id in sorted(set(chains) | {u for u, (f, e) in instructions.items() if f or e}):
        chain = chains.get(user_id, {"extraction": [], "filing": []})
        filing_instructions, extraction_instructions = instructions.get(user_id, (None, None))
        reading_steps, filing_steps = chain["extraction"], chain["filing"]
        names: set[str] = set()
        for position in range(max(1, len(filing_steps))):
            filing = filing_steps[position] if position < len(filing_steps) else None
            if position < len(reading_steps):
                reading: tuple[str, str, str] | None = reading_steps[position]
            else:
                reading = reading_steps[0] if position == 0 and reading_steps else None
            name = _unique(f"{filing[2]} · {filing[1]}" if filing else "My configuration", names)
            connection.execute(
                sa.text(
                    "INSERT INTO ai_configuration (id, user_id, name, is_default, "
                    "reading_method, reading_endpoint_id, reading_model_name, "
                    "reading_instructions, reading_temperature, reading_max_tokens, "
                    "reading_thinking, filing_endpoint_id, filing_model_name, filing_strategy, "
                    "filing_instructions, filing_temperature, filing_max_tokens, "
                    "filing_thinking, max_examples, max_folders, max_text_chars, created_at) "
                    "VALUES (:id, :user_id, :name, :is_default, :method, :reading_endpoint, "
                    ":reading_model, :reading_instructions, 0.1, 4096, 'default', "
                    ":filing_endpoint, :filing_model, 'one_call', :filing_instructions, 0.1, "
                    "2048, 'default', 8, 250, 6000, :created_at)"
                ),
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "name": name,
                    "is_default": position == 0,
                    "method": "vision" if reading else "text_layer",
                    "reading_endpoint": reading[0] if reading else None,
                    "reading_model": reading[1] if reading else None,
                    "reading_instructions": extraction_instructions,
                    "filing_endpoint": filing[0] if filing else None,
                    "filing_model": filing[1] if filing else None,
                    "filing_instructions": filing_instructions,
                    "created_at": _now(),
                },
            )


def _unique(name: str, taken: set[str]) -> str:
    candidate, counter = name, 2
    while candidate in taken:
        candidate = f"{name} ({counter})"
        counter += 1
    taken.add(candidate)
    return candidate


def _readings_from_documents(connection: sa.engine.Connection) -> None:
    documents = connection.execute(
        sa.text(
            "SELECT id, user_id, extracted_markdown, extraction_model FROM document "
            "WHERE extracted_markdown IS NOT NULL AND extracted_markdown != ''"
        )
    ).all()
    for document_id, user_id, markdown, model in documents:
        reading_id = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO reading (id, user_id, document_id, setup_key, method, label, text, "
                "created_at) VALUES (:id, :user_id, :document_id, :key, 'vision', :label, "
                ":text, :created_at)"
            ),
            {
                "id": reading_id,
                "user_id": user_id,
                "document_id": document_id,
                "key": MIGRATED_SETUP_KEY,
                "label": model or "Vision model",
                "text": markdown,
                "created_at": _now(),
            },
        )
        connection.execute(
            sa.text("UPDATE document SET reading_id = :reading WHERE id = :id"),
            {"reading": reading_id, "id": document_id},
        )
    # How each existing proposal was read, as far as the document records it: a stored
    # extraction model means it read the markdown, anything else the text layer.
    connection.execute(
        sa.text(
            "UPDATE proposal SET reading_label = COALESCE("
            "(SELECT extraction_model FROM document WHERE document.id = proposal.document_id), "
            "'Text layer')"
        )
    )


def downgrade() -> None:
    """Back to chains: each user's default configuration becomes a one-step chain per task.
    Other configurations, model settings and stored readings beyond the latest are lost."""
    op.create_table(
        "ai_task_step",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("task", sa.Enum("extraction", "filing", name="aitask"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("endpoint_id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["endpoint_id"], ["ai_endpoint.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_task_step_user_id", "ai_task_step", ["user_id"])
    op.create_index("ix_ai_task_step_endpoint_id", "ai_task_step", ["endpoint_id"])
    op.create_index("ix_ai_task_step_task", "ai_task_step", ["task"])
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(sa.Column("filing_instructions", sa.String(), nullable=True))
        batch.add_column(sa.Column("extraction_instructions", sa.String(), nullable=True))
    with op.batch_alter_table("document") as batch:
        batch.add_column(sa.Column("extracted_markdown", sa.String(), nullable=True))
        batch.add_column(sa.Column("extraction_model", sa.String(), nullable=True))
        batch.add_column(sa.Column("extraction_error", sa.String(), nullable=True))
        batch.add_column(sa.Column("rerun_endpoint_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("rerun_model_name", sa.String(), nullable=True))
    with op.batch_alter_table("proposal") as batch:
        batch.add_column(
            sa.Column("chosen_by_user", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("fallback_from", sa.String(), nullable=True))

    connection = op.get_bind()
    defaults = connection.execute(
        sa.text(
            "SELECT user_id, reading_method, reading_endpoint_id, reading_model_name, "
            "filing_endpoint_id, filing_model_name, filing_instructions, reading_instructions "
            "FROM ai_configuration WHERE is_default"
        )
    ).all()
    for user_id, method, r_endpoint, r_model, f_endpoint, f_model, f_text, r_text in defaults:
        for task, endpoint_id, model in (
            ("extraction", r_endpoint if method == "vision" else None, r_model),
            ("filing", f_endpoint, f_model),
        ):
            if endpoint_id and model:
                connection.execute(
                    sa.text(
                        "INSERT INTO ai_task_step (id, user_id, task, position, endpoint_id, "
                        "model_name) VALUES (:id, :user_id, :task, 0, :endpoint, :model)"
                    ),
                    {
                        "id": str(uuid4()),
                        "user_id": user_id,
                        "task": task,
                        "endpoint": endpoint_id,
                        "model": model,
                    },
                )
        connection.execute(
            sa.text(
                "UPDATE app_settings SET filing_instructions = :filing, "
                "extraction_instructions = :reading WHERE user_id = :user_id"
            ),
            {"filing": f_text, "reading": r_text, "user_id": user_id},
        )
    connection.execute(
        sa.text(
            "UPDATE document SET extracted_markdown = (SELECT text FROM reading "
            "WHERE reading.id = document.reading_id AND reading.method = 'vision'), "
            "extraction_model = (SELECT label FROM reading WHERE reading.id = document.reading_id "
            "AND reading.method = 'vision')"
        )
    )

    with op.batch_alter_table("proposal") as batch:
        batch.drop_column("reading_label")
        batch.drop_column("configuration_name")
        batch.drop_column("configuration_id")
        batch.drop_column("system_prompt")
    with op.batch_alter_table("document") as batch:
        batch.drop_constraint("fk_document_configuration_id", type_="foreignkey")
        batch.drop_column("waiting_reason")
        batch.drop_column("reading_id")
        batch.drop_column("configuration_id")
    op.drop_index("ix_reading_document_id", table_name="reading")
    op.drop_index("ix_reading_user_id", table_name="reading")
    op.drop_table("reading")
    op.drop_index("ix_ai_configuration_user_id", table_name="ai_configuration")
    op.drop_table("ai_configuration")
