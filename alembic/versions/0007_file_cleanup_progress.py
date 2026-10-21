"""Index prepared-file ages and retain completed cleanup boundaries.

Revision ID: 0007_file_cleanup_progress
Revises: 0006_pending_file_deletions
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_file_cleanup_progress"
down_revision = "0006_pending_file_deletions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_cleanup_progress",
        sa.Column(
            "id",
            sa.Integer(),
            sa.CheckConstraint("id = 1", name=op.f("ck_file_cleanup_progress_singleton")),
            nullable=False,
            autoincrement=False,
        ),
        sa.Column("cover_cleanup_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_pdf_cleanup_before", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_cleanup_progress")),
    )
    op.execute("INSERT INTO file_cleanup_progress (id) VALUES (1)")
    op.create_index("ix_cover_files_created_at_id", "cover_files", ["created_at", "id"])
    op.create_index("ix_source_pdf_files_created_at_id", "source_pdf_files", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_source_pdf_files_created_at_id", table_name="source_pdf_files")
    op.drop_index("ix_cover_files_created_at_id", table_name="cover_files")
    op.drop_table("file_cleanup_progress")
