"""Consolidate prepared files, preserving bytes, metadata, and book references.

Revision ID: 0008_unified_files
Revises: 0007_file_cleanup_progress
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0008_unified_files"
down_revision = "0007_file_cleanup_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.BigInteger(), sa.Identity(),
                  sa.CheckConstraint("id > 0", name=op.f("ck_files_positive_id")), nullable=False),
        sa.Column("kind", sa.Enum("cover", "source_pdf", native_enum=False,
                                 create_constraint=True, name="file_kind"), nullable=False),
        sa.Column("storage_key", sa.Text(),
                  sa.CheckConstraint("length(storage_key) > 0", name=op.f("ck_files_storage_key_nonempty")), nullable=False),
        sa.Column("byte_size", sa.BigInteger(),
                  sa.CheckConstraint("byte_size > 0", name=op.f("ck_files_positive_byte_size")), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_files")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_files_storage_key")),
    )
    op.create_index("ix_files_created_at_id", "files", ["created_at", "id"])
    # IDs were allocated independently. Keep cover IDs and shift PDF IDs above
    # the cover range; update every PDF reference before replacing the old FK.
    op.execute("LOCK TABLE books, cover_files, source_pdf_files IN ACCESS EXCLUSIVE MODE")
    offset = op.get_bind().scalar(sa.text("SELECT COALESCE(MAX(id), 0) FROM cover_files"))
    op.execute("""
        INSERT INTO files (id, kind, storage_key, byte_size, metadata, created_at)
        SELECT id, 'cover', storage_key, byte_size,
               jsonb_build_object('width', width, 'height', height), created_at FROM cover_files
    """)
    op.get_bind().execute(sa.text("""
        INSERT INTO files (id, kind, storage_key, byte_size, metadata, created_at)
        SELECT id + :offset, 'source_pdf', storage_key, byte_size,
               jsonb_build_object('page_count', page_count), created_at FROM source_pdf_files
    """), {"offset": offset})
    for field, old_table in (("cover_file_id", "cover_files"), ("source_pdf_file_id", "source_pdf_files")):
        op.drop_constraint(f"fk_books_{field}_{old_table}", "books", type_="foreignkey")
    op.get_bind().execute(sa.text(
        "UPDATE books SET source_pdf_file_id = source_pdf_file_id + :offset WHERE source_pdf_file_id IS NOT NULL"
    ), {"offset": offset})
    for field in ("cover_file_id", "source_pdf_file_id"):
        op.create_foreign_key(op.f(f"fk_books_{field}_files"), "books", "files", [field], ["id"])
    op.execute("""
        SELECT setval(pg_get_serial_sequence('files', 'id'),
                      COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM files
    """)
    op.drop_table("source_pdf_files")
    op.drop_table("cover_files")
    # Restart the bounded age scan across the unified ID space. Retrying an age
    # window is harmless, and avoids skipping a kind with an older boundary.
    op.add_column("file_cleanup_progress", sa.Column("cleanup_before", sa.DateTime(timezone=True)))
    op.drop_column("file_cleanup_progress", "cover_cleanup_before")
    op.drop_column("file_cleanup_progress", "source_pdf_cleanup_before")


def downgrade() -> None:
    for table, metadata_fields in (("cover_files", ("width", "height")), ("source_pdf_files", ("page_count",))):
        op.create_table(
            table,
            sa.Column("id", sa.BigInteger(), sa.Identity(),
                      sa.CheckConstraint("id > 0", name=op.f(f"ck_{table}_positive_id")), nullable=False),
            sa.Column("storage_key", sa.Text(),
                      sa.CheckConstraint("length(storage_key) > 0", name=op.f(f"ck_{table}_storage_key_nonempty")), nullable=False),
            sa.Column("byte_size", sa.BigInteger(),
                      sa.CheckConstraint("byte_size > 0", name=op.f(f"ck_{table}_positive_byte_size")), nullable=False),
            *(sa.Column(field, sa.Integer(),
                        sa.CheckConstraint(f"{field} > 0", name=op.f(f"ck_{table}_positive_{field}")), nullable=False)
              for field in metadata_fields),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
            sa.UniqueConstraint("storage_key", name=op.f(f"uq_{table}_storage_key")),
        )
        kind = {"cover_files": "cover", "source_pdf_files": "source_pdf"}[table]
        columns = ", ".join(metadata_fields)
        values = ", ".join(f"(metadata->>'{field}')::integer" for field in metadata_fields)
        # Legacy tables require metadata. Refuse an incompatible downgrade
        # transactionally rather than fabricating dimensions or page counts.
        op.execute(f"""
            INSERT INTO {table} (id, storage_key, byte_size, created_at, {columns})
            SELECT id, storage_key, byte_size, created_at, {values} FROM files WHERE kind = '{kind}'
        """)
        op.execute(f"""
            SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                          COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}
        """)
        op.create_index(f"ix_{table}_created_at_id", table, ["created_at", "id"])
    for field, table in (("cover_file_id", "cover_files"), ("source_pdf_file_id", "source_pdf_files")):
        op.drop_constraint(f"fk_books_{field}_files", "books", type_="foreignkey")
        op.create_foreign_key(op.f(f"fk_books_{field}_{table}"), "books", table, [field], ["id"])
    op.drop_table("files")
    for field in ("cover_cleanup_before", "source_pdf_cleanup_before"):
        op.add_column("file_cleanup_progress", sa.Column(field, sa.DateTime(timezone=True)))
    op.drop_column("file_cleanup_progress", "cleanup_before")
