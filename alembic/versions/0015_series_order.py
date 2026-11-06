"""Give series members continuous, unique positions."""

import sqlalchemy as sa

from alembic import op

revision = "0015_series_order"
down_revision = "0014_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column("series_index", sa.Integer(), nullable=True))
    op.execute("""
        WITH positions AS (
            SELECT id, row_number() OVER (PARTITION BY series_id ORDER BY id)::integer AS position
            FROM books WHERE series_id IS NOT NULL
        )
        UPDATE books SET series_index = positions.position FROM positions WHERE books.id = positions.id
    """)
    op.create_check_constraint(
        op.f("ck_books_series_membership"),
        "books",
        "(series_id IS NULL AND series_index IS NULL) OR (series_id IS NOT NULL AND series_index > 0 AND series_index IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_books_series_index", "books", ["series_id", "series_index"], deferrable=True, initially="DEFERRED"
    )
    op.drop_constraint(op.f("fk_books_series_id_series"), "books", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_books_series_id_series"), "books", "series", ["series_id"], ["id"], ondelete="RESTRICT"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_books_series_id_series"), "books", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_books_series_id_series"), "books", "series", ["series_id"], ["id"], ondelete="SET NULL"
    )
    op.drop_constraint("uq_books_series_index", "books", type_="unique")
    op.drop_constraint(op.f("ck_books_series_membership"), "books", type_="check")
    op.drop_column("books", "series_index")
