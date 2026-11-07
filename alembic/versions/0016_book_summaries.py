"""Nullable canonical summaries and explicit book summary runs."""

import sqlalchemy as sa

from alembic import op

revision = "0016_book_summaries"
down_revision = "0015_series_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("books", "sections", "segments"):
        op.add_column(table, sa.Column("summary", sa.Text(), nullable=True))
    op.create_table(
        "book_summary_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("policy", sa.Text(), nullable=False),
        sa.Column("source_language", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text()),
        sa.CheckConstraint("state IN ('running', 'completed', 'failed')", name="summary_run_state"),
        sa.CheckConstraint("(state = 'running') = (finished_at IS NULL)", name="summary_run_finished"),
    )
    op.create_index("ix_book_summary_runs_book_id", "book_summary_runs", ["book_id"])


def downgrade() -> None:
    op.drop_table("book_summary_runs")
    for table in ("segments", "sections", "books"):
        op.drop_column(table, "summary")
