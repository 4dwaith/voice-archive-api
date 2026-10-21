"""Move book ownership to files and track when files become unlinked.

Revision ID: 0009_file_ownership
Revises: 0008_unified_files
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_file_ownership"
down_revision = "0008_unified_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOCK TABLE books, files IN ACCESS EXCLUSIVE MODE")
    # Refuse invalid legacy links rather than silently changing ownership.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM books b JOIN files f ON f.id = b.cover_file_id WHERE f.kind <> 'cover'
            ) OR EXISTS (
                SELECT 1 FROM books b JOIN files f ON f.id = b.source_pdf_file_id WHERE f.kind <> 'source_pdf'
            ) THEN
                RAISE EXCEPTION 'Cannot migrate files: book file kind mismatch';
            END IF;
        END $$
    """)
    op.add_column("files", sa.Column("book_id", sa.BigInteger(), nullable=True))
    op.add_column("files", sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(op.f("fk_files_book_id_books"), "files", "books", ["book_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_files_book_kind", "files", ["book_id", "kind"])
    op.execute("""
        UPDATE files f SET book_id = b.id
        FROM books b WHERE f.id = b.cover_file_id OR f.id = b.source_pdf_file_id
    """)
    op.execute("UPDATE files SET unlinked_at = created_at WHERE book_id IS NULL")
    op.create_check_constraint(
        op.f("ck_files_ownership_timestamp"), "files", "(book_id IS NULL) = (unlinked_at IS NOT NULL)"
    )
    op.create_check_constraint(
        op.f("ck_files_book_kind"), "files", "book_id IS NULL OR kind IN ('cover', 'source_pdf')"
    )
    op.execute("""
        CREATE FUNCTION set_file_unlinked_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.book_id IS NOT NULL THEN
                NEW.unlinked_at := NULL;
            ELSIF TG_OP = 'INSERT' THEN
                NEW.unlinked_at := NEW.created_at;
            ELSIF OLD.book_id IS DISTINCT FROM NEW.book_id THEN
                NEW.unlinked_at := statement_timestamp();
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER files_ownership_timestamp
        BEFORE INSERT OR UPDATE ON files
        FOR EACH ROW EXECUTE FUNCTION set_file_unlinked_at()
    """)
    for field in ("cover_file_id", "source_pdf_file_id"):
        op.drop_column("books", field)
    op.drop_index("ix_files_created_at_id", table_name="files")
    op.create_index("ix_files_unlinked_at_id", "files", ["unlinked_at", "id"])
    op.execute("UPDATE file_cleanup_progress SET cleanup_before = NULL")


def downgrade() -> None:
    op.execute("LOCK TABLE books, files IN ACCESS EXCLUSIVE MODE")
    for field, kind in (("cover_file_id", "cover"), ("source_pdf_file_id", "source_pdf")):
        op.add_column("books", sa.Column(field, sa.BigInteger(), nullable=True))
        op.execute(f"UPDATE books b SET {field} = f.id FROM files f WHERE f.book_id = b.id AND f.kind = '{kind}'")
        op.create_foreign_key(op.f(f"fk_books_{field}_files"), "books", "files", [field], ["id"])
        op.create_unique_constraint(op.f(f"uq_books_{field}"), "books", [field])
    op.execute("DROP TRIGGER files_ownership_timestamp ON files")
    op.execute("DROP FUNCTION set_file_unlinked_at()")
    op.drop_index("ix_files_unlinked_at_id", table_name="files")
    op.create_index("ix_files_created_at_id", "files", ["created_at", "id"])
    op.drop_constraint(op.f("ck_files_book_kind"), "files", type_="check")
    op.drop_constraint(op.f("ck_files_ownership_timestamp"), "files", type_="check")
    op.drop_constraint("uq_files_book_kind", "files", type_="unique")
    op.drop_constraint(op.f("fk_files_book_id_books"), "files", type_="foreignkey")
    op.drop_column("files", "unlinked_at")
    op.drop_column("files", "book_id")
    op.execute("UPDATE file_cleanup_progress SET cleanup_before = NULL")
