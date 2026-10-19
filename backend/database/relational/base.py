"""
Abstract base class for the relational database layer.

All concrete implementations must implement every abstract method.
Callers receive domain model objects — never ORM instances.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from database.models.domain import (
    AgentCacheModel,
    EmailModel,
    GuidelineModel,
    SenderModel,
    TagModel,
    ThreadModel,
)


class RelationalDB(ABC):
    """Abstract interface for the relational database layer."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Establish the database connection / engine pool."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Dispose of the connection pool and clean up resources."""

    # ------------------------------------------------------------------
    # Senders
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_sender(self, sender: SenderModel) -> SenderModel:
        """
        Insert a new sender or update an existing one (keyed on email).
        Returns the persisted model with the assigned UUID.
        """

    @abstractmethod
    async def get_sender_by_email(self, email: str) -> SenderModel | None:
        """Return the sender with the given email address, or None."""

    @abstractmethod
    async def get_sender(self, sender_id: UUID) -> SenderModel | None:
        """Return the sender with the given UUID, or None."""

    @abstractmethod
    async def update_sender_summary(self, sender_id: UUID, summary: str) -> None:
        """Overwrite the LLM-generated summary for a sender."""

    @abstractmethod
    async def list_senders(self, limit: int = 50, offset: int = 0) -> list[SenderModel]:
        """Return a paginated list of all senders."""

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_thread(self, thread: ThreadModel) -> ThreadModel:
        """
        Insert a new thread or update an existing one.
        When id is None, a new thread is created; otherwise the existing
        thread (by id) is updated.
        Returns the persisted model with the assigned UUID.
        """

    @abstractmethod
    async def get_thread(self, thread_id: UUID) -> ThreadModel | None:
        """Return the thread with the given UUID, or None."""

    @abstractmethod
    async def get_thread_by_gmail_id(self, gmail_thread_id: str) -> ThreadModel | None:
        """Return the thread with the given Gmail thread ID, or None."""

    @abstractmethod
    async def merge_thread_into(self, source_thread_id: UUID, target_thread_id: UUID) -> None:
        """
        Move all emails from source_thread to target_thread, update target's
        mail_count and date range, then delete source_thread.
        """

    @abstractmethod
    async def update_thread_summary(self, thread_id: UUID, summary: str) -> None:
        """Overwrite the LLM-generated summary for a thread."""

    @abstractmethod
    async def list_threads(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        sender_id: UUID | None = None,
    ) -> list[ThreadModel]:
        """Return a paginated list of threads, optionally filtered by status and/or sender."""

    # ------------------------------------------------------------------
    # Emails
    # ------------------------------------------------------------------

    @abstractmethod
    async def insert_email_atomic(
        self,
        sender: SenderModel,
        thread_subject: str,
        email: EmailModel,
        received_at: datetime,
        gmail_thread_id: str | None = None,
    ) -> tuple[SenderModel, ThreadModel, EmailModel]:
        """
        Upsert sender, find-or-create thread, and insert email — all within a
        single transaction. Any failure rolls back all three writes atomically.

        Thread lookup: by gmail_thread_id when provided, otherwise by
        (sender_id, subject) for backward compatibility.

        Returns (sender, thread, email) domain models with assigned UUIDs.
        Raises DuplicateEmailError if email.message_id already exists.
        """

    @abstractmethod
    async def insert_email(self, email: EmailModel) -> EmailModel:
        """
        Insert a new email record.
        Raises DuplicateEmailError if message_id already exists.
        Returns the persisted model with the assigned UUID.
        """

    @abstractmethod
    async def get_email_by_message_id(self, message_id: str) -> EmailModel | None:
        """Return the email with the given RFC 2822 Message-ID, or None."""

    @abstractmethod
    async def get_email(self, email_id: UUID) -> EmailModel | None:
        """Return the email with the given UUID, or None."""

    @abstractmethod
    async def mark_as_embedded(self, email_id: UUID, model_name: str) -> None:
        """Set embedded_at = now() and embedding_model on the email record."""

    @abstractmethod
    async def get_unembedded_emails(self, limit: int = 100) -> list[EmailModel]:
        """Return emails where embedded_at IS NULL, ordered by received_at DESC."""

    @abstractmethod
    async def get_emails_by_sender(
        self, sender_id: UUID, since: datetime | None = None
    ) -> list[EmailModel]:
        """Return all emails from a sender, optionally filtered by date."""

    @abstractmethod
    async def get_latest_email_per_thread(self, thread_ids: list[UUID]) -> dict[UUID, EmailModel]:
        """Return a mapping thread_id → most recent EmailModel for each given thread."""

    @abstractmethod
    async def get_senders_by_ids(self, sender_ids: list[UUID]) -> dict[UUID, SenderModel]:
        """Return a sender lookup map for the given IDs."""

    @abstractmethod
    async def list_emails(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        sender_id: UUID | None = None,
        thread_id: UUID | None = None,
    ) -> list[EmailModel]:
        """Return a paginated, optionally filtered list of emails."""

    # ------------------------------------------------------------------
    # Agent Cache
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_agent_cache(self, cache: AgentCacheModel) -> AgentCacheModel:
        """Insert or update an agent cache entry (keyed on email_id)."""

    @abstractmethod
    async def get_agent_cache(self, email_id: UUID) -> AgentCacheModel | None:
        """Return the agent cache entry for the given email, or None."""

    @abstractmethod
    async def get_agent_caches_by_email_ids(self, email_ids: list[UUID]) -> dict[UUID, AgentCacheModel]:
        """Return a mapping email_id → AgentCacheModel for the given email IDs."""

    @abstractmethod
    async def list_agent_caches(self, limit: int = 2000) -> list[AgentCacheModel]:
        """Return all agent cache entries up to the given limit."""

    @abstractmethod
    async def update_suggested_reply(self, email_id: UUID, reply: str) -> None:
        """Update only the suggested_reply field in agent_cache for the given email."""

    @abstractmethod
    async def update_suggested_tags(self, email_id: UUID, tags: list[str]) -> None:
        """Update only the suggested_tags field in agent_cache for the given email."""

    @abstractmethod
    async def update_importance_score(self, email_id: UUID, importance_score: int) -> None:
        """Update only the importance_score field in agent_cache for the given email."""

    # ------------------------------------------------------------------
    # Guidelines
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_guideline(self, guideline: GuidelineModel) -> GuidelineModel:
        """
        Insert a new guideline or update an existing one (keyed on filename).
        Increments version on update.
        """

    @abstractmethod
    async def get_guideline(self, guideline_id: UUID) -> GuidelineModel | None:
        """Return the guideline with the given UUID, or None."""

    @abstractmethod
    async def get_unembedded_guidelines(self) -> list[GuidelineModel]:
        """Return guidelines where embedded_at IS NULL."""

    @abstractmethod
    async def mark_guideline_as_embedded(
        self, guideline_id: UUID, model_name: str
    ) -> None:
        """Set embedded_at = now() and embedding_model on the guideline record."""

    @abstractmethod
    async def list_guidelines(
        self, limit: int = 50, offset: int = 0
    ) -> list[GuidelineModel]:
        """Return a paginated list of all guidelines."""

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_tags(self) -> list[TagModel]:
        """Return all tags."""

    @abstractmethod
    async def insert_tag(self, name: str) -> TagModel:
        """Insert a new tag. Raises ValueError if the name already exists."""

    @abstractmethod
    async def get_tags_by_sender(self, sender_id: UUID) -> list[TagModel]:
        """Return deduplicated tags for all emails from a given sender, ordered by name."""

    # ------------------------------------------------------------------
    # Deletions
    # ------------------------------------------------------------------

    @abstractmethod
    async def delete_email(self, email_id: UUID) -> bool:
        """Delete email by UUID. Returns True if deleted, False if not found."""

    @abstractmethod
    async def delete_sender(self, sender_id: UUID) -> bool:
        """Delete sender by UUID. Returns True if deleted, False if not found."""

    @abstractmethod
    async def delete_thread(self, thread_id: UUID) -> bool:
        """Delete thread by UUID. Returns True if deleted, False if not found."""

    @abstractmethod
    async def delete_guideline(self, guideline_id: UUID) -> bool:
        """Delete guideline by UUID. Returns True if deleted, False if not found."""

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @abstractmethod
    async def health_check(self) -> bool:
        """Execute a trivial query (SELECT 1) to verify DB connectivity."""

    @abstractmethod
    async def get_all_email_ids(self) -> set[str]:
        """Return all email UUIDs (as strings) from the emails table."""

    @abstractmethod
    async def clear_all(self) -> None:
        """Delete all rows from every managed table (emails, senders, threads, guidelines, agent_cache, tags)."""
