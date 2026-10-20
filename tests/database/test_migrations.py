"""Migration guarantees shared by all application models."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from voice_archive_api.db.base import Base


def test_current_migration_schema_matches_models(test_engine):
    with test_engine.connect() as connection:
        assert compare_metadata(
            MigrationContext.configure(connection), Base.metadata
        ) == []


def test_full_downgrade_and_repeatable_upgrade_to_head(test_engine, migration_config):
    original_tables = set(inspect(test_engine).get_table_names())
    with test_engine.connect() as connection:
        original_heads = MigrationContext.configure(connection).get_current_heads()

    command.downgrade(migration_config, "base")

    # Alembic retains its bookkeeping table; all application tables must be gone.
    assert inspect(test_engine).get_table_names() == ["alembic_version"]
    with test_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == ()

    # The first upgrade restores the schema; repeating it must be a safe no-op.
    for _ in range(2):
        command.upgrade(migration_config, "head")
        assert set(inspect(test_engine).get_table_names()) == original_tables
        with test_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_heads() == original_heads
