"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-04-08

Creates all 8 tables, 5 ENUM types, and all required indexes for the
email Email Agent relational database.

Table creation order respects FK dependencies:
  senders → threads → emails → attachments → tags → email_tags
  → agent_cache → guidelines
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. ENUM types (must exist before the tables that reference them)
    # ------------------------------------------------------------------
    op.execute("CREATE TYPE relation_type AS ENUM ('internal', 'client', 'vip', 'unknown')")
    op.execute("CREATE TYPE thread_status AS ENUM ('open', 'closed', 'pending')")
    op.execute("CREATE TYPE email_status AS ENUM ('new', 'in_progress', 'resolved', 'archived')")
    op.execute("CREATE TYPE suggested_action AS ENUM ('reply', 'archive', 'escalate', 'ignore')")
    op.execute("CREATE TYPE file_type AS ENUM ('markdown', 'pdf')")

    # ------------------------------------------------------------------
    # 2. senders
    # ------------------------------------------------------------------
    op.create_table(
        "senders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("importance_score", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column(
            "relation_type",
            postgresql.ENUM(name="relation_type", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("mail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_contact_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_senders_email", "senders", ["email"], unique=True)

    # ------------------------------------------------------------------
    # 3. threads
    # ------------------------------------------------------------------
    op.create_table(
        "threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "sender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("senders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_mail_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="thread_status", create_type=False),
            nullable=False,
            server_default="open",
        ),
    )

    # ------------------------------------------------------------------
    # 4. emails
    # ------------------------------------------------------------------
    op.create_table(
        "emails",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", sa.String(), nullable=False, unique=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("threads.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "sender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("senders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recipients", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_important", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "status",
            postgresql.ENUM(name="email_status", create_type=False),
            nullable=False,
            server_default="new",
        ),
        sa.Column("embedding_model", sa.String(), nullable=True),
        sa.Column("embedded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_emails_sender_id", "emails", ["sender_id"])
    op.create_index("ix_emails_thread_id", "emails", ["thread_id"])
    op.create_index("ix_emails_received_at", "emails", [sa.text("received_at DESC")])
    op.create_index("ix_emails_status", "emails", ["status"])
    op.create_index(
        "ix_emails_read_important",
        "emails",
        ["is_read", "is_important"],
        postgresql_where=text("status != 'archived'"),
    )

    # ------------------------------------------------------------------
    # 5. attachments
    # ------------------------------------------------------------------
    op.create_table(
        "attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "email_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
    )

    # ------------------------------------------------------------------
    # 6. tags
    # ------------------------------------------------------------------
    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
    )

    # ------------------------------------------------------------------
    # 7. email_tags (association)
    # ------------------------------------------------------------------
    op.create_table(
        "email_tags",
        sa.Column(
            "email_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("emails.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ------------------------------------------------------------------
    # 8. agent_cache
    # ------------------------------------------------------------------
    op.create_table(
        "agent_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "email_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("emails.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("analysis", sa.Text(), nullable=False),
        sa.Column("suggested_reply", sa.Text(), nullable=True),
        sa.Column("suggested_tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "suggested_action",
            postgresql.ENUM(name="suggested_action", create_type=False),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("model_used", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_agent_cache_email_id", "agent_cache", ["email_id"], unique=True)

    # ------------------------------------------------------------------
    # 9. guidelines
    # ------------------------------------------------------------------
    op.create_table(
        "guidelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(), nullable=False, unique=True),
        sa.Column(
            "file_type",
            postgresql.ENUM(name="file_type", create_type=False),
            nullable=False,
        ),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=True),
        sa.Column("embedded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("guidelines")
    op.drop_index("ix_agent_cache_email_id", table_name="agent_cache")
    op.drop_table("agent_cache")
    op.drop_table("email_tags")
    op.drop_table("tags")
    op.drop_table("attachments")
    op.drop_index("ix_emails_read_important", table_name="emails")
    op.drop_index("ix_emails_status", table_name="emails")
    op.drop_index("ix_emails_received_at", table_name="emails")
    op.drop_index("ix_emails_thread_id", table_name="emails")
    op.drop_index("ix_emails_sender_id", table_name="emails")
    op.drop_table("emails")
    op.drop_table("threads")
    op.drop_index("ix_senders_email", table_name="senders")
    op.drop_table("senders")

    # Drop ENUMs in reverse order
    op.execute("DROP TYPE IF EXISTS file_type")
    op.execute("DROP TYPE IF EXISTS suggested_action")
    op.execute("DROP TYPE IF EXISTS email_status")
    op.execute("DROP TYPE IF EXISTS thread_status")
    op.execute("DROP TYPE IF EXISTS relation_type")
