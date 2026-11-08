"""Persist book requests for sequential provider batches."""

import sqlalchemy as sa

from alembic import op

revision = "0024_book_batch_jobs"
down_revision = "0023_embedding_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "book_batch_jobs",
        sa.Column("book_id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("active_batch_id", sa.Uuid(), unique=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("book_batch_jobs")
