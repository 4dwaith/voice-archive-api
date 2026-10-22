"""Immutable content domain and section-owned files.

Revision ID: 0012_content_domain
Revises: 0011_file_metadata_constraints
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0012_content_domain"
down_revision = "0011_file_metadata_constraints"
branch_labels = None
depends_on = None


def ownership_trigger(*, sections: bool) -> None:
    linked = "NEW.book_id IS NOT NULL" + (" OR NEW.section_id IS NOT NULL" if sections else "")
    changed = "OLD.book_id IS DISTINCT FROM NEW.book_id" + (
        " OR OLD.section_id IS DISTINCT FROM NEW.section_id" if sections else ""
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION set_file_unlinked_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF {linked} THEN NEW.unlinked_at := NULL;
            ELSIF TG_OP = 'INSERT' THEN NEW.unlinked_at := NEW.created_at;
            ELSIF {changed} THEN NEW.unlinked_at := statement_timestamp();
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("DROP TRIGGER files_reject_linked_deletion ON files")
    condition = "OLD.book_id IS NOT NULL" + (" OR OLD.section_id IS NOT NULL" if sections else "")
    op.execute(f"""CREATE TRIGGER files_reject_linked_deletion BEFORE DELETE ON files
        FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION reject_linked_file_deletion()""")


def upgrade() -> None:
    op.add_column("books", sa.Column("content_version", sa.Uuid(), nullable=True))
    op.add_column("books", sa.Column("content_schema_version", sa.Integer(), nullable=True))
    op.add_column("books", sa.Column("content_imported_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_books_content_metadata"),
        "books",
        """
        (content_version IS NULL AND content_schema_version IS NULL AND content_imported_at IS NULL)
        OR (content_version IS NOT NULL AND content_schema_version IS NOT NULL
            AND content_schema_version > 0 AND content_imported_at IS NOT NULL)""",
    )
    op.create_table(
        "sections",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.BigInteger()),
        sa.Column(
            "kind",
            sa.Enum("part", "chapter", "section", native_enum=False, create_constraint=True, name="kind"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("sibling_sequence", sa.Integer(), nullable=False),
        sa.UniqueConstraint("book_id", "id", name="uq_sections_book_id_id"),
        sa.UniqueConstraint("book_id", "source_key", name="uq_sections_book_source_key"),
        sa.UniqueConstraint(
            "book_id",
            "parent_id",
            "sibling_sequence",
            name="uq_sections_sibling_sequence",
            postgresql_nulls_not_distinct=True,
        ),
        sa.ForeignKeyConstraint(["book_id", "parent_id"], ["sections.book_id", "sections.id"]),
        sa.CheckConstraint("id > 0", name="positive_id"),
        sa.CheckConstraint("length(source_key) BETWEEN 1 AND 256", name="source_key_length"),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 2000", name="title_length"),
        sa.CheckConstraint("sibling_sequence >= 0", name="sibling_sequence"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_self_parent"),
    )
    # Only page projection is validated here. Full rectangle geometry belongs to canonical validation.
    op.execute("""
        CREATE FUNCTION public.region_pages(value jsonb) RETURNS integer[]
        LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
        DECLARE entry jsonb; pages integer[] := '{}'; page numeric;
        BEGIN
            IF jsonb_typeof(value) <> 'array' THEN
                RAISE EXCEPTION 'regions must be an array' USING ERRCODE = '23514';
            END IF;
            FOR entry IN SELECT jsonb_array_elements(value) LOOP
                IF jsonb_typeof(entry) <> 'object'
                   OR jsonb_typeof(entry->'page_index') IS DISTINCT FROM 'number'
                   OR (entry->>'page_index') !~ '^(0|[1-9][0-9]*)$' THEN
                    RAISE EXCEPTION 'region page_index must be an integer' USING ERRCODE = '23514';
                END IF;
                page := (entry->>'page_index')::numeric;
                IF page < 0 OR page > 2147483647 OR trunc(page) <> page THEN
                    RAISE EXCEPTION 'region page_index outside integer range' USING ERRCODE = '23514';
                END IF;
                pages := array_append(pages, page::integer);
            END LOOP;
            RETURN ARRAY(SELECT DISTINCT n FROM unnest(pages) AS n ORDER BY n);
        END $$
    """)
    op.create_table(
        "segments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("section_id", sa.BigInteger()),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("heading", "prose", "verse", native_enum=False, create_constraint=True, name="kind"),
            nullable=False,
        ),
        sa.Column("html_content", sa.Text(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("language", sa.Text()),
        sa.Column("regions", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "page_indexes",
            pg.ARRAY(sa.Integer()),
            sa.Computed("public.region_pages(regions)", persisted=True),
            nullable=False,
        ),
        sa.Column("narration_start_ms", sa.BigInteger()),
        sa.Column("narration_end_ms", sa.BigInteger()),
        sa.Column("narration_seek_ms", sa.BigInteger()),
        sa.Column("alignment_status", sa.Text()),
        sa.Column("alignment_metadata", pg.JSONB(), nullable=True),
        sa.UniqueConstraint("book_id", "source_key", name="uq_segments_book_source_key"),
        sa.UniqueConstraint("book_id", "sequence", name="uq_segments_book_sequence"),
        sa.ForeignKeyConstraint(["book_id", "section_id"], ["sections.book_id", "sections.id"]),
        sa.CheckConstraint("id > 0", name="positive_id"),
        sa.CheckConstraint("length(source_key) BETWEEN 1 AND 256", name="source_key_length"),
        sa.CheckConstraint("sequence >= 0", name="sequence"),
        sa.CheckConstraint("length(html_content) > 0 AND length(text_content) > 0", name="nonempty_text"),
        sa.CheckConstraint("jsonb_typeof(regions) = 'array'", name="regions_array"),
        sa.CheckConstraint(
            """(narration_start_ms IS NULL AND narration_end_ms IS NULL AND narration_seek_ms IS NULL) OR
            (narration_start_ms IS NOT NULL AND narration_end_ms IS NOT NULL AND narration_start_ms >= 0 AND narration_end_ms > narration_start_ms
             AND (narration_seek_ms IS NULL OR (narration_seek_ms >= 0 AND narration_seek_ms <= narration_start_ms)))""",
            name="timing",
        ),
    )
    op.create_index("ix_segments_page_indexes", "segments", ["page_indexes"], postgresql_using="gin")
    op.create_index("ix_segments_book_id_section_id", "segments", ["book_id", "section_id"])
    op.add_column("files", sa.Column("section_id", sa.BigInteger()))
    op.create_foreign_key(
        op.f("fk_files_section_id_sections"), "files", "sections", ["section_id"], ["id"], ondelete="SET NULL"
    )
    op.create_unique_constraint("uq_files_section_kind", "files", ["section_id", "kind"])
    op.drop_constraint(op.f("ck_files_file_kind"), "files", type_="check")
    op.create_check_constraint(op.f("ck_files_file_kind"), "files", "kind IN ('cover', 'source_pdf', 'audio_chapter')")
    # The original emulated enum uses VARCHAR(10); audio_chapter requires more space.
    op.alter_column("files", "kind", type_=sa.String(13), existing_type=sa.String(10))
    op.drop_constraint(op.f("ck_files_ownership_timestamp"), "files", type_="check")
    op.create_check_constraint(
        op.f("ck_files_ownership_timestamp"),
        "files",
        "(book_id IS NULL AND section_id IS NULL) = (unlinked_at IS NOT NULL)",
    )
    op.create_check_constraint(op.f("ck_files_exclusive_owner"), "files", "book_id IS NULL OR section_id IS NULL")
    op.create_check_constraint(op.f("ck_files_section_kind"), "files", "section_id IS NULL OR kind = 'audio_chapter'")
    ownership_trigger(sections=True)


def downgrade() -> None:
    # Refuse a lossy rollback. Purge/queue audio through the service before downgrading.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM files WHERE kind = 'audio_chapter') THEN
        RAISE EXCEPTION 'Remove audio_chapter files before downgrading content domain'; END IF; END $$""")
    ownership_trigger(sections=False)
    for name in ("section_kind", "exclusive_owner", "ownership_timestamp"):
        op.drop_constraint(op.f("ck_files_" + name), "files", type_="check")
    op.create_check_constraint(
        op.f("ck_files_ownership_timestamp"), "files", "(book_id IS NULL) = (unlinked_at IS NOT NULL)"
    )
    op.drop_constraint("uq_files_section_kind", "files", type_="unique")
    op.drop_constraint(op.f("fk_files_section_id_sections"), "files", type_="foreignkey")
    op.drop_column("files", "section_id")
    op.drop_constraint(op.f("ck_files_file_kind"), "files", type_="check")
    op.alter_column("files", "kind", type_=sa.String(10), existing_type=sa.String(13))
    op.create_check_constraint(op.f("ck_files_file_kind"), "files", "kind IN ('cover', 'source_pdf')")
    op.drop_table("segments")
    op.drop_table("sections")
    op.execute("DROP FUNCTION public.region_pages(jsonb)")
    op.drop_constraint(op.f("ck_books_content_metadata"), "books", type_="check")
    for name in ("content_imported_at", "content_schema_version", "content_version"):
        op.drop_column("books", name)
