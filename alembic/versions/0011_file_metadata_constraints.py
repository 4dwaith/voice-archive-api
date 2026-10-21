"""Restore kind-specific required positive integer file metadata.

Revision ID: 0011_file_metadata_constraints
Revises: 0010_protect_linked_files
"""

from alembic import op

revision = "0011_file_metadata_constraints"
down_revision = "0010_protect_linked_files"
branch_labels = None
depends_on = None


def positive_metadata_integer(field: str) -> str:
    # Keep the migration self-contained, independent of future model changes.
    return f"""CASE
        WHEN jsonb_typeof(metadata->'{field}') = 'number'
             AND (metadata->>'{field}') ~ '^[1-9][0-9]*$'
        THEN (metadata->>'{field}')::numeric <= 2147483647
        ELSE false END"""


def upgrade() -> None:
    # Validate existing rows; abort transactionally instead of inventing metadata.
    op.create_check_constraint(
        op.f("ck_files_cover_metadata"),
        "files",
        f"kind <> 'cover' OR ({positive_metadata_integer('width')} AND {positive_metadata_integer('height')})",
    )
    op.create_check_constraint(
        op.f("ck_files_source_pdf_metadata"),
        "files",
        f"kind <> 'source_pdf' OR ({positive_metadata_integer('page_count')})",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_files_source_pdf_metadata"), "files", type_="check")
    op.drop_constraint(op.f("ck_files_cover_metadata"), "files", type_="check")
