"""
ChromaDB implementation of VectorDB.

Uses chromadb.PersistentClient (synchronous). All Chroma calls are wrapped
with asyncio.get_event_loop().run_in_executor(None, ...) so they don't block
the async event loop.

Collections managed:
  - email_chunks        (one doc per chunk of each email body)
  - thread_summaries    (one doc per thread summary)
  - sender_summaries    (one doc per sender summary)
  - guideline_chunks    (one doc per chunk of each guideline)
  - task_chunks         (one doc per action-item task extracted from emails)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

import chromadb

from database.models.domain import (
    ChromaDocumentItem,
    EmailModel,
    GuidelineModel,
    SearchResult,
    SenderModel,
    ThreadModel,
)
from database.vector.base import VectorDB
from database.vector.chunker import TokenChunker
from database.vector.embedder import Embedder

logger = logging.getLogger(__name__)

_COLLECTION_EMAIL_CHUNKS = "email_chunks"
_COLLECTION_THREAD_SUMMARIES = "thread_summaries"
_COLLECTION_SENDER_SUMMARIES = "sender_summaries"
_COLLECTION_GUIDELINE_CHUNKS = "guideline_chunks"
_COLLECTION_TASK_CHUNKS = "task_chunks"


class ChromaVectorDB(VectorDB):
    """ChromaDB-backed implementation of the VectorDB interface."""

    def __init__(
        self,
        chroma_path: str,
        embedding_model: str,
        chunk_size: int = 400,
        chunk_overlap: int = 80,
    ) -> None:
        self._chroma_path = chroma_path
        self._embedding_model = embedding_model
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

        self._client: chromadb.PersistentClient | None = None
        self._chunker: TokenChunker | None = None
        self._embedder: Embedder | None = None

        self._col_email_chunks = None
        self._col_thread_summaries = None
        self._col_sender_summaries = None
        self._col_guideline_chunks = None
        self._col_task_chunks = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run(self, fn, *args, **kwargs):
        """Run a synchronous callable in the default executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    def _collection_metadata(self) -> dict:
        return {
            "hnsw:space": "cosine",
            "embedding_model": self._embedding_model,
            "chunk_size": self._chunk_size,
            "chunk_overlap": self._chunk_overlap,
            "version": "1",
        }

    def _get_or_create(self, name: str):
        assert self._client is not None
        return self._client.get_or_create_collection(
            name=name,
            metadata=self._collection_metadata(),
        )

    def _docs_to_items(
        self, ids: list[str], documents: list[str], metadatas: list[dict]
    ) -> list[ChromaDocumentItem]:
        items = []
        for doc_id, doc, meta in zip(ids, documents, metadatas):
            items.append(
                ChromaDocumentItem(
                    id=doc_id,
                    document_snippet=doc[:200] if doc else "",
                    metadata=meta or {},
                )
            )
        return items

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        Path(self._chroma_path).mkdir(parents=True, exist_ok=True)

        def _init():
            client = chromadb.PersistentClient(path=self._chroma_path)
            return client

        self._client = await self._run(_init)
        self._chunker = TokenChunker(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
        )
        self._embedder = Embedder(self._embedding_model)

        self._col_email_chunks = await self._run(
            self._get_or_create, _COLLECTION_EMAIL_CHUNKS
        )
        self._col_thread_summaries = await self._run(
            self._get_or_create, _COLLECTION_THREAD_SUMMARIES
        )
        self._col_sender_summaries = await self._run(
            self._get_or_create, _COLLECTION_SENDER_SUMMARIES
        )
        self._col_guideline_chunks = await self._run(
            self._get_or_create, _COLLECTION_GUIDELINE_CHUNKS
        )
        self._col_task_chunks = await self._run(
            self._get_or_create, _COLLECTION_TASK_CHUNKS
        )
        logger.info("ChromaVectorDB connected: path=%s", self._chroma_path)

    async def disconnect(self) -> None:
        self._client = None
        logger.info("ChromaVectorDB disconnected")

    # ------------------------------------------------------------------
    # Upserts
    # ------------------------------------------------------------------

    async def upsert_email_chunks(self, email: EmailModel) -> None:
        assert self._chunker and self._embedder and self._col_email_chunks

        raw_chunks = self._chunker.chunk(email.body)
        if not raw_chunks:
            logger.debug("upsert_email_chunks: no chunks for email %s (empty body)", email.id)
            return

        # Prepend subject to every chunk so subject-based queries always match
        subject_prefix = f"Subject: {email.subject}\n\n" if email.subject else ""
        chunks = [f"{subject_prefix}{c}" for c in raw_chunks]

        ids = [f"{email.id}__chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "email_id": str(email.id),
                "subject": email.subject or "",
                "chunk_index": i,
                "sender_id": str(email.sender_id),
                "thread_id": str(email.thread_id),
                "received_at": email.received_at.isoformat(),
                "is_important": email.is_important,
                "status": email.status,
                "embedding_model": self._embedding_model,
            }
            for i in range(len(chunks))
        ]

        embeddings = await self._run(self._embedder.embed, chunks)

        col = self._col_email_chunks

        def _upsert():
            col.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )

        await self._run(_upsert)
        logger.debug("upsert_email_chunks: %d chunks for email %s", len(chunks), email.id)

    async def upsert_thread_summary(self, thread: ThreadModel) -> None:
        assert self._embedder and self._col_thread_summaries
        if not thread.summary or not thread.id:
            return

        doc_id = str(thread.id)
        meta = {
            "thread_id": str(thread.id),
            "sender_id": str(thread.sender_id),
            "last_mail_at": thread.last_mail_at.isoformat() if thread.last_mail_at else "",
            "status": thread.status,
        }
        embedding = (await self._run(self._embedder.embed, [thread.summary]))[0]
        col = self._col_thread_summaries

        def _upsert():
            col.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[thread.summary],
                metadatas=[meta],
            )

        await self._run(_upsert)

    async def upsert_sender_summary(self, sender: SenderModel) -> None:
        assert self._embedder and self._col_sender_summaries
        if not sender.summary or not sender.id:
            return

        doc_id = str(sender.id)
        meta = {
            "sender_id": str(sender.id),
            "importance_score": sender.importance_score,
            "relation_type": sender.relation_type,
            "mail_count": sender.mail_count,
        }
        embedding = (await self._run(self._embedder.embed, [sender.summary]))[0]
        col = self._col_sender_summaries

        def _upsert():
            col.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[sender.summary],
                metadatas=[meta],
            )

        await self._run(_upsert)

    async def upsert_guideline_chunks(self, guideline: GuidelineModel) -> None:
        assert self._chunker and self._embedder and self._col_guideline_chunks

        chunks = self._chunker.chunk(guideline.content)
        if not chunks:
            return

        ids = [f"{guideline.id}__chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "guideline_id": str(guideline.id),
                "filename": guideline.filename,
                "category": guideline.category,
                "version": guideline.version,
                "chunk_index": i,
                "embedding_model": self._embedding_model,
            }
            for i in range(len(chunks))
        ]

        embeddings = await self._run(self._embedder.embed, chunks)
        col = self._col_guideline_chunks

        def _upsert():
            col.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )

        await self._run(_upsert)

    async def upsert_task_chunks(
        self,
        email_id: UUID,
        tasks: list,
        subject: str = "",
        sender_id: str = "",
        received_at: str = "",
    ) -> None:
        assert self._embedder and self._col_task_chunks
        if not tasks:
            return

        ids, documents, metadatas = [], [], []
        for i, t in enumerate(tasks):
            task_text = t.task if hasattr(t, "task") else t.get("task", "")
            deadline = (t.deadline if hasattr(t, "deadline") else t.get("deadline")) or ""
            if not task_text:
                continue
            doc = f"Task: {task_text}\nDeadline: {deadline or 'none'}\nFrom email: {subject}"
            ids.append(f"{email_id}__task_{i}")
            documents.append(doc)
            metadatas.append({
                "email_id": str(email_id),
                "task_index": i,
                "task_text": task_text,
                "deadline": str(deadline) if deadline else "",
                "subject": subject,
                "sender_id": sender_id,
                "received_at": received_at,
                "source_type": "task",
                "embedding_model": self._embedding_model,
            })

        if not ids:
            return

        embeddings = await self._run(self._embedder.embed, documents)
        col = self._col_task_chunks

        def _upsert():
            col.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

        await self._run(_upsert)
        logger.debug("upsert_task_chunks: %d tasks for email %s", len(ids), email_id)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_emails(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        assert self._embedder and self._col_email_chunks

        embedding = (await self._run(self._embedder.embed, [query]))[0]
        col = self._col_email_chunks

        def _query():
            kwargs = {
                "query_embeddings": [embedding],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
            }
            if filters:
                kwargs["where"] = filters
            return col.query(**kwargs)

        results = await self._run(_query)
        return self._chroma_results_to_search_results(results, source="email_chunk")

    async def search_guidelines(
        self,
        query: str,
        top_k: int = 3,
    ) -> list[SearchResult]:
        assert self._embedder and self._col_guideline_chunks

        embedding = (await self._run(self._embedder.embed, [query]))[0]
        col = self._col_guideline_chunks

        def _query():
            return col.query(
                query_embeddings=[embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )

        results = await self._run(_query)
        return self._chroma_results_to_search_results(results, source="guideline_chunk")

    async def search_tasks(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        assert self._embedder and self._col_task_chunks
        col = self._col_task_chunks

        count = await self._run(col.count)
        if count == 0:
            return []

        n = min(top_k, count)
        embedding = (await self._run(self._embedder.embed, [query]))[0]

        def _query():
            return col.query(
                query_embeddings=[embedding],
                n_results=n,
                include=["documents", "metadatas", "distances"],
            )

        results = await self._run(_query)
        return self._chroma_results_to_search_results(results, source="task_chunk")

    @staticmethod
    def _chroma_results_to_search_results(
        results: dict, source: str
    ) -> list[SearchResult]:
        output = []
        if not results or not results.get("ids"):
            return output
        ids = results["ids"][0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        for _, doc, meta, dist in zip(ids, documents, metadatas, distances):
            output.append(
                SearchResult(
                    source=source,
                    document=doc or "",
                    score=1.0 - float(dist),
                    metadata=meta or {},
                )
            )
        return output

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def delete_email_chunks(self, email_id: UUID) -> None:
        assert self._col_email_chunks
        col = self._col_email_chunks
        email_id_str = str(email_id)

        def _delete():
            results = col.get(where={"email_id": email_id_str}, include=[])
            if results and results.get("ids"):
                col.delete(ids=results["ids"])

        await self._run(_delete)

    async def delete_guideline_chunks(self, guideline_id: UUID) -> None:
        assert self._col_guideline_chunks
        col = self._col_guideline_chunks
        guideline_id_str = str(guideline_id)

        def _delete():
            results = col.get(where={"guideline_id": guideline_id_str}, include=[])
            if results and results.get("ids"):
                col.delete(ids=results["ids"])

        await self._run(_delete)

    async def delete_task_chunks(self, email_id: UUID) -> None:
        assert self._col_task_chunks
        col = self._col_task_chunks
        email_id_str = str(email_id)

        def _delete():
            results = col.get(where={"email_id": email_id_str}, include=[])
            if results and results.get("ids"):
                col.delete(ids=results["ids"])

        await self._run(_delete)

    # ------------------------------------------------------------------
    # Sync support
    # ------------------------------------------------------------------

    async def get_all_email_ids_in_chroma(self) -> set[str]:
        assert self._col_email_chunks
        col = self._col_email_chunks

        def _get_all():
            return col.get(include=["metadatas"])

        results = await self._run(_get_all)
        if not results or not results.get("metadatas"):
            return set()
        return {m["email_id"] for m in results["metadatas"] if m and "email_id" in m}

    # ------------------------------------------------------------------
    # Listing / inspection
    # ------------------------------------------------------------------

    async def list_collections(self) -> list[dict]:
        assert self._client

        def _list():
            names = [
                _COLLECTION_EMAIL_CHUNKS,
                _COLLECTION_THREAD_SUMMARIES,
                _COLLECTION_SENDER_SUMMARIES,
                _COLLECTION_GUIDELINE_CHUNKS,
                _COLLECTION_TASK_CHUNKS,
            ]
            out = []
            for name in names:
                try:
                    col = self._client.get_collection(name)
                    out.append({"name": name, "count": col.count()})
                except Exception:
                    out.append({"name": name, "count": 0})
            return out

        return await self._run(_list)

    async def _list_collection_items(
        self,
        col,
        limit: int,
        offset: int,
        where: dict | None = None,
    ) -> list[ChromaDocumentItem]:
        def _get():
            kwargs = {
                "limit": limit,
                "offset": offset,
                "include": ["documents", "metadatas"],
            }
            if where:
                kwargs["where"] = where
            return col.get(**kwargs)

        results = await self._run(_get)
        if not results or not results.get("ids"):
            return []
        return self._docs_to_items(
            results["ids"],
            results.get("documents") or [""] * len(results["ids"]),
            results.get("metadatas") or [{}] * len(results["ids"]),
        )

    async def get_thread_chunks(self, thread_id: UUID) -> list[ChromaDocumentItem]:
        """
        Return all email chunks for a given thread, sorted by received_at ascending.
        Fetches all matching documents (no pagination) for use as RAG context.
        """
        assert self._col_email_chunks
        col = self._col_email_chunks
        thread_id_str = str(thread_id)

        def _get():
            return col.get(
                where={"thread_id": thread_id_str},
                include=["documents", "metadatas"],
            )

        results = await self._run(_get)
        if not results or not results.get("ids"):
            return []

        ids = results["ids"]
        documents = results.get("documents") or [""] * len(ids)
        metadatas = results.get("metadatas") or [{}] * len(ids)

        # Build items with full document text (not the 200-char snippet used by inspection endpoints)
        items = [
            ChromaDocumentItem(id=doc_id, document_snippet=doc, metadata=meta or {})
            for doc_id, doc, meta in zip(ids, documents, metadatas)
        ]

        # Sort chronologically so conversation history reads in order
        items.sort(key=lambda item: item.metadata.get("received_at", ""))
        return items

    async def list_email_chunks(
        self,
        limit: int = 20,
        offset: int = 0,
        email_id: str | None = None,
    ) -> list[ChromaDocumentItem]:
        assert self._col_email_chunks
        where = {"email_id": email_id} if email_id else None
        return await self._list_collection_items(
            self._col_email_chunks, limit, offset, where
        )

    async def list_guideline_chunks(
        self,
        limit: int = 20,
        offset: int = 0,
        guideline_id: str | None = None,
    ) -> list[ChromaDocumentItem]:
        assert self._col_guideline_chunks
        where = {"guideline_id": guideline_id} if guideline_id else None
        return await self._list_collection_items(
            self._col_guideline_chunks, limit, offset, where
        )

    async def list_thread_summaries(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChromaDocumentItem]:
        assert self._col_thread_summaries
        return await self._list_collection_items(
            self._col_thread_summaries, limit, offset
        )

    async def list_sender_summaries(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChromaDocumentItem]:
        assert self._col_sender_summaries
        return await self._list_collection_items(
            self._col_sender_summaries, limit, offset
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        try:
            await self.list_collections()
            return True
        except Exception as exc:
            logger.warning("ChromaDB health check failed: %s", exc)
            return False

    async def clear_all(self) -> None:
        """Delete and recreate all managed collections, removing every document."""
        assert self._client is not None

        collection_names = [
            _COLLECTION_EMAIL_CHUNKS,
            _COLLECTION_THREAD_SUMMARIES,
            _COLLECTION_SENDER_SUMMARIES,
            _COLLECTION_GUIDELINE_CHUNKS,
            _COLLECTION_TASK_CHUNKS,
        ]

        def _delete_and_recreate():
            for name in collection_names:
                self._client.delete_collection(name)
            return (
                self._client.get_or_create_collection(_COLLECTION_EMAIL_CHUNKS, metadata=self._collection_metadata()),
                self._client.get_or_create_collection(_COLLECTION_THREAD_SUMMARIES, metadata=self._collection_metadata()),
                self._client.get_or_create_collection(_COLLECTION_SENDER_SUMMARIES, metadata=self._collection_metadata()),
                self._client.get_or_create_collection(_COLLECTION_GUIDELINE_CHUNKS, metadata=self._collection_metadata()),
                self._client.get_or_create_collection(_COLLECTION_TASK_CHUNKS, metadata=self._collection_metadata()),
            )

        (
            self._col_email_chunks,
            self._col_thread_summaries,
            self._col_sender_summaries,
            self._col_guideline_chunks,
            self._col_task_chunks,
        ) = await self._run(_delete_and_recreate)
        logger.info("ChromaVectorDB: all collections cleared and recreated")
