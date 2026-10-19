"""
DatabaseManager — orchestrates the relational and vector (ChromaDB) database layers.

Design rules (from DB&RAG_PLAN.md):
  - Relational DB is the source of truth — never delete from relational based on Chroma state.
  - Chroma is a derived index — can always be rebuilt from the relational DB.
  - On dual-write failure: log error, mark embedded_at = NULL, let refresh() fix it.
  - message_id must be unique — DuplicateEmailError is raised on conflict.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

_RE_SUBJECT_PREFIX = re.compile(r"^(re|fwd?|fw)\s*:\s*", re.IGNORECASE)


def _normalize_subject(subject: str) -> str:
    """Strip reply/forward prefixes (Re:, Fwd:, Fw:) recursively."""
    while True:
        stripped = _RE_SUBJECT_PREFIX.sub("", subject).strip()
        if stripped == subject:
            return subject
        subject = stripped

from database.exceptions import DuplicateEmailError
from database.models.domain import (
    EmailModel,
    GuidelineModel,
    RefreshReport,
    SearchResult,
    SenderModel,
    SyncReport,
    ThreadModel,
)
from database.relational.base import RelationalDB
from database.vector.base import VectorDB

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    High-level façade that coordinates all database operations across the
    relational (PostgreSQL) and vector (ChromaDB) stores.

    Usage:
        manager = DatabaseManager(relational_db, vector_db)
        await manager.startup()
        email = await manager.ingest_email(raw_dict)
        results = await manager.search("query string")
        await manager.shutdown()
    """

    def __init__(self, relational: RelationalDB, vector: VectorDB) -> None:
        self._relational = relational
        self._vector = vector

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Connect both database layers. Called once during app startup."""
        await self._relational.connect()
        await self._vector.connect()
        logger.info("DatabaseManager started: RelationalDB + ChromaDB connected")

    async def shutdown(self) -> None:
        """Disconnect both database layers. Called once during app shutdown."""
        await self._relational.disconnect()
        await self._vector.disconnect()
        logger.info("DatabaseManager shut down")

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest_email(self, raw: dict) -> EmailModel:
        """
        Validate, deduplicate, persist to relational DB, then embed to ChromaDB.

        Expected keys in raw:
          message_id (str)       — RFC 2822 Message-ID header (must be unique)
          thread_subject (str)   — Subject used to find/create the thread
          sender_email (str)
          sender_name (str|None)
          subject (str)
          body (str)
          body_html (str|None)
          recipients (list[str])
          received_at (datetime) — timezone-aware preferred; naive treated as UTC
          has_attachments (bool)

        Raises:
          ValueError           if required fields are missing or invalid.
          DuplicateEmailError  if message_id already exists.
        """
        # 1. Validate before any DB writes
        if not raw.get("message_id", "").strip():
            raise ValueError("'message_id' is required and must be non-empty")
        if not raw.get("sender_email", "").strip():
            raise ValueError("'sender_email' is required")
        if not isinstance(raw.get("recipients"), list):
            raise ValueError("'recipients' must be a list of strings")

        message_id: str = raw["message_id"]

        # 2. Deduplicate
        existing = await self._relational.get_email_by_message_id(message_id)
        if existing:
            raise DuplicateEmailError(message_id)

        # 3. Normalise received_at
        received_at: datetime = raw["received_at"]
        if isinstance(received_at, str):
            from dateutil.parser import parse as parse_dt
            received_at = parse_dt(received_at)
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)

        raw_subject = raw.get("thread_subject") or raw.get("subject", "(no subject)")
        thread_subject = _normalize_subject(raw_subject)
        gmail_thread_id: str | None = raw.get("gmail_thread_id") or None

        # 4. Upsert sender + find/create thread + insert email — one atomic transaction
        _, __, email = await self._relational.insert_email_atomic(
            sender=SenderModel(
                email=raw["sender_email"],
                display_name=raw.get("sender_name"),
                last_contact_at=received_at,
            ),
            thread_subject=thread_subject,
            gmail_thread_id=gmail_thread_id,
            email=EmailModel(
                message_id=message_id,
                recipients=raw.get("recipients", []),
                subject=raw.get("subject", "(no subject)"),
                body=raw.get("body", ""),
                body_html=raw.get("body_html"),
                received_at=received_at,
                is_read=raw.get("is_read", False),
                is_important=raw.get("is_important", False),
                status="new",
            ),
            received_at=received_at,
        )

        # 5. Embed to ChromaDB (non-fatal on failure)
        try:
            await self._vector.upsert_email_chunks(email)
            await self._relational.mark_as_embedded(email.id, self._vector._embedding_model)
            logger.debug("Email %s embedded successfully", email.id)
        except Exception as exc:
            logger.error(
                "ChromaDB embedding failed for email %s: %s — "
                "email is in relational DB but embedded_at=NULL; refresh() will retry.",
                email.id,
                exc,
            )

        return email

    async def ingest_guideline(self, file_path: str) -> GuidelineModel:
        """
        Extract text from a guideline file (PDF or Markdown), persist to
        PostgreSQL, then embed chunks to ChromaDB.

        Args:
            file_path: Absolute or relative path to the file on disk.

        Returns:
            The persisted GuidelineModel (with assigned UUID).
        """
        path = Path(file_path).resolve()
        filename = path.name
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            file_type: str = "pdf"
            content = self._extract_pdf_text(path)
        elif suffix in (".md", ".markdown"):
            file_type = "markdown"
            content = path.read_text(encoding="utf-8")
        else:
            raise ValueError(
                f"Unsupported guideline file type '{suffix}'. "
                "Only .pdf and .md/.markdown are supported."
            )

        if not content.strip():
            raise ValueError(f"Guideline file '{filename}' is empty or could not be read.")

        # Determine category from parent directory name (convention) or default
        category = path.parent.name if path.parent.name not in (".", "") else "general"

        guideline = await self._relational.upsert_guideline(
            GuidelineModel(
                filename=filename,
                file_type=file_type,
                category=category,
                content=content,
                storage_path=str(path),
            )
        )

        try:
            await self._vector.upsert_guideline_chunks(guideline)
            await self._relational.mark_guideline_as_embedded(
                guideline.id, self._vector._embedding_model
            )
        except Exception as exc:
            logger.error(
                "ChromaDB embedding failed for guideline %s: %s", guideline.id, exc
            )

        return guideline

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        import pdfplumber
        pages: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n\n".join(pages)

    # ------------------------------------------------------------------
    # Sync & refresh
    # ------------------------------------------------------------------

    async def refresh(self) -> RefreshReport:
        """
        Find all emails/guidelines with embedded_at IS NULL and re-embed them.
        Returns a RefreshReport with counts and any errors.
        """
        started_at = datetime.now(timezone.utc)
        emails_reembedded = 0
        guidelines_reembedded = 0
        errors: list[str] = []

        # Re-embed emails
        emails = await self._relational.get_unembedded_emails(limit=500)
        for email in emails:
            try:
                await self._vector.upsert_email_chunks(email)
                await self._relational.mark_as_embedded(
                    email.id, self._vector._embedding_model
                )
                emails_reembedded += 1
            except Exception as exc:
                msg = f"email {email.id}: {exc}"
                errors.append(msg)
                logger.error("refresh() failed for %s", msg)

        # Re-embed guidelines
        guidelines = await self._relational.get_unembedded_guidelines()
        for guideline in guidelines:
            try:
                await self._vector.upsert_guideline_chunks(guideline)
                await self._relational.mark_guideline_as_embedded(
                    guideline.id, self._vector._embedding_model
                )
                guidelines_reembedded += 1
            except Exception as exc:
                msg = f"guideline {guideline.id}: {exc}"
                errors.append(msg)
                logger.error("refresh() failed for %s", msg)

        # Re-index task chunks from all agent_cache entries that have actions
        all_caches = await self._relational.list_agent_caches()
        for cache in all_caches:
            if not cache.actions:
                continue
            email = await self._relational.get_email(cache.email_id)
            if email is None:
                continue
            try:
                await self._vector.upsert_task_chunks(
                    email_id=cache.email_id,
                    tasks=cache.actions,
                    subject=email.subject or "",
                    sender_id=str(email.sender_id) if email.sender_id else "",
                    received_at=email.received_at.isoformat() if email.received_at else "",
                )
            except Exception as exc:
                msg = f"task chunks for email {cache.email_id}: {exc}"
                errors.append(msg)
                logger.error("refresh() failed for %s", msg)

        completed_at = datetime.now(timezone.utc)
        logger.info(
            "refresh() complete: %d emails, %d guidelines re-embedded, %d errors",
            emails_reembedded,
            guidelines_reembedded,
            len(errors),
        )
        return RefreshReport(
            emails_reembedded=emails_reembedded,
            guidelines_reembedded=guidelines_reembedded,
            errors=errors,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def sync_check(self) -> SyncReport:
        """
        Compare PostgreSQL email IDs against Chroma email_id metadata values.

        Returns a SyncReport listing:
          - missing_in_chroma: PG IDs not found in Chroma
          - orphaned_in_chroma: Chroma IDs not found in PG
        """
        checked_at = datetime.now(timezone.utc)

        relational_ids = await self._relational.get_all_email_ids()
        chroma_ids = await self._vector.get_all_email_ids_in_chroma()

        missing_in_chroma = sorted(relational_ids - chroma_ids)
        orphaned_in_chroma = sorted(chroma_ids - relational_ids)

        logger.info(
            "sync_check(): relational=%d, Chroma=%d, missing=%d, orphaned=%d",
            len(relational_ids),
            len(chroma_ids),
            len(missing_in_chroma),
            len(orphaned_in_chroma),
        )
        return SyncReport(
            missing_in_chroma=missing_in_chroma,
            orphaned_in_chroma=orphaned_in_chroma,
            total_in_postgres=len(relational_ids),
            total_in_chroma=len(chroma_ids),
            checked_at=checked_at,
        )

    async def full_resync(self) -> None:
        """
        Run sync_check(), then:
          - Re-embed emails missing in Chroma.
          - Delete orphaned Chroma chunks (no matching PG record).
        """
        report = await self.sync_check()

        # Re-embed missing
        for email_id_str in report.missing_in_chroma:
            try:
                email = await self._relational.get_email(UUID(email_id_str))
                if email:
                    await self._vector.upsert_email_chunks(email)
                    await self._relational.mark_as_embedded(
                        email.id, self._vector._embedding_model
                    )
                    logger.info("full_resync(): re-embedded email %s", email_id_str)
            except Exception as exc:
                logger.error("full_resync(): failed to re-embed email %s: %s", email_id_str, exc)

        # Delete orphaned
        for email_id_str in report.orphaned_in_chroma:
            try:
                await self._vector.delete_email_chunks(UUID(email_id_str))
                logger.info("full_resync(): deleted orphaned chunks for %s", email_id_str)
            except Exception as exc:
                logger.error(
                    "full_resync(): failed to delete orphaned chunks for %s: %s",
                    email_id_str,
                    exc,
                )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def upsert_tasks_for_email(
        self,
        email_id: UUID,
        tasks: list,
        subject: str = "",
        sender_id: str = "",
        received_at: str = "",
    ) -> None:
        if self._vector is None:
            return
        await self._vector.upsert_task_chunks(
            email_id=email_id,
            tasks=tasks,
            subject=subject,
            sender_id=sender_id,
            received_at=received_at,
        )

    async def search_tasks(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if self._vector is None:
            return []
        return await self._vector.search_tasks(query, top_k=top_k)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """
        Semantic search across email_chunks and guideline_chunks.

        Results from both collections are merged and sorted by score (descending).
        Returns at most top_k results.
        """
        guideline_k = max(1, top_k // 2)

        email_results = await self._vector.search_emails(query, top_k, filters)
        guideline_results = await self._vector.search_guidelines(query, guideline_k)

        combined = email_results + guideline_results
        combined.sort(key=lambda r: r.score, reverse=True)
        return combined[:top_k]

    # ------------------------------------------------------------------
    # Deletions
    # ------------------------------------------------------------------

    async def delete_email(self, email_id: UUID) -> bool:
        """Delete email from relational DB and its chunks from ChromaDB."""
        deleted = await self._relational.delete_email(email_id)
        if deleted:
            try:
                await self._vector.delete_email_chunks(email_id)
            except Exception as exc:
                logger.warning("Chroma chunk deletion failed for email %s: %s", email_id, exc)
        return deleted

    async def delete_guideline(self, guideline_id: UUID) -> bool:
        """Delete guideline from relational DB and its chunks from ChromaDB."""
        deleted = await self._relational.delete_guideline(guideline_id)
        if deleted:
            try:
                await self._vector.delete_guideline_chunks(guideline_id)
            except Exception as exc:
                logger.warning(
                    "Chroma chunk deletion failed for guideline %s: %s", guideline_id, exc
                )
        return deleted

    async def delete_sender(self, sender_id: UUID) -> bool:
        """Delete sender from relational DB."""
        return await self._relational.delete_sender(sender_id)

    async def delete_thread(self, thread_id: UUID) -> bool:
        """Delete thread from relational DB."""
        return await self._relational.delete_thread(thread_id)

    async def clear_all(self) -> None:
        """
        Delete all records from every relational table and all documents from
        every ChromaDB collection. Non-fatal Chroma errors are logged but do
        not prevent the relational clear from completing.
        """
        await self._relational.clear_all()
        try:
            await self._vector.clear_all()
        except Exception as exc:
            logger.error("ChromaDB clear_all failed: %s", exc)

    # ------------------------------------------------------------------
    # Pass-through accessors (convenience for routers)
    # ------------------------------------------------------------------

    @property
    def relational(self) -> RelationalDB:
        return self._relational

    @property
    def vector(self) -> VectorDB:
        return self._vector
