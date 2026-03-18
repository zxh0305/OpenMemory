"""add raw memory inputs table

Revision ID: add_raw_memory_inputs_table
Revises: create_archived_memories_table
Create Date: 2026-03-17 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_raw_memory_inputs_table"
down_revision = "create_archived_memories_table"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "raw_memory_inputs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("app_id", sa.String(length=32), sa.ForeignKey("apps.id"), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("extracted_facts", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("infer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processing_status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("idx_raw_memory_inputs_user_id", "raw_memory_inputs", ["user_id"])
    op.create_index("idx_raw_memory_inputs_app_id", "raw_memory_inputs", ["app_id"])
    op.create_index("idx_raw_memory_inputs_status", "raw_memory_inputs", ["processing_status"])
    op.create_index("idx_raw_memory_inputs_processed_at", "raw_memory_inputs", ["processed_at"])
    op.create_index("idx_raw_memory_inputs_created_at", "raw_memory_inputs", ["created_at"])
    op.create_index("idx_raw_memory_user_time", "raw_memory_inputs", ["user_id", "created_at"])
    op.create_index("idx_raw_memory_app_time", "raw_memory_inputs", ["app_id", "created_at"])


def downgrade():
    op.drop_index("idx_raw_memory_app_time", table_name="raw_memory_inputs")
    op.drop_index("idx_raw_memory_user_time", table_name="raw_memory_inputs")
    op.drop_index("idx_raw_memory_inputs_created_at", table_name="raw_memory_inputs")
    op.drop_index("idx_raw_memory_inputs_processed_at", table_name="raw_memory_inputs")
    op.drop_index("idx_raw_memory_inputs_status", table_name="raw_memory_inputs")
    op.drop_index("idx_raw_memory_inputs_app_id", table_name="raw_memory_inputs")
    op.drop_index("idx_raw_memory_inputs_user_id", table_name="raw_memory_inputs")
    op.drop_table("raw_memory_inputs")
