"""Convert accepted embedding arrays to native pgvector storage without regeneration."""

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0026_pgvector"
down_revision = "0025_batch_state_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.drop_constraint(
        op.f("ck_window_embeddings_window_embedding_vector_dimensions"), "window_embeddings", type_="check"
    )
    op.alter_column(
        "window_embeddings",
        "vector",
        existing_type=postgresql.ARRAY(sa.Double()),
        type_=VECTOR(),
        postgresql_using="vector::vector",
    )
    op.create_check_constraint(
        "window_embedding_vector_dimensions", "window_embeddings", "vector IS NULL OR vector_dims(vector) = dimensions"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_window_embeddings_window_embedding_vector_dimensions"), "window_embeddings", type_="check"
    )
    op.alter_column(
        "window_embeddings",
        "vector",
        existing_type=VECTOR(),
        type_=postgresql.ARRAY(sa.Double()),
        postgresql_using="vector::real[]::double precision[]",
    )
    op.create_check_constraint(
        "window_embedding_vector_dimensions", "window_embeddings", "vector IS NULL OR cardinality(vector) = dimensions"
    )
    # Other database consumers may use this extension. Leave its installation intact.
