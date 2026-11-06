"""Add series membership without changing existing book content."""

import sqlalchemy as sa

from alembic import op

revision = "0014_series"
down_revision = "0013_section_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("title", sa.String(750), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id > 0", name="positive_id"),
        sa.CheckConstraint("length(title) > 0", name="title_nonempty"),
    )
    op.add_column("books", sa.Column("series_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_books_series_id_series"), "books", "series", ["series_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_books_series_id", "books", ["series_id"])


def downgrade() -> None:
    op.drop_index("ix_books_series_id", table_name="books")
    op.drop_constraint(op.f("fk_books_series_id_series"), "books", type_="foreignkey")
    op.drop_column("books", "series_id")
    op.drop_table("series")
