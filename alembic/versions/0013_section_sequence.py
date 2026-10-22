"""Store section preorder once, preserving existing content and versions."""

import sqlalchemy as sa

from alembic import op

revision = "0013_section_sequence"
down_revision = "0012_content_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sections", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute("""
        WITH RECURSIVE preorder AS (
            SELECT id, book_id, ARRAY[sibling_sequence] AS path
            FROM sections WHERE parent_id IS NULL
            UNION ALL
            SELECT child.id, child.book_id, parent.path || child.sibling_sequence
            FROM sections child JOIN preorder parent
            ON child.parent_id = parent.id AND child.book_id = parent.book_id
        ), numbered AS (
            SELECT id, (row_number() OVER (PARTITION BY book_id ORDER BY path) - 1)::integer AS sequence
            FROM preorder
        )
        UPDATE sections SET sequence = numbered.sequence FROM numbered WHERE sections.id = numbered.id
    """)
    op.alter_column("sections", "sequence", nullable=False)
    op.create_check_constraint(op.f("ck_sections_sequence"), "sections", "sequence >= 0")
    op.create_unique_constraint("uq_sections_book_sequence", "sections", ["book_id", "sequence"])


def downgrade() -> None:
    op.drop_constraint("uq_sections_book_sequence", "sections", type_="unique")
    op.drop_constraint(op.f("ck_sections_sequence"), "sections", type_="check")
    op.drop_column("sections", "sequence")
