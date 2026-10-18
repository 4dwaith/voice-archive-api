"""Use column types for book length limits.

Revision ID: 0003_book_column_constraints
Revises: 0002_book_metadata
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_book_column_constraints"
down_revision = "0002_book_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "books", "description", existing_type=sa.Text(), type_=sa.String(15000)
    )
    op.create_check_constraint(op.f("ck_books_title_nonempty"), "books", "length(title) > 0")
    for name in ("title_length", "author_length", "description_length"):
        op.drop_constraint(op.f(f"ck_books_{name}"), "books", type_="check")


def downgrade() -> None:
    for name, expression in (
        ("title_length", "length(title) BETWEEN 1 AND 750"),
        ("author_length", "author IS NULL OR length(author) <= 450"),
        ("description_length", "description IS NULL OR length(description) <= 15000"),
    ):
        op.create_check_constraint(op.f(f"ck_books_{name}"), "books", expression)
    op.drop_constraint(op.f("ck_books_title_nonempty"), "books", type_="check")
    op.alter_column(
        "books", "description", existing_type=sa.String(15000), type_=sa.Text()
    )
