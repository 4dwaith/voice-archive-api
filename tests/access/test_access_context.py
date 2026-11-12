"""SQL filters and loaded-resource checks enforce the same ownership rules."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from sqlalchemy import Integer, literal, select, union_all
from sqlalchemy.orm import Session

from voice_archive_api.db.session import create_session_factory
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.access import (
    AccessContext,
    can_access_resource,
    create_owner_filter,
    get_access_context,
    is_public,
)
from voice_archive_api.services.files.service import create_file


@pytest.mark.parametrize(
    "access,expected_owners",
    [
        (None, [None]),
        (AccessContext(user_id=None), [None]),
        (AccessContext(user_id=None, is_write=True), []),
        (AccessContext(user_id=2), [None, 2]),
        (AccessContext(user_id=2, is_write=True), [2]),
        (AccessContext(user_id=None, is_trusted=True), [None, 2, 3]),
        (AccessContext(user_id=None, is_write=True, is_trusted=True), [None, 2, 3]),
    ],
)
def test_sql_and_loaded_resource_access_agree(test_engine, access, expected_owners):
    owners = [None, 2, 3]
    rows = union_all(*(select(literal(owner, Integer).label("owner")) for owner in owners)).subquery()
    with Session(test_engine) as db:
        if access is not None:
            db.info["access"] = access

        loaded_owners = [owner for owner in owners if can_access_resource(db, owner)]
        filtered_owners = list(db.scalars(select(rows.c.owner).where(create_owner_filter(db, rows.c.owner))))

    assert loaded_owners == expected_owners
    assert filtered_owners == expected_owners


def test_session_factories_require_explicit_trust(test_engine):
    request_factory = create_session_factory(test_engine)
    local_factory = create_session_factory(test_engine, access=AccessContext(user_id=None, is_trusted=True))

    with request_factory() as request_db, local_factory() as local_db:
        request_access = get_access_context(request_db)
        local_access = get_access_context(local_db)

    assert request_access == AccessContext(user_id=None)
    assert not request_access.is_trusted
    assert local_access.is_trusted


@pytest.mark.parametrize("is_write,expected", [(False, True), (True, False)])
def test_public_access_is_allowed_only_for_reads(is_write, expected):
    access = AccessContext(user_id=2, is_write=is_write)

    assert is_public(access) is expected


def test_unscoped_upload_is_rejected_before_file_processing():
    storage = Mock()

    with Session() as db, pytest.raises(PermissionError):
        create_file(db, FileKind.COVER, BytesIO(b"unused"), storage, max_bytes=100)

    assert storage.mock_calls == []
