"""Record embedding batch submissions and outstanding input claims."""

import sqlalchemy as sa

from alembic import op

revision = "0023_embedding_batches"
down_revision = "0022_retrieval_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_batch_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("provider_name", sa.Text(), unique=True),
        sa.Column("input_file_name", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "embedding_batch_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("embedding_batch_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("embedding_id", sa.Uuid(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_embedding_batch_requests_batch_id", "embedding_batch_requests", ["batch_id"])
    op.create_index(
        "uq_embedding_request_active_input",
        "embedding_batch_requests",
        ["embedding_id", "input_hash"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_table("embedding_batch_requests")
    op.drop_table("embedding_batch_jobs")
