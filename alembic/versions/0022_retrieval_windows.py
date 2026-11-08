"""Store whole-segment windows and planned embedding rows."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_retrieval_windows"
down_revision = "0021_summary_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_windows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_version", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.BigInteger(), sa.ForeignKey("sections.id", ondelete="CASCADE")),
        sa.Column("segment_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("primary_text", sa.Text(), nullable=False),
        sa.Column("translated_text", sa.Text()),
        sa.UniqueConstraint("book_id", "sequence", name="uq_retrieval_windows_book_sequence"),
    )
    op.create_table(
        "window_embeddings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("window_id", sa.Uuid(), sa.ForeignKey("retrieval_windows.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "kind",
            sa.Text(),
            sa.CheckConstraint("kind IN ('primary', 'translated')", name="window_embedding_kind"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "dimensions",
            sa.Integer(),
            sa.CheckConstraint("dimensions > 0", name="window_embedding_dimensions"),
            nullable=False,
        ),
        sa.Column("input_format", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("vector", postgresql.ARRAY(sa.Double())),
        sa.UniqueConstraint(
            "window_id", "kind", "model", "dimensions", "input_format", name="uq_window_embedding_profile"
        ),
        sa.CheckConstraint(
            "vector IS NULL OR cardinality(vector) = dimensions", name="window_embedding_vector_dimensions"
        ),
    )


def downgrade() -> None:
    op.drop_table("window_embeddings")
    op.drop_table("retrieval_windows")
