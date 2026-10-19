"""
Abstract base class for the vector database layer.

All concrete implementations (ChromaVectorDB, etc.) must implement every
abstract method. Callers receive domain model objects — never raw Chroma dicts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from database.models.domain import (
    ChromaDocumentItem,
    EmailModel,
    GuidelineModel,
    SearchResult,
    SenderModel,
    ThreadModel,
)


class VectorDB(ABC):
    """Abstract interface for the vector database layer."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Initialize the vector DB client and collections."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Release any resources held by the vector DB client."""

    # ------------------------------------------------------------------
    # Upserts
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_email_chunks(self, email: EmailModel) -> None:
        """
        Split email.body into chunks and embed each chunk into the
        email_chunks collection. Any previous chunks for this email are
        replaced (delete + re-insert).
        """

    @abstractmethod
    async def upsert_thread_summary(self, thread: ThreadModel) -> None:
        """Embed thread.summary into the thread_summaries collection."""

    @abstractmethod
    async def upsert_sender_summary(self, sender: SenderModel) -> None:
        """Embed sender.summary into the sender_summaries collection."""

    @abstractmethod
    async def upsert_guideline_chunks(self, guideline: GuidelineModel) -> None:
        """
        Split guideline.content into chunks and embed each chunk into the
        guideline_chunks collection.
        """

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @abstractmethod
    async def search_emails(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """Semantic search over email_chunks. Supports Chroma where-filters."""

    @abstractmethod
    async def search_guidelines(
        self,
        query: str,
        top_k: int = 3,
    ) -> list[SearchResult]:
        """Semantic search over guideline_chunks."""

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    @abstractmethod
    async def delete_email_chunks(self, email_id: UUID) -> None:
        """Remove all chunks for the given email from email_chunks."""

    @abstractmethod
    async def delete_guideline_chunks(self, guideline_id: UUID) -> None:
        """Remove all chunks for the given guideline from guideline_chunks."""

    # ------------------------------------------------------------------
    # Sync support
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_all_email_ids_in_chroma(self) -> set[str]:
        """
        Return all unique email_id values stored in the email_chunks
        collection metadata. Used by DatabaseManager.sync_check().
        """

    # ------------------------------------------------------------------
    # Listing / inspection
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_collections(self) -> list[dict]:
        """Return a list of {name, count} dicts for all managed collections."""

    @abstractmethod
    async def get_thread_chunks(self, thread_id: UUID) -> list[ChromaDocumentItem]:
        """
        Return all email chunks belonging to a thread, ordered by received_at ascending.
        Used to build conversation history context for RAG-augmented summaries.
        """

    @abstractmethod
    async def list_email_chunks(
        self,
        limit: int = 20,
        offset: int = 0,
        email_id: str | None = None,
    ) -> list[ChromaDocumentItem]:
        """Return paginated documents from the email_chunks collection."""

    @abstractmethod
    async def list_guideline_chunks(
        self,
        limit: int = 20,
        offset: int = 0,
        guideline_id: str | None = None,
    ) -> list[ChromaDocumentItem]:
        """Return paginated documents from the guideline_chunks collection."""

    @abstractmethod
    async def list_thread_summaries(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChromaDocumentItem]:
        """Return paginated documents from the thread_summaries collection."""

    @abstractmethod
    async def list_sender_summaries(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChromaDocumentItem]:
        """Return paginated documents from the sender_summaries collection."""

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify connectivity to the vector DB."""

    @abstractmethod
    async def clear_all(self) -> None:
        """Delete all documents from every managed collection."""
