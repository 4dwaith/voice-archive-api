"""Create book metadata and database-enforced identity/length constraints.

Revision ID: 0002_book_metadata
Revises: 0001_backend_foundation
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_book_metadata"
down_revision = "0001_backend_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "books",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("title", sa.String(750), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("author", sa.String(450), nullable=True),
        sa.Column("normalized_author", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id > 0", name=op.f("ck_books_positive_id")),
        sa.CheckConstraint(
            "length(title) BETWEEN 1 AND 750",
            name=op.f("ck_books_title_length"),
        ),
        sa.CheckConstraint(
            "length(normalized_title) > 0",
            name=op.f("ck_books_normalized_title_nonempty"),
        ),
        sa.CheckConstraint(
            "author IS NULL OR length(author) <= 450",
            name=op.f("ck_books_author_length"),
        ),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 15000",
            name=op.f("ck_books_description_length"),
        ),
        sa.CheckConstraint(
            "length(language) > 0",
            name=op.f("ck_books_language_nonempty"),
        ),
    )
    op.create_index(
        "uq_books_identity",
        "books",
        [
            sa.text("digest(normalized_title, 'sha256')"),
            sa.text("digest(normalized_author, 'sha256')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("books")
