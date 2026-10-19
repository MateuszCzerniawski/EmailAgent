"""
SQLite implementation of RelationalDB using SQLAlchemy async + aiosqlite.

Uses dialect-agnostic ORM models (database.models.orm) so it works with
SQLite's type system (TEXT for UUIDs, JSON for arrays, TEXT for enums).

Key differences from postgres.py:
  - No pg_insert / ON CONFLICT — upserts use select-then-update-or-insert
  - connect() runs Base.metadata.create_all() so tables are created automatically
    and persist across server restarts (no Alembic migration needed for dev)
  - connect_args={"check_same_thread": False} required for aiosqlite
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from database.exceptions import DuplicateEmailError
from database.models.domain import (
    AgentCacheModel,
    EmailModel,
    GuidelineModel,
    SenderModel,
    TagModel,
    ThreadModel,
)
from database.models.orm import (
    AgentCacheORM,
    Base,
    EmailORM,
    GuidelineORM,
    SenderORM,
    TagORM,
    ThreadORM,
    email_tags,
)
from database.relational.base import RelationalDB

logger = logging.getLogger(__name__)


def _load_json_list(value) -> list:
    """Return value as a Python list, parsing JSON strings if needed."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    return []


class SQLiteDB(RelationalDB):
    """
    SQLite-backed implementation of RelationalDB.

    Data is stored in a persistent SQLite file. Tables are auto-created on
    connect() using Base.metadata.create_all(), so the DB survives restarts
    with no migration tooling required.
    """

    def __init__(
        self,
        database_url: str = "sqlite+aiosqlite:///./database/emailagent.db",
    ) -> None:
        self._database_url = database_url
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

        # Ensure the parent directory of the SQLite file exists
        if "///" in database_url:
            db_path = database_url.split("///", 1)[1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._engine = create_async_engine(
            self._database_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

        # Verify connectivity
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            raise ConnectionError(
                f"Cannot open SQLite database '{self._database_url}': {exc}"
            ) from exc

        # Auto-create tables (idempotent — safe to run on every startup)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Migrate existing databases: add columns introduced after initial schema.
            # Each migration runs in its own transaction so one failure doesn't block others.
            for migration_sql in [
                # --- AGENT CACHE ---
                "ALTER TABLE agent_cache ADD COLUMN actions TEXT",
                "ALTER TABLE agent_cache ADD COLUMN importance_score INTEGER",
                "ALTER TABLE agent_cache ADD COLUMN suggested_action TEXT",
                "ALTER TABLE agent_cache ADD COLUMN summary TEXT",
                "ALTER TABLE agent_cache ADD COLUMN confidence_score REAL",
                "ALTER TABLE agent_cache ADD COLUMN model_used TEXT",

                # --- THREADS ---
                "ALTER TABLE threads ADD COLUMN gmail_thread_id TEXT",
                "ALTER TABLE threads ADD COLUMN summary TEXT",
                "ALTER TABLE threads ADD COLUMN summary_updated_at DATETIME",
                "ALTER TABLE threads ADD COLUMN status TEXT",

                # --- SENDERS ---
                "ALTER TABLE senders ADD COLUMN importance_score INTEGER",
                "ALTER TABLE senders ADD COLUMN relation_type TEXT",
                "ALTER TABLE senders ADD COLUMN summary TEXT",
                "ALTER TABLE senders ADD COLUMN summary_updated_at DATETIME",

                # --- EMAILS ---
                "ALTER TABLE emails ADD COLUMN is_important BOOLEAN",
                "ALTER TABLE emails ADD COLUMN body_html TEXT",

                # --- INDEXES ---
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_threads_gmail_thread_id ON threads (gmail_thread_id)",
                "CREATE INDEX IF NOT EXISTS ix_emails_thread_id ON emails (thread_id)",
                "CREATE INDEX IF NOT EXISTS ix_emails_sender_id ON emails (sender_id)",
                "CREATE INDEX IF NOT EXISTS ix_threads_sender_id ON threads (sender_id)",
            ]:
                try:
                    async with self._engine.begin() as conn:
                        await conn.execute(text(migration_sql))
                except Exception:
                    pass  # already applied

        db_path = self._database_url.split("///", 1)[-1]
        logger.info("RelationalDB connected: %s", db_path)

    async def disconnect(self) -> None:
        if self._engine:
            await self._engine.dispose()
            logger.info("RelationalDB disconnected")

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession, None]:
        assert self._session_factory is not None, "Call connect() first"
        async with self._session_factory() as session:
            async with session.begin():
                yield session

    # ------------------------------------------------------------------
    # ORM → domain converters
    # ------------------------------------------------------------------

    @staticmethod
    def _sender_to_domain(row: SenderORM) -> SenderModel:
        return SenderModel(
            id=row.id,
            email=row.email,
            display_name=row.display_name,
            importance_score=row.importance_score,
            relation_type=row.relation_type,
            mail_count=row.mail_count,
            last_contact_at=row.last_contact_at,
            summary=row.summary,
            summary_updated_at=row.summary_updated_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _thread_to_domain(row: ThreadORM) -> ThreadModel:
        return ThreadModel(
            id=row.id,
            gmail_thread_id=row.gmail_thread_id,
            subject=row.subject,
            sender_id=row.sender_id,
            mail_count=row.mail_count,
            started_at=row.started_at,
            last_mail_at=row.last_mail_at,
            summary=row.summary,
            summary_updated_at=row.summary_updated_at,
            status=row.status,
        )

    @staticmethod
    def _email_to_domain(row: EmailORM) -> EmailModel:
        return EmailModel(
            id=row.id,
            message_id=row.message_id,
            thread_id=row.thread_id,
            sender_id=row.sender_id,
            recipients=_load_json_list(row.recipients),
            subject=row.subject,
            body=row.body,
            body_html=row.body_html,
            received_at=row.received_at,
            is_read=row.is_read,
            is_important=row.is_important,
            status=row.status,
            embedding_model=row.embedding_model,
            embedded_at=row.embedded_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _agent_cache_to_domain(row: AgentCacheORM) -> AgentCacheModel:
        return AgentCacheModel(
            id=row.id,
            email_id=row.email_id,
            analysis=row.analysis,
            suggested_reply=row.suggested_reply,
            suggested_tags=_load_json_list(row.suggested_tags),
            suggested_action=row.suggested_action,
            actions=_load_json_list(row.actions),
            summary=row.summary,
            confidence_score=row.confidence_score,
            importance_score=row.importance_score,
            model_used=row.model_used,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _guideline_to_domain(row: GuidelineORM) -> GuidelineModel:
        return GuidelineModel(
            id=row.id,
            filename=row.filename,
            file_type=row.file_type,
            category=row.category,
            version=row.version,
            content=row.content,
            storage_path=row.storage_path,
            embedding_model=row.embedding_model,
            embedded_at=row.embedded_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # ------------------------------------------------------------------
    # Senders
    # ------------------------------------------------------------------

    async def upsert_sender(self, sender: SenderModel) -> SenderModel:
        async with self._session() as session:
            result = await session.execute(
                select(SenderORM).where(SenderORM.email == sender.email)
            )
            existing = result.scalars().first()
            if existing:
                existing.display_name = sender.display_name or existing.display_name
                existing.mail_count = existing.mail_count + 1
                existing.last_contact_at = sender.last_contact_at or existing.last_contact_at
                await session.flush()
                return self._sender_to_domain(existing)

            new_row = SenderORM(
                id=sender.id or uuid4(),
                email=sender.email,
                display_name=sender.display_name,
                importance_score=sender.importance_score,
                relation_type=sender.relation_type,
                mail_count=sender.mail_count,
                last_contact_at=sender.last_contact_at,
                summary=sender.summary,
                summary_updated_at=sender.summary_updated_at,
            )
            session.add(new_row)
            await session.flush()
            return self._sender_to_domain(new_row)

    async def get_sender_by_email(self, email: str) -> SenderModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(SenderORM).where(SenderORM.email == email)
            )
            row = result.scalars().first()
            return self._sender_to_domain(row) if row else None

    async def get_sender(self, sender_id: UUID) -> SenderModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(SenderORM).where(SenderORM.id == sender_id)
            )
            row = result.scalars().first()
            return self._sender_to_domain(row) if row else None

    async def update_sender_summary(self, sender_id: UUID, summary: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(SenderORM)
                .where(SenderORM.id == sender_id)
                .values(summary=summary, summary_updated_at=datetime.now(timezone.utc))
            )

    async def list_senders(self, limit: int = 50, offset: int = 0) -> list[SenderModel]:
        async with self._session() as session:
            result = await session.execute(
                select(SenderORM)
                .order_by(SenderORM.last_contact_at.desc().nullslast())
                .limit(limit)
                .offset(offset)
            )
            return [self._sender_to_domain(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    async def upsert_thread(self, thread: ThreadModel) -> ThreadModel:
        async with self._session() as session:
            if thread.id:
                result = await session.execute(
                    select(ThreadORM).where(ThreadORM.id == thread.id)
                )
                existing = result.scalars().first()
                if existing:
                    existing.mail_count = thread.mail_count
                    existing.last_mail_at = thread.last_mail_at
                    existing.status = thread.status
                    if thread.started_at and not existing.started_at:
                        existing.started_at = thread.started_at
                    if thread.gmail_thread_id and not existing.gmail_thread_id:
                        existing.gmail_thread_id = thread.gmail_thread_id
                    await session.flush()
                    return self._thread_to_domain(existing)

            new_row = ThreadORM(
                id=thread.id or uuid4(),
                subject=thread.subject,
                sender_id=thread.sender_id,
                mail_count=thread.mail_count,
                started_at=thread.started_at,
                last_mail_at=thread.last_mail_at,
                summary=thread.summary,
                summary_updated_at=thread.summary_updated_at,
                status=thread.status,
            )
            session.add(new_row)
            await session.flush()
            return self._thread_to_domain(new_row)

    async def get_thread(self, thread_id: UUID) -> ThreadModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(ThreadORM).where(ThreadORM.id == thread_id)
            )
            row = result.scalars().first()
            return self._thread_to_domain(row) if row else None

    async def get_thread_by_gmail_id(self, gmail_thread_id: str) -> ThreadModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(ThreadORM).where(ThreadORM.gmail_thread_id == gmail_thread_id)
            )
            row = result.scalars().first()
            return self._thread_to_domain(row) if row else None

    async def merge_thread_into(self, source_thread_id: UUID, target_thread_id: UUID) -> None:
        """Move all emails from source thread to target thread, then delete source."""
        async with self._session() as session:
            # Move emails to target
            await session.execute(
                update(EmailORM)
                .where(EmailORM.thread_id == source_thread_id)
                .values(thread_id=target_thread_id)
            )
            # Recalculate target thread stats from its (now combined) emails
            stats = await session.execute(
                select(
                    func.count(EmailORM.id),
                    func.min(EmailORM.received_at),
                    func.max(EmailORM.received_at),
                ).where(EmailORM.thread_id == target_thread_id)
            )
            row = stats.first()
            await session.execute(
                update(ThreadORM)
                .where(ThreadORM.id == target_thread_id)
                .values(
                    mail_count=row[0] if row else 0,
                    started_at=row[1] if row else None,
                    last_mail_at=row[2] if row else None,
                )
            )
            # Delete source (no emails reference it now)
            await session.execute(
                delete(ThreadORM).where(ThreadORM.id == source_thread_id)
            )

    async def update_thread_summary(self, thread_id: UUID, summary: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(ThreadORM)
                .where(ThreadORM.id == thread_id)
                .values(summary=summary, summary_updated_at=datetime.now(timezone.utc))
            )

    async def list_threads(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        sender_id: UUID | None = None,
    ) -> list[ThreadModel]:
        async with self._session() as session:
            query = select(ThreadORM)
            if status:
                query = query.where(ThreadORM.status == status)
            if sender_id:
                query = query.where(ThreadORM.sender_id == sender_id)
            query = (
                query.order_by(ThreadORM.last_mail_at.desc().nullslast())
                .limit(limit)
                .offset(offset)
            )
            result = await session.execute(query)
            return [self._thread_to_domain(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # Emails
    # ------------------------------------------------------------------

    async def insert_email_atomic(
        self,
        sender: SenderModel,
        thread_subject: str,
        email: EmailModel,
        received_at: datetime,
        gmail_thread_id: str | None = None,
    ) -> tuple[SenderModel, ThreadModel, EmailModel]:
        """
        Upsert sender, find-or-create thread, and insert email in one transaction.
        Any exception triggers automatic rollback of all three writes.
        """
        async with self._session() as session:
            # --- 1. Upsert sender ---
            result = await session.execute(
                select(SenderORM).where(SenderORM.email == sender.email)
            )
            sender_row = result.scalars().first()
            if sender_row:
                sender_row.display_name = sender.display_name or sender_row.display_name
                sender_row.mail_count = sender_row.mail_count + 1
                sender_row.last_contact_at = received_at
                await session.flush()
            else:
                sender_row = SenderORM(
                    id=sender.id or uuid4(),
                    email=sender.email,
                    display_name=sender.display_name,
                    importance_score=sender.importance_score,
                    relation_type=sender.relation_type,
                    mail_count=1,  # first email for this sender
                    last_contact_at=received_at,
                    summary=sender.summary,
                    summary_updated_at=sender.summary_updated_at,
                )
                session.add(sender_row)
                await session.flush()

            sender_model = self._sender_to_domain(sender_row)

            # --- 2. Find or create thread ---
            # Primary lookup: by gmail_thread_id (exact, cross-sender)
            # Fallback: by subject on threads that have no gmail_thread_id yet (legacy data).
            #   sender_id is intentionally NOT part of the fallback — threads involve
            #   multiple senders (replies from different people in the same conversation).
            #   Restricting to gmail_thread_id IS NULL prevents accidentally merging two
            #   different Gmail threads that happen to share the same subject.
            thread_row = None
            if gmail_thread_id:
                result = await session.execute(
                    select(ThreadORM).where(ThreadORM.gmail_thread_id == gmail_thread_id)
                )
                thread_row = result.scalars().first()
            if thread_row is None:
                result = await session.execute(
                    select(ThreadORM).where(
                        ThreadORM.subject == thread_subject,
                        ThreadORM.status == "open",
                        ThreadORM.gmail_thread_id.is_(None),
                    )
                )
                thread_row = result.scalars().first()
                # Backfill gmail_thread_id on the matched legacy thread
                if thread_row is not None and gmail_thread_id:
                    thread_row.gmail_thread_id = gmail_thread_id

            if thread_row:
                thread_row.mail_count = thread_row.mail_count + 1
                thread_row.last_mail_at = received_at
                await session.flush()
            else:
                thread_row = ThreadORM(
                    id=uuid4(),
                    gmail_thread_id=gmail_thread_id,
                    subject=thread_subject,
                    sender_id=sender_row.id,
                    mail_count=1,
                    started_at=received_at,
                    last_mail_at=received_at,
                    status="open",
                )
                session.add(thread_row)
                await session.flush()

            thread_model = self._thread_to_domain(thread_row)

            # --- 3. Insert email ---
            existing = await session.execute(
                select(EmailORM).where(EmailORM.message_id == email.message_id)
            )
            if existing.scalars().first():
                raise DuplicateEmailError(email.message_id)

            email_row = EmailORM(
                id=email.id or uuid4(),
                message_id=email.message_id,
                thread_id=thread_row.id,
                sender_id=sender_row.id,
                recipients=email.recipients,
                subject=email.subject,
                body=email.body,
                body_html=email.body_html,
                received_at=received_at,
                is_read=email.is_read,
                is_important=email.is_important,
                status=email.status,
                embedding_model=email.embedding_model,
                embedded_at=email.embedded_at,
            )
            session.add(email_row)
            await session.flush()

            return sender_model, thread_model, self._email_to_domain(email_row)

    async def insert_email(self, email: EmailModel) -> EmailModel:
        existing = await self.get_email_by_message_id(email.message_id)
        if existing:
            raise DuplicateEmailError(email.message_id)

        async with self._session() as session:
            new_row = EmailORM(
                id=email.id or uuid4(),
                message_id=email.message_id,
                thread_id=email.thread_id,
                sender_id=email.sender_id,
                recipients=email.recipients,
                subject=email.subject,
                body=email.body,
                body_html=email.body_html,
                received_at=email.received_at,
                is_read=email.is_read,
                is_important=email.is_important,
                status=email.status,
                embedding_model=email.embedding_model,
                embedded_at=email.embedded_at,
            )
            session.add(new_row)
            await session.flush()
            return self._email_to_domain(new_row)

    async def get_email_by_message_id(self, message_id: str) -> EmailModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(EmailORM).where(EmailORM.message_id == message_id)
            )
            row = result.scalars().first()
            return self._email_to_domain(row) if row else None

    async def get_email(self, email_id: UUID) -> EmailModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(EmailORM).where(EmailORM.id == email_id)
            )
            row = result.scalars().first()
            return self._email_to_domain(row) if row else None

    async def mark_as_embedded(self, email_id: UUID, model_name: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(EmailORM)
                .where(EmailORM.id == email_id)
                .values(
                    embedded_at=datetime.now(timezone.utc),
                    embedding_model=model_name,
                )
            )

    async def get_unembedded_emails(self, limit: int = 100) -> list[EmailModel]:
        async with self._session() as session:
            result = await session.execute(
                select(EmailORM)
                .where(EmailORM.embedded_at.is_(None))
                .order_by(EmailORM.received_at.desc())
                .limit(limit)
            )
            return [self._email_to_domain(r) for r in result.scalars().all()]

    async def get_emails_by_sender(
        self, sender_id: UUID, since: datetime | None = None
    ) -> list[EmailModel]:
        async with self._session() as session:
            query = select(EmailORM).where(EmailORM.sender_id == sender_id)
            if since:
                query = query.where(EmailORM.received_at >= since)
            result = await session.execute(query.order_by(EmailORM.received_at.desc()))
            return [self._email_to_domain(r) for r in result.scalars().all()]

    async def get_latest_email_per_thread(self, thread_ids: list[UUID]) -> dict[UUID, EmailModel]:
        if not thread_ids:
            return {}
        async with self._session() as session:
            sub = (
                select(
                    EmailORM.thread_id,
                    func.max(EmailORM.received_at).label("max_received_at"),
                )
                .where(EmailORM.thread_id.in_(thread_ids))
                .group_by(EmailORM.thread_id)
                .subquery()
            )
            result = await session.execute(
                select(EmailORM).join(
                    sub,
                    (EmailORM.thread_id == sub.c.thread_id)
                    & (EmailORM.received_at == sub.c.max_received_at),
                )
            )
            return {r.thread_id: self._email_to_domain(r) for r in result.scalars().all()}

    async def get_senders_by_ids(self, sender_ids: list[UUID]) -> dict[UUID, SenderModel]:
        if not sender_ids:
            return {}
        async with self._session() as session:
            result = await session.execute(
                select(SenderORM).where(SenderORM.id.in_(sender_ids))
            )
            return {r.id: self._sender_to_domain(r) for r in result.scalars().all()}

    async def list_emails(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        sender_id: UUID | None = None,
        thread_id: UUID | None = None,
    ) -> list[EmailModel]:
        async with self._session() as session:
            query = select(EmailORM)
            if status:
                query = query.where(EmailORM.status == status)
            if sender_id:
                query = query.where(EmailORM.sender_id == sender_id)
            if thread_id:
                query = query.where(EmailORM.thread_id == thread_id)
            result = await session.execute(
                query.order_by(EmailORM.received_at.desc()).limit(limit).offset(offset)
            )
            return [self._email_to_domain(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # Agent Cache
    # ------------------------------------------------------------------

    async def upsert_agent_cache(self, cache: AgentCacheModel) -> AgentCacheModel:
        async with self._session() as session:
            # Przygotowanie danych (np. tablic akcji)
            actions_json = [action.model_dump(mode='json') for action in cache.actions] if cache.actions else []

            # Natywny SQLite Upsert
            stmt = sqlite_insert(AgentCacheORM).values(
                id=cache.id or uuid4(),
                email_id=cache.email_id,
                analysis=cache.analysis,
                suggested_reply=cache.suggested_reply,
                suggested_tags=cache.suggested_tags,
                suggested_action=cache.suggested_action,
                actions=actions_json,
                summary=cache.summary,
                confidence_score=cache.confidence_score,
                importance_score=cache.importance_score,
                model_used=cache.model_used,
            )

            # Instrukcja: Jeśli email_id już istnieje, zaktualizuj podane pola
            stmt = stmt.on_conflict_do_update(
                index_elements=['email_id'],
                set_=dict(
                    analysis=stmt.excluded.analysis,
                    suggested_reply=stmt.excluded.suggested_reply,
                    suggested_tags=stmt.excluded.suggested_tags,
                    suggested_action=stmt.excluded.suggested_action,
                    actions=stmt.excluded.actions,
                    summary=stmt.excluded.summary,
                    confidence_score=stmt.excluded.confidence_score,
                    importance_score=stmt.excluded.importance_score,
                    model_used=stmt.excluded.model_used,
                    updated_at=datetime.now(timezone.utc)
                )
            )

            await session.execute(stmt)
            await session.flush()

            # Zwracamy zaktualizowany/utworzony model prosto z bazy
            result = await session.execute(
                select(AgentCacheORM).where(AgentCacheORM.email_id == cache.email_id)
            )
            row = result.scalars().first()
            return self._agent_cache_to_domain(row)

    async def get_agent_cache(self, email_id: UUID) -> AgentCacheModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(AgentCacheORM).where(AgentCacheORM.email_id == email_id)
            )
            row = result.scalars().first()
            return self._agent_cache_to_domain(row) if row else None

    async def get_agent_caches_by_email_ids(self, email_ids: list[UUID]) -> dict[UUID, AgentCacheModel]:
        if not email_ids:
            return {}
        async with self._session() as session:
            result = await session.execute(
                select(AgentCacheORM).where(AgentCacheORM.email_id.in_(email_ids))
            )
            return {r.email_id: self._agent_cache_to_domain(r) for r in result.scalars().all()}

    async def list_agent_caches(self, limit: int = 2000) -> list[AgentCacheModel]:
        async with self._session() as session:
            result = await session.execute(select(AgentCacheORM).limit(limit))
            return [self._agent_cache_to_domain(r) for r in result.scalars().all()]

    async def update_suggested_reply(self, email_id: UUID, reply: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(AgentCacheORM)
                .where(AgentCacheORM.email_id == email_id)
                .values(suggested_reply=reply)
            )

    async def update_suggested_tags(self, email_id: UUID, tags: list[str]) -> None:
        async with self._session() as session:
            await session.execute(
                update(AgentCacheORM)
                .where(AgentCacheORM.email_id == email_id)
                .values(suggested_tags=tags)
            )

    async def update_importance_score(self, email_id: UUID, importance_score: int) -> None:
        async with self._session() as session:
            await session.execute(
                update(AgentCacheORM)
                .where(AgentCacheORM.email_id == email_id)
                .values(importance_score=importance_score)
            )

    # ------------------------------------------------------------------
    # Guidelines
    # ------------------------------------------------------------------

    async def upsert_guideline(self, guideline: GuidelineModel) -> GuidelineModel:
        async with self._session() as session:
            result = await session.execute(
                select(GuidelineORM).where(GuidelineORM.filename == guideline.filename)
            )
            existing = result.scalars().first()
            if existing:
                existing.file_type = guideline.file_type
                existing.category = guideline.category
                existing.version = existing.version + 1
                existing.content = guideline.content
                existing.storage_path = guideline.storage_path
                existing.embedding_model = None
                existing.embedded_at = None
                existing.updated_at = datetime.now(timezone.utc)
                await session.flush()
                return self._guideline_to_domain(existing)

            new_row = GuidelineORM(
                id=guideline.id or uuid4(),
                filename=guideline.filename,
                file_type=guideline.file_type,
                category=guideline.category,
                version=guideline.version,
                content=guideline.content,
                storage_path=guideline.storage_path,
                embedding_model=guideline.embedding_model,
                embedded_at=guideline.embedded_at,
            )
            session.add(new_row)
            await session.flush()
            return self._guideline_to_domain(new_row)

    async def get_guideline(self, guideline_id: UUID) -> GuidelineModel | None:
        async with self._session() as session:
            result = await session.execute(
                select(GuidelineORM).where(GuidelineORM.id == guideline_id)
            )
            row = result.scalars().first()
            return self._guideline_to_domain(row) if row else None

    async def get_unembedded_guidelines(self) -> list[GuidelineModel]:
        async with self._session() as session:
            result = await session.execute(
                select(GuidelineORM).where(GuidelineORM.embedded_at.is_(None))
            )
            return [self._guideline_to_domain(r) for r in result.scalars().all()]

    async def mark_guideline_as_embedded(
        self, guideline_id: UUID, model_name: str
    ) -> None:
        async with self._session() as session:
            await session.execute(
                update(GuidelineORM)
                .where(GuidelineORM.id == guideline_id)
                .values(
                    embedded_at=datetime.now(timezone.utc),
                    embedding_model=model_name,
                )
            )

    async def list_guidelines(
        self, limit: int = 50, offset: int = 0
    ) -> list[GuidelineModel]:
        async with self._session() as session:
            result = await session.execute(
                select(GuidelineORM)
                .order_by(GuidelineORM.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [self._guideline_to_domain(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    async def list_tags(self) -> list[TagModel]:
        async with self._session() as session:
            result = await session.execute(select(TagORM).order_by(TagORM.name))
            return [TagModel(id=r.id, name=r.name) for r in result.scalars().all()]

    async def insert_tag(self, name: str) -> TagModel:
        async with self._session() as session:
            existing = await session.execute(
                select(TagORM).where(TagORM.name == name)
            )
            if existing.scalars().first():
                raise ValueError(f"Tag '{name}' already exists")
            new_row = TagORM(id=uuid4(), name=name)
            session.add(new_row)
            await session.flush()
            return TagModel(id=new_row.id, name=new_row.name)

    async def get_tags_by_sender(self, sender_id: UUID) -> list[TagModel]:
        async with self._session() as session:
            result = await session.execute(
                select(TagORM)
                .join(email_tags, TagORM.id == email_tags.c.tag_id)
                .join(EmailORM, EmailORM.id == email_tags.c.email_id)
                .where(EmailORM.sender_id == sender_id)
                .distinct()
                .order_by(TagORM.name)
            )
            return [TagModel(id=r.id, name=r.name) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # Deletions
    # ------------------------------------------------------------------

    async def delete_email(self, email_id: UUID) -> bool:
        async with self._session() as session:
            result = await session.execute(
                delete(EmailORM).where(EmailORM.id == email_id)
            )
            return result.rowcount > 0

    async def delete_sender(self, sender_id: UUID) -> bool:
        async with self._session() as session:
            result = await session.execute(
                delete(SenderORM).where(SenderORM.id == sender_id)
            )
            return result.rowcount > 0

    async def delete_thread(self, thread_id: UUID) -> bool:
        async with self._session() as session:
            result = await session.execute(
                delete(ThreadORM).where(ThreadORM.id == thread_id)
            )
            return result.rowcount > 0

    async def delete_guideline(self, guideline_id: UUID) -> bool:
        async with self._session() as session:
            result = await session.execute(
                delete(GuidelineORM).where(GuidelineORM.id == guideline_id)
            )
            return result.rowcount > 0

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        try:
            async with self._session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            logger.warning("SQLite health check failed: %s", exc)
            return False

    async def get_all_email_ids(self) -> set[str]:
        async with self._session() as session:
            result = await session.execute(select(EmailORM.id))
            return {str(row) for row in result.scalars().all()}

    async def clear_all(self) -> None:
        async with self._session() as session:
            for orm_cls in (AgentCacheORM, EmailORM, ThreadORM, SenderORM, GuidelineORM, TagORM):
                await session.execute(delete(orm_cls))
        logger.info("RelationalDB: all tables cleared")
