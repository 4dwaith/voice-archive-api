"""Kind-specific metadata remains valid even when writes bypass Python models."""

import json

import pytest
from psycopg.errors import CheckViolation
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command

VALID_METADATA = {"cover": {"width": 16, "height": 24}, "source_pdf": {"page_count": 3}}
INVALID_METADATA = (
    [(kind, invalid) for kind in VALID_METADATA for invalid in ({}, None, [], "metadata", 1, True)]
    + [
        (kind, {**metadata, field: invalid})
        for kind, metadata in VALID_METADATA.items()
        for field in metadata
        for invalid in (None, 0, -1, 1.5, 1.0, "1", True, [], {}, 2_147_483_648)
    ]
    + [("cover", {"width": 16}), ("cover", {"height": 24})]
)


def insert_file(connection, kind, metadata):
    return connection.scalar(
        text("""INSERT INTO files (kind, storage_key, byte_size, metadata, created_at)
            VALUES (:kind, 'metadata-test', 1, CAST(:metadata AS jsonb), now()) RETURNING id"""),
        {"kind": kind, "metadata": json.dumps(metadata)},
    )


@pytest.mark.parametrize("kind,metadata", INVALID_METADATA)
def test_direct_insert_and_update_reject_invalid_metadata(test_engine, kind, metadata):
    with test_engine.begin() as connection, pytest.raises(IntegrityError) as raised:
        insert_file(connection, kind, metadata)
    assert isinstance(raised.value.orig, CheckViolation)

    valid = VALID_METADATA[kind]
    with test_engine.begin() as connection:
        file_id = insert_file(connection, kind, valid)
    with test_engine.begin() as connection, pytest.raises(IntegrityError) as raised:
        connection.execute(
            text("UPDATE files SET metadata = CAST(:metadata AS jsonb) WHERE id = :id"),
            {"metadata": json.dumps(metadata), "id": file_id},
        )
    assert isinstance(raised.value.orig, CheckViolation)
    with test_engine.connect() as connection:
        assert connection.scalar(text("SELECT metadata FROM files WHERE id = :id"), {"id": file_id}) == valid


@pytest.mark.parametrize("kind", VALID_METADATA)
def test_positive_integer_limits_and_extra_metadata_are_allowed(test_engine, kind):
    metadata = dict.fromkeys(VALID_METADATA[kind], 2_147_483_647) | {"custom": "value"}
    with test_engine.begin() as connection:
        insert_file(connection, kind, metadata)


def test_changing_kind_requires_matching_metadata(test_engine):
    with test_engine.begin() as connection:
        file_id = insert_file(connection, "cover", VALID_METADATA["cover"])
    with test_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(text("UPDATE files SET kind = 'source_pdf' WHERE id = :id"), {"id": file_id})


@pytest.mark.parametrize("kind", VALID_METADATA)
def test_metadata_migration_preserves_valid_existing_rows(test_engine, migration_config, kind):
    command.downgrade(migration_config, "0010_protect_linked_files")
    with test_engine.begin() as connection:
        file_id = insert_file(connection, kind, VALID_METADATA[kind])
    command.upgrade(migration_config, "head")
    with test_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT metadata FROM files WHERE id = :id"), {"id": file_id})
            == VALID_METADATA[kind]
        )


@pytest.mark.parametrize("kind", VALID_METADATA)
def test_metadata_migration_refuses_invalid_existing_rows_transactionally(test_engine, migration_config, kind):
    command.downgrade(migration_config, "0010_protect_linked_files")
    with test_engine.begin() as connection:
        file_id = insert_file(connection, kind, {})
    with pytest.raises(IntegrityError):
        command.upgrade(migration_config, "head")
    with test_engine.connect() as connection:
        assert connection.scalar(text("SELECT metadata FROM files WHERE id = :id"), {"id": file_id}) == {}
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0010_protect_linked_files"
