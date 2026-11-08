"""Record cumulative provider token usage for summary runs."""

import sqlalchemy as sa

from alembic import op

revision = "0019_summary_token_usage"
down_revision = "0018_summary_scopes"
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
    # Leave existing runs unknown rather than inventing zero-token measurements.
    for name in USAGE_COLUMNS:
        op.add_column("book_summary_runs", sa.Column(name, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    for name in reversed(USAGE_COLUMNS):
        op.drop_column("book_summary_runs", name)
