"""Add users and ownership without publishing the existing library."""

import sqlalchemy as sa

from alembic import op

revision = "0028_users_and_ownership"
down_revision = "0027_window_lexical"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("id > 0", name=op.f("ck_users_positive_id")),
        sa.CheckConstraint("email IS NULL OR length(email) > 0", name=op.f("ck_users_email_nonempty")),
        sa.CheckConstraint("email IS NOT NULL OR password_hash IS NULL", name=op.f("ck_users_email_less_password")),
        sa.CheckConstraint(
            "NOT is_enabled OR (email IS NOT NULL AND password_hash IS NOT NULL)",
            name=op.f("ck_users_enabled_credentials"),
        ),
    )
    op.create_index("uq_users_email", "users", [sa.text("lower(email)")], unique=True)
    owner_id = op.get_bind().scalar(
        sa.text("INSERT INTO users (display_name, is_enabled) VALUES ('Unassigned', false) RETURNING id")
    )
    for table in ("series", "books"):
        op.add_column(table, sa.Column("owner_user_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_owner_user_id_users", table, "users", ["owner_user_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_index(f"ix_{table}_owner_user_id", table, ["owner_user_id"])
        op.get_bind().execute(sa.text(f"UPDATE {table} SET owner_user_id = :owner_id"), {"owner_id": owner_id})
        op.add_column(
            table,
            sa.Column(
                "ownership_key",
                sa.BigInteger(),
                sa.Computed("coalesce(owner_user_id, 0)", persisted=True),
                nullable=False,
            ),
        )
    op.create_unique_constraint("uq_series_id_ownership", "series", ["id", "ownership_key"])
    op.create_foreign_key(
        "fk_books_series_ownership",
        "books",
        "series",
        ["series_id", "ownership_key"],
        ["id", "ownership_key"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_index("uq_books_identity", table_name="books")
    op.create_index(
        "uq_books_identity",
        "books",
        [
            "owner_user_id",
            sa.text("digest(normalized_title, 'sha256')"),
            sa.text("digest(normalized_author, 'sha256')"),
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    # Restore the old uniqueness first: duplicates across owners must fail the
    # downgrade atomically, rather than losing ownership and account data.
    op.drop_index("uq_books_identity", table_name="books")
    op.create_index(
        "uq_books_identity",
        "books",
        [sa.text("digest(normalized_title, 'sha256')"), sa.text("digest(normalized_author, 'sha256')")],
        unique=True,
    )
    op.drop_constraint("fk_books_series_ownership", "books", type_="foreignkey")
    op.drop_constraint("uq_series_id_ownership", "series", type_="unique")
    for table in ("books", "series"):
        op.drop_column(table, "ownership_key")
        op.drop_index(f"ix_{table}_owner_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_owner_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "owner_user_id")
    op.drop_table("users")
