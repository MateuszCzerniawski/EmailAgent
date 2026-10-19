"""
SQLAlchemy 2.0 ORM models — dialect-agnostic (SQLite + PostgreSQL).

Type mapping chosen for cross-dialect compatibility:
  UUID        → sa.Uuid(as_uuid=True)       TEXT in SQLite, UUID in PostgreSQL
  TEXT[]      → sa.JSON                     JSON in SQLite, can use ARRAY in PG
  ENUM        → sa.String(50)               TEXT in SQLite, enforced in Python
  TIMESTAMPTZ → sa.DateTime(timezone=True)  TEXT in SQLite, TIMESTAMPTZ in PG

Note: postgres.py uses postgresql.ENUM / postgresql.UUID directly and works
only with PostgreSQL. sqlite_db.py uses these dialect-agnostic ORM models.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    SmallInteger,
    String,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Association table: email_tags (many-to-many)
# ---------------------------------------------------------------------------

email_tags = Table(
    "email_tags",
    Base.metadata,
    Column(
        "email_id",
        Uuid(as_uuid=True),
        ForeignKey("emails.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        Uuid(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# ---------------------------------------------------------------------------
# ORM classes
# ---------------------------------------------------------------------------


class SenderORM(Base):
    __tablename__ = "senders"

    id = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    importance_score: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    relation_type: Mapped[str] = mapped_column(String(50), default="unknown", nullable=False)
    mail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_contact_at = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    threads: Mapped[list[ThreadORM]] = relationship("ThreadORM", back_populates="sender")
    emails: Mapped[list[EmailORM]] = relationship("EmailORM", back_populates="sender")


class ThreadORM(Base):
    __tablename__ = "threads"

    id = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    sender_id = mapped_column(
        Uuid(as_uuid=True), ForeignKey("senders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    mail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at = mapped_column(DateTime(timezone=True), nullable=True)
    last_mail_at = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open", nullable=False)

    sender: Mapped[SenderORM] = relationship("SenderORM", back_populates="threads")
    emails: Mapped[list[EmailORM]] = relationship("EmailORM", back_populates="thread")


class EmailORM(Base):
    __tablename__ = "emails"

    id = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    message_id: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    thread_id = mapped_column(
        Uuid(as_uuid=True), ForeignKey("threads.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sender_id = mapped_column(
        Uuid(as_uuid=True), ForeignKey("senders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    recipients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at = mapped_column(DateTime(timezone=True), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="new", nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedded_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sender: Mapped[SenderORM] = relationship("SenderORM", back_populates="emails")
    thread: Mapped[ThreadORM] = relationship("ThreadORM", back_populates="emails")
    attachments: Mapped[list[AttachmentORM]] = relationship(
        "AttachmentORM", back_populates="email", cascade="all, delete-orphan"
    )
    tags: Mapped[list[TagORM]] = relationship(
        "TagORM", secondary=email_tags, back_populates="emails"
    )
    agent_cache: Mapped[AgentCacheORM | None] = relationship(
        "AgentCacheORM", back_populates="email", uselist=False
    )


class AttachmentORM(Base):
    __tablename__ = "attachments"

    id = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email_id = mapped_column(
        Uuid(as_uuid=True), ForeignKey("emails.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    email: Mapped[EmailORM] = relationship("EmailORM", back_populates="attachments")


class TagORM(Base):
    __tablename__ = "tags"

    id = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    emails: Mapped[list[EmailORM]] = relationship(
        "EmailORM", secondary=email_tags, back_populates="tags"
    )


class AgentCacheORM(Base):
    __tablename__ = "agent_cache"

    id = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email_id = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("emails.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    analysis: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    suggested_action: Mapped[str] = mapped_column(String(50), nullable=False)
    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_used: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    email: Mapped[EmailORM] = relationship("EmailORM", back_populates="agent_cache")


class GuidelineORM(Base):
    __tablename__ = "guidelines"

    id = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    filename: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedded_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
