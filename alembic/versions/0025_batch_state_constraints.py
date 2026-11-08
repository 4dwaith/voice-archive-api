"""Validate queue and embedding batch states using existing text values."""

import sqlalchemy as sa

from alembic import op

revision = "0025_batch_state_constraints"
down_revision = "0024_book_batch_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("book_batch_jobs", "state", existing_type=sa.Text(), type_=sa.String(9))
    op.create_check_constraint(
        "book_batch_job_state", "book_batch_jobs", "state IN ('queued', 'running', 'completed', 'failed')"
    )
    op.alter_column("embedding_batch_jobs", "state", existing_type=sa.Text(), type_=sa.String(21))
    op.create_check_constraint(
        "embedding_batch_job_state",
        "embedding_batch_jobs",
        "state IN ('submitting', 'submitted', 'completed', 'completed_with_errors', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("embedding_batch_job_state", "embedding_batch_jobs", type_="check")
    op.alter_column("embedding_batch_jobs", "state", existing_type=sa.String(21), type_=sa.Text())
    op.drop_constraint("book_batch_job_state", "book_batch_jobs", type_="check")
    op.alter_column("book_batch_jobs", "state", existing_type=sa.String(9), type_=sa.Text())
