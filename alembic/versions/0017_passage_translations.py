"""Store faithful English passage translations with accepted summaries."""

import sqlalchemy as sa

from alembic import op

revision = "0017_passage_translations"
down_revision = "0016_book_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segments", sa.Column("english_translation", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("segments", "english_translation")
