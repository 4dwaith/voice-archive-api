"""Retain pending deletions for safe retries after database commits.

Revision ID: 0006_pending_file_deletions
Revises: 0005_book_file_links
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_pending_file_deletions"
down_revision = "0005_book_file_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_file_deletions",
        sa.Column(
            "storage_key", sa.Text(),
            sa.CheckConstraint("length(storage_key) > 0", name=op.f("ck_pending_file_deletions_storage_key_nonempty")),
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("storage_key", name=op.f("pk_pending_file_deletions")),
    )


def downgrade() -> None:
    op.drop_table("pending_file_deletions")
