"""Backfill summary token usage and require zero-default measurements."""

import sqlalchemy as sa

from alembic import op

revision = "0020_required_summary_usage"
down_revision = "0019_summary_token_usage"
branch_labels = None
depends_on = None

USAGE_COLUMNS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "thinking_tokens",
    "total_tokens",
    "generation_response_count",
    "missing_usage_response_count",
)


def upgrade() -> None:
    table = sa.table("book_summary_runs", *(sa.column(name, sa.BigInteger()) for name in USAGE_COLUMNS))
    op.execute(table.update().values({name: sa.func.coalesce(table.c[name], 0) for name in USAGE_COLUMNS}))
    for name in USAGE_COLUMNS:
        op.alter_column("book_summary_runs", name, existing_type=sa.BigInteger(), nullable=False, server_default="0")


def downgrade() -> None:
    for name in reversed(USAGE_COLUMNS):
        op.alter_column("book_summary_runs", name, existing_type=sa.BigInteger(), nullable=True, server_default=None)
