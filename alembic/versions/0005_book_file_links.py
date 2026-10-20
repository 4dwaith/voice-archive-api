"""Link prepared covers and source PDFs through explicit book saves.

Revision ID: 0005_book_file_links
Revises: 0004_book_files
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_book_file_links"
down_revision = "0004_book_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column(
        "cover_file_id", sa.BigInteger(),
        sa.ForeignKey("cover_files.id", name=op.f("fk_books_cover_file_id_cover_files")),
        nullable=True,
    ))
    op.add_column("books", sa.Column(
        "source_pdf_file_id", sa.BigInteger(),
        sa.ForeignKey("source_pdf_files.id", name=op.f("fk_books_source_pdf_file_id_source_pdf_files")),
        nullable=True,
    ))
    # PostgreSQL permits multiple NULLs: books without either file remain valid.
    op.create_unique_constraint(op.f("uq_books_cover_file_id"), "books", ["cover_file_id"])
    op.create_unique_constraint(op.f("uq_books_source_pdf_file_id"), "books", ["source_pdf_file_id"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_books_source_pdf_file_id"), "books", type_="unique")
    op.drop_constraint(op.f("uq_books_cover_file_id"), "books", type_="unique")
    op.drop_constraint(op.f("fk_books_source_pdf_file_id_source_pdf_files"), "books", type_="foreignkey")
    op.drop_constraint(op.f("fk_books_cover_file_id_cover_files"), "books", type_="foreignkey")
    op.drop_column("books", "source_pdf_file_id")
    op.drop_column("books", "cover_file_id")
