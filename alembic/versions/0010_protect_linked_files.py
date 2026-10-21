"""Reject deletion of files until their owner has been cleared.

Revision ID: 0010_protect_linked_files
Revises: 0009_file_ownership
"""

from alembic import op

revision = "0010_protect_linked_files"
down_revision = "0009_file_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION reject_linked_file_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Cannot delete a linked file; detach it first'
                USING ERRCODE = '23514';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER files_reject_linked_deletion
        BEFORE DELETE ON files
        FOR EACH ROW WHEN (OLD.book_id IS NOT NULL)
        EXECUTE FUNCTION reject_linked_file_deletion()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER files_reject_linked_deletion ON files")
    op.execute("DROP FUNCTION reject_linked_file_deletion()")
