"""Record independently published summary scopes and downstream overrides."""

import sqlalchemy as sa

from alembic import op

revision = "0018_summary_scopes"
down_revision = "0017_passage_translations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("book_summary_runs", sa.Column("root_section_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_summary_run_root", "book_summary_runs", "sections", ["root_section_id"], ["id"], ondelete="CASCADE"
    )
    op.add_column(
        "book_summary_runs", sa.Column("allow_stale_downstream", sa.Boolean(), server_default="false", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("book_summary_runs", "allow_stale_downstream")
    op.drop_constraint("fk_summary_run_root", "book_summary_runs", type_="foreignkey")
    op.drop_column("book_summary_runs", "root_section_id")
