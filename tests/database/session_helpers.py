"""Explicit trusted sessions for tests of local imports and maintenance jobs."""

from sqlalchemy.orm import Session

from voice_archive_api.services.access import AccessContext


def create_trusted_session(bind, **options):
    return Session(bind, info={"access": AccessContext(user_id=None, is_trusted=True)}, **options)
