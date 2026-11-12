"""Record uploaders; legacy linked files use their book's current owner."""

import sqlalchemy as sa

from alembic import op

revision = "0029_file_uploaders"
down_revision = "0028_users_and_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 0028 creates the reserved Unassigned account first, with ID 1.
    op.add_column("files", sa.Column("uploaded_by_user_id", sa.BigInteger(), nullable=False, server_default="1"))
    op.execute("""
        UPDATE files SET uploaded_by_user_id = coalesce(
            (SELECT books.owner_user_id FROM books WHERE books.id = files.book_id),
            (SELECT books.owner_user_id FROM sections JOIN books ON books.id = sections.book_id
             WHERE sections.id = files.section_id), 1)
    """)
    op.create_foreign_key(
        "fk_files_uploaded_by_user_id_users", "files", "users", ["uploaded_by_user_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_files_uploaded_by_user_id", "files", ["uploaded_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_files_uploaded_by_user_id", table_name="files")
    op.drop_column("files", "uploaded_by_user_id")
