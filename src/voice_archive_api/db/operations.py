"""Database-specific primitives used by application services."""

from datetime import datetime

from psycopg import Error as PostgreSQLError
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import DateTime, func, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement


def acquire_named_transaction_lock(db: Session, resource: str) -> None:
    # PostgreSQL advisory locks coordinate cooperating callers by an application
    # key across connections/processes in this database, rather than locking a
    # table or row. Hash the shared resource name into
    # that key: another transaction requesting the same key waits until we commit
    # or roll back. Connection loss also releases it. Other SQL is not blocked
    # automatically, so every competing claim must acquire this lock before its
    # check-and-write transaction, using a consistent order with any row locks.
    # Hash collisions only serialize unrelated resources; they do not weaken
    # exclusion. Never keep the transaction open across provider/network calls.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:resource))"), {"resource": resource})


def create_wall_clock_expression() -> ColumnElement[datetime]:
    # now() is fixed at transaction start, potentially before a lock wait.
    # Retry boundaries need the actual time when the statement is evaluated.
    expression: ColumnElement[datetime] = func.clock_timestamp(type_=DateTime(timezone=True))
    return expression


def allocate_identity_values(db: Session, table: str, column: str, count: int) -> list[int]:
    values = db.scalars(
        text("SELECT nextval(pg_get_serial_sequence(:table, :column)) FROM generate_series(1, :count)"),
        {"table": table, "column": column, "count": count},
    )
    return list(values)


def read_constraint_name(error: IntegrityError) -> str | None:
    if isinstance(error.orig, UniqueViolation | ForeignKeyViolation):
        return error.orig.diag.constraint_name
    return None


def read_database_error_reason(error: StatementError) -> str:
    """Retain driver diagnostics without exposing SQL statements or parameters."""
    original = error.orig
    primary = getattr(getattr(original, "diag", None), "message_primary", None)
    driver_message = primary or (
        str(original) if isinstance(original, PostgreSQLError) else "Driver message unavailable"
    )
    return f"{type(original).__name__} SQLSTATE={getattr(original, 'sqlstate', None)}: {driver_message}"
