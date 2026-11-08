"""Retain validated summary batches for resumable generation."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_summary_checkpoints"
down_revision = "0020_required_summary_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "book_summary_batch_checkpoints",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("book_summary_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("request_fingerprint", sa.String(64), primary_key=True),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("summaries", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("target_kind IN ('book', 'section')", name="summary_checkpoint_target_kind"),
        sa.CheckConstraint("batch_index > 0", name="summary_checkpoint_batch_index"),
    )


def downgrade() -> None:
    op.drop_table("book_summary_batch_checkpoints")
