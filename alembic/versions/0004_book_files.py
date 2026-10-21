"""Persist prepared covers and source PDFs in separate tables.

Revision ID: 0004_book_files
Revises: 0003_book_column_constraints
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_book_files"
down_revision = "0003_book_column_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cover_files",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            sa.CheckConstraint("id > 0", name=op.f("ck_cover_files_positive_id")),
            nullable=False,
        ),
        sa.Column(
            "storage_key",
            sa.Text(),
            sa.CheckConstraint("length(storage_key) > 0", name=op.f("ck_cover_files_storage_key_nonempty")),
            nullable=False,
        ),
        sa.Column(
            "byte_size",
            sa.BigInteger(),
            sa.CheckConstraint("byte_size > 0", name=op.f("ck_cover_files_positive_byte_size")),
            nullable=False,
        ),
        sa.Column(
            "width",
            sa.Integer(),
            sa.CheckConstraint("width > 0", name=op.f("ck_cover_files_positive_width")),
            nullable=False,
        ),
        sa.Column(
            "height",
            sa.Integer(),
            sa.CheckConstraint("height > 0", name=op.f("ck_cover_files_positive_height")),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cover_files")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_cover_files_storage_key")),
    )

    op.create_table(
        "source_pdf_files",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            sa.CheckConstraint("id > 0", name=op.f("ck_source_pdf_files_positive_id")),
            nullable=False,
        ),
        sa.Column(
            "storage_key",
            sa.Text(),
            sa.CheckConstraint("length(storage_key) > 0", name=op.f("ck_source_pdf_files_storage_key_nonempty")),
            nullable=False,
        ),
        sa.Column(
            "byte_size",
            sa.BigInteger(),
            sa.CheckConstraint("byte_size > 0", name=op.f("ck_source_pdf_files_positive_byte_size")),
            nullable=False,
        ),
        sa.Column(
            "page_count",
            sa.Integer(),
            sa.CheckConstraint("page_count > 0", name=op.f("ck_source_pdf_files_positive_page_count")),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_pdf_files")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_source_pdf_files_storage_key")),
    )


def downgrade() -> None:
    # This removes records only. Filesystem cleanup is never a migration side effect.
    op.drop_table("source_pdf_files")
    op.drop_table("cover_files")
