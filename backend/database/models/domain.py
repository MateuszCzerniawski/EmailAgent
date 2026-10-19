"""
Pydantic domain models (DTOs) for the database layer.

These are the objects passed between all layers — routers, manager, relational DB,
and vector DB. ORM instances never escape the relational/ package; they are always
converted to these models before being returned to callers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# ENUM literals
# ---------------------------------------------------------------------------

RelationType = Literal["internal", "client", "vip", "unknown"]
ThreadStatus = Literal["open", "closed", "pending"]
EmailStatus = Literal["new", "in_progress", "resolved", "archived"]
SuggestedAction = Literal["reply", "archive", "escalate", "ignore"]
FileType = Literal["markdown", "pdf"]


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class SenderModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    email: str
    display_name: str | None = None
    importance_score: int = Field(default=3, ge=1, le=5)
    relation_type: RelationType = "unknown"
    mail_count: int = 0
    last_contact_at: datetime | None = None
    summary: str | None = None
    summary_updated_at: datetime | None = None
    created_at: datetime | None = None


class ThreadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    gmail_thread_id: str | None = None
    subject: str
    sender_id: UUID
    mail_count: int = 0
    started_at: datetime | None = None
    last_mail_at: datetime | None = None
    summary: str | None = None
    summary_updated_at: datetime | None = None
    status: ThreadStatus = "open"


class EmailModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    message_id: str
    thread_id: UUID | None = None
    sender_id: UUID | None = None
    recipients: list[str] = Field(default_factory=list)
    subject: str
    body: str
    body_html: str | None = None
    received_at: datetime
    is_read: bool = False
    is_important: bool = False
    status: EmailStatus = "new"
    embedding_model: str | None = None
    embedded_at: datetime | None = None
    created_at: datetime | None = None


class AttachmentModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    email_id: UUID
    filename: str
    mime_type: str
    size_bytes: int
    storage_path: str


class TagModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    name: str


class ActionItem(BaseModel):
    task: str
    deadline: str | None = None


class AgentCacheModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    email_id: UUID
    analysis: str
    suggested_reply: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    suggested_action: SuggestedAction = "ignore"
    actions: list[ActionItem] = Field(default_factory=list)
    summary: str | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    importance_score: int | None = Field(default=None, ge=1, le=5)
    model_used: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GuidelineModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    filename: str
    file_type: FileType
    category: str
    version: int = 1
    content: str
    storage_path: str
    embedding_model: str | None = None
    embedded_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Vector DB result types
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """A single result from a semantic vector search."""

    source: str
    document: str
    score: float
    metadata: dict


class ChromaDocumentItem(BaseModel):
    """A single document returned from a Chroma collection listing endpoint."""

    id: str
    document_snippet: str
    metadata: dict


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


class RefreshReport(BaseModel):
    emails_reembedded: int
    guidelines_reembedded: int
    errors: list[str]
    started_at: datetime
    completed_at: datetime


class SyncReport(BaseModel):
    missing_in_chroma: list[str]
    orphaned_in_chroma: list[str]
    total_in_postgres: int
    total_in_chroma: int
    checked_at: datetime
