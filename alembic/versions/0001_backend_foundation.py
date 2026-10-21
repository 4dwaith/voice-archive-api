"""Establish the initial migration baseline.

Revision ID: 0001_backend_foundation
Revises:
Create Date: 2026-09-07
"""

from collections.abc import Sequence

revision: str = "0001_backend_foundation"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No domain tables exist in phase 01."""


def downgrade() -> None:
    """The baseline has no schema objects to remove."""
