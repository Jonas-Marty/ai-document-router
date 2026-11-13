"""prices per model instead of per connection

Revision ID: a3d6e1f8c2b4
Revises: f1c4a8b2d9e3
Create Date: 2026-11-13 18:00:00.000000

A connection's two prices become a price list keyed by model name. Each model already used
on the connection, by a configuration or in the call history, gets the connection's old
prices, so nothing that showed a cost before stops showing one. Other models start without
a price, which shows no cost rather than a wrong one.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3d6e1f8c2b4"
down_revision: str | Sequence[str] | None = "f1c4a8b2d9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.add_column(sa.Column("model_prices", sa.JSON(), nullable=False, server_default="[]"))

    bind = op.get_bind()
    endpoints = bind.execute(
        sa.text(
            "SELECT id, price_input_per_million, price_output_per_million FROM ai_endpoint "
            "WHERE price_input_per_million IS NOT NULL OR price_output_per_million IS NOT NULL"
        )
    ).all()
    for endpoint_id, price_input, price_output in endpoints:
        models = bind.execute(
            sa.text(
                "SELECT reading_model_name FROM ai_configuration WHERE reading_endpoint_id = :id "
                "UNION SELECT filing_model_name FROM ai_configuration "
                "WHERE filing_endpoint_id = :id "
                "UNION SELECT model FROM ai_call WHERE endpoint_id = :id"
            ),
            {"id": endpoint_id},
        ).scalars()
        prices = [
            {
                "model_name": model,
                "price_input_per_million": price_input,
                "price_output_per_million": price_output,
            }
            for model in sorted({m for m in models if m})
        ]
        bind.execute(
            sa.text("UPDATE ai_endpoint SET model_prices = :prices WHERE id = :id"),
            {"prices": json.dumps(prices), "id": endpoint_id},
        )

    with op.batch_alter_table("ai_endpoint") as batch:
        batch.drop_column("price_output_per_million")
        batch.drop_column("price_input_per_million")


def downgrade() -> None:
    with op.batch_alter_table("ai_endpoint") as batch:
        batch.add_column(sa.Column("price_input_per_million", sa.Float(), nullable=True))
        batch.add_column(sa.Column("price_output_per_million", sa.Float(), nullable=True))

    # The connection gets back the first model's prices: one pair cannot hold several.
    bind = op.get_bind()
    for endpoint_id, raw in bind.execute(sa.text("SELECT id, model_prices FROM ai_endpoint")):
        prices = json.loads(raw) if isinstance(raw, str) else raw
        if prices:
            bind.execute(
                sa.text(
                    "UPDATE ai_endpoint SET price_input_per_million = :i, "
                    "price_output_per_million = :o WHERE id = :id"
                ),
                {
                    "i": prices[0].get("price_input_per_million"),
                    "o": prices[0].get("price_output_per_million"),
                    "id": endpoint_id,
                },
            )

    with op.batch_alter_table("ai_endpoint") as batch:
        batch.drop_column("model_prices")
