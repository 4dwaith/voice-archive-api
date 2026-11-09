"""Index original window text using its existing translation-presence contract."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

from alembic import op

revision = "0027_window_lexical"
down_revision = "0026_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column(
        "retrieval_windows",
        sa.Column(
            "english_vector",
            TSVECTOR(),
            sa.Computed(
                "CASE WHEN translated_text IS NULL THEN to_tsvector('english'::regconfig, primary_text) END",
                persisted=True,
            ),
        ),
    )
    op.add_column(
        "retrieval_windows",
        sa.Column(
            "trigram_text",
            sa.Text(),
            sa.Computed("CASE WHEN translated_text IS NOT NULL THEN normalize(primary_text, NFC) END", persisted=True),
        ),
    )
    op.create_index(
        "ix_retrieval_windows_english",
        "retrieval_windows",
        ["english_vector"],
        postgresql_using="gin",
        postgresql_where=sa.text("translated_text IS NULL"),
    )
    op.create_index(
        "ix_retrieval_windows_trigrams",
        "retrieval_windows",
        ["trigram_text"],
        postgresql_using="gin",
        postgresql_ops={"trigram_text": "gin_trgm_ops"},
        postgresql_where=sa.text("translated_text IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_windows_trigrams", table_name="retrieval_windows")
    op.drop_index("ix_retrieval_windows_english", table_name="retrieval_windows")
    op.drop_column("retrieval_windows", "trigram_text")
    op.drop_column("retrieval_windows", "english_vector")
    # Other consumers may use pg_trgm; retain its installation.
