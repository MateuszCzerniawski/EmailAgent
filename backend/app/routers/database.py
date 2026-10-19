"""
Database router — test/admin endpoints for the database layer.

All handlers are async def (unlike the Gmail/LLM routers which are plain def).
Mounted at /database by main.py.

Endpoint groups:
  /database/health                 — connectivity check
  /database/emails/ingest          — ingest a raw email
  /database/guidelines/ingest      — ingest a guideline file
  /database/sync/*                 — sync / refresh operations
  /database/search                 — semantic search

Listing / inspection (SELECT * equivalents):
  Relational:
    /database/senders              — list all senders
    /database/senders/{id}         — single sender
    /database/threads              — list all threads
    /database/threads/{id}         — single thread
    /database/emails               — list emails (with filters)
    /database/emails/{id}          — single email
    /database/emails/{id}/cache    — agent cache entry
    /database/guidelines           — list guidelines
    /database/guidelines/{id}      — single guideline
    /database/tags                 — list all tags

  Vector (ChromaDB):
    /database/chroma/collections      — collection names + counts
    /database/chroma/email-chunks     — documents in email_chunks
    /database/chroma/guideline-chunks — documents in guideline_chunks
    /database/chroma/thread-summaries — documents in thread_summaries
    /database/chroma/sender-summaries — documents in sender_summaries
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.dependencies import get_db_manager
from database.exceptions import DuplicateEmailError
from database.models.domain import (
    AgentCacheModel,
    ChromaDocumentItem,
    EmailModel,
    GuidelineModel,
    RefreshReport,
    SearchResult,
    SenderModel,
    SyncReport,
    TagModel,
    ThreadModel,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class RawEmailInput(BaseModel):
    """Payload for POST /database/emails/ingest."""

    message_id: str
    thread_subject: str
    sender_email: str
    sender_name: str | None = None
    subject: str
    body: str
    body_html: str | None = None
    recipients: list[str] = []
    received_at: datetime
    has_attachments: bool = False
    is_read: bool = False
    is_important: bool = False


class GuidelineIngestInput(BaseModel):
    """Payload for POST /database/guidelines/ingest."""

    file_path: str


class TagCreateInput(BaseModel):
    """Payload for POST /database/tags."""

    name: str


class GuidelineCreateInput(BaseModel):
    """Payload for POST /database/guidelines."""

    file_path: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.delete(
    "/clear",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear all database data (relational + vector)",
)
async def clear_database(db=Depends(get_db_manager)) -> None:
    """
    Delete all records from every relational table and every ChromaDB collection.

    This is a destructive, irreversible operation — use with care.
    """
    try:
        await db.clear_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database clear failed: {exc}")


@router.get("/health", summary="Database health check")
async def database_health(db=Depends(get_db_manager)) -> dict:
    """
    Check connectivity to both relational DB and ChromaDB.

    Returns {"relational": "ok"|"error", "chroma": "ok"|"error"}.
    """
    relational_ok = await db.relational.health_check()
    chroma_ok = await db.vector.health_check()
    return {
        "relational": "ok" if relational_ok else "error",
        "chroma": "ok" if chroma_ok else "error",
    }

# ---------------------------------------------------------------------------
# Sync / refresh
# ---------------------------------------------------------------------------


@router.get(
    "/sync/check",
    response_model=SyncReport,
    summary="Compare PostgreSQL vs ChromaDB email IDs",
)
async def sync_check(db=Depends(get_db_manager)) -> SyncReport:
    """
    Returns lists of emails missing in ChromaDB (need embedding) and
    orphaned in ChromaDB (no matching PG record).
    """
    return await db.sync_check()


@router.post(
    "/sync/refresh",
    response_model=RefreshReport,
    summary="Re-embed all emails/guidelines with embedded_at IS NULL",
)
async def sync_refresh(db=Depends(get_db_manager)) -> RefreshReport:
    """Re-embed any email or guideline that has not yet been embedded."""
    return await db.refresh()


@router.post(
    "/sync/full",
    status_code=202,
    summary="Full resync: re-embed missing, delete orphaned Chroma documents",
)
async def full_resync(db=Depends(get_db_manager)) -> dict:
    """
    Runs sync_check(), re-embeds missing records, and deletes orphaned
    ChromaDB documents. Returns immediately after completion.
    """
    await db.full_resync()
    return {"status": "completed"}


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------


@router.get(
    "/search",
    response_model=list[SearchResult],
    summary="Semantic search across emails and guidelines",
)
async def search(
    q: Annotated[str, Query(description="Semantic search query")],
    top_k: Annotated[int, Query(ge=1, le=50)] = 5,
    db=Depends(get_db_manager),
) -> list[SearchResult]:
    """Search email_chunks and guideline_chunks by semantic similarity."""
    return await db.search(query=q, top_k=top_k)


# ---------------------------------------------------------------------------
# Relational listing — Senders
# ---------------------------------------------------------------------------


@router.get(
    "/senders",
    response_model=list[SenderModel],
    summary="List all senders (SELECT * FROM senders)",
)
async def list_senders(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db=Depends(get_db_manager),
) -> list[SenderModel]:
    return await db.relational.list_senders(limit=limit, offset=offset)


@router.get(
    "/senders/{sender_id}",
    response_model=SenderModel,
    summary="Get a single sender by UUID",
)
async def get_sender(sender_id: UUID, db=Depends(get_db_manager)) -> SenderModel:
    sender = await db.relational.get_sender(sender_id)
    if sender is None:
        raise HTTPException(status_code=404, detail=f"Sender {sender_id} not found")
    return sender


@router.delete(
    "/senders/{sender_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a sender by UUID",
)
async def delete_sender(sender_id: UUID, db=Depends(get_db_manager)) -> None:
    deleted = await db.delete_sender(sender_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Sender {sender_id} not found")


# ---------------------------------------------------------------------------
# Relational listing — Threads
# ---------------------------------------------------------------------------


@router.get(
    "/threads",
    response_model=list[ThreadModel],
    summary="List all threads (SELECT * FROM threads)",
)
async def list_threads(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[str | None, Query(description="Filter by status: open/closed/pending")] = None,
    db=Depends(get_db_manager),
) -> list[ThreadModel]:
    return await db.relational.list_threads(limit=limit, offset=offset, status=status)


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadModel,
    summary="Get a single thread by UUID",
)
async def get_thread(thread_id: UUID, db=Depends(get_db_manager)) -> ThreadModel:
    thread = await db.relational.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return thread


@router.delete(
    "/threads/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a thread by UUID",
)
async def delete_thread(thread_id: UUID, db=Depends(get_db_manager)) -> None:
    deleted = await db.delete_thread(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")


# ---------------------------------------------------------------------------
# Relational listing — Emails
# ---------------------------------------------------------------------------


@router.get(
    "/emails",
    response_model=list[EmailModel],
    summary="List emails (SELECT * FROM emails) with optional filters",
)
async def list_emails(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[str | None, Query(description="new/in_progress/resolved/archived")] = None,
    sender_id: Annotated[UUID | None, Query(description="Filter by sender UUID")] = None,
    thread_id: Annotated[UUID | None, Query(description="Filter by thread UUID")] = None,
    db=Depends(get_db_manager),
) -> list[EmailModel]:
    return await db.relational.list_emails(
        limit=limit,
        offset=offset,
        status=status,
        sender_id=sender_id,
        thread_id=thread_id,
    )


@router.get(
    "/emails/{email_id}",
    response_model=EmailModel,
    summary="Get a single email by UUID",
)
async def get_email(email_id: UUID, db=Depends(get_db_manager)) -> EmailModel:
    email = await db.relational.get_email(email_id)
    if email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")
    return email


@router.delete(
    "/emails/{email_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an email by UUID (also removes its Chroma chunks)",
)
async def delete_email(email_id: UUID, db=Depends(get_db_manager)) -> None:
    deleted = await db.delete_email(email_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")


@router.get(
    "/emails/{email_id}/cache",
    response_model=AgentCacheModel,
    summary="Get the agent cache entry for an email",
)
async def get_email_cache(email_id: UUID, db=Depends(get_db_manager)) -> AgentCacheModel:
    cache = await db.relational.get_agent_cache(email_id)
    if cache is None:
        raise HTTPException(
            status_code=404,
            detail=f"No agent cache entry for email {email_id}",
        )
    return cache


# ---------------------------------------------------------------------------
# Relational listing — Guidelines
# ---------------------------------------------------------------------------


@router.post(
    "/guidelines",
    response_model=GuidelineModel,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a guideline file (PDF or Markdown) into the database and embed it",
)
async def create_guideline(
    body: GuidelineCreateInput, db=Depends(get_db_manager)
) -> GuidelineModel:
    try:
        return await db.ingest_guideline(body.file_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get(
    "/guidelines",
    response_model=list[GuidelineModel],
    summary="List all guidelines (SELECT * FROM guidelines)",
)
async def list_guidelines(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db=Depends(get_db_manager),
) -> list[GuidelineModel]:
    return await db.relational.list_guidelines(limit=limit, offset=offset)


@router.get(
    "/guidelines/{guideline_id}",
    response_model=GuidelineModel,
    summary="Get a single guideline by UUID",
)
async def get_guideline(guideline_id: UUID, db=Depends(get_db_manager)) -> GuidelineModel:
    guideline = await db.relational.get_guideline(guideline_id)
    if guideline is None:
        raise HTTPException(
            status_code=404, detail=f"Guideline {guideline_id} not found"
        )
    return guideline


@router.delete(
    "/guidelines/{guideline_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a guideline by UUID (also removes its Chroma chunks)",
)
async def delete_guideline(guideline_id: UUID, db=Depends(get_db_manager)) -> None:
    deleted = await db.delete_guideline(guideline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Guideline {guideline_id} not found")


# ---------------------------------------------------------------------------
# Relational listing — Tags
# ---------------------------------------------------------------------------


@router.get(
    "/tags",
    response_model=list[TagModel],
    summary="List all tags (SELECT * FROM tags)",
)
async def list_tags(db=Depends(get_db_manager)) -> list[TagModel]:
    return await db.relational.list_tags()


@router.post(
    "/tags",
    response_model=TagModel,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tag",
)
async def create_tag(body: TagCreateInput, db=Depends(get_db_manager)) -> TagModel:
    try:
        return await db.relational.insert_tag(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ---------------------------------------------------------------------------
# Vector DB inspection — ChromaDB
# ---------------------------------------------------------------------------


@router.get(
    "/chroma/collections",
    summary="List ChromaDB collection names and document counts",
)
async def chroma_collections(db=Depends(get_db_manager)) -> list[dict]:
    """Returns [{name, count}] for all four managed collections."""
    return await db.vector.list_collections()


@router.get(
    "/chroma/email-chunks",
    response_model=list[ChromaDocumentItem],
    summary="List documents from the email_chunks collection",
)
async def chroma_email_chunks(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    email_id: Annotated[str | None, Query(description="Filter by email UUID")] = None,
    db=Depends(get_db_manager),
) -> list[ChromaDocumentItem]:
    return await db.vector.list_email_chunks(limit=limit, offset=offset, email_id=email_id)


@router.get(
    "/chroma/guideline-chunks",
    response_model=list[ChromaDocumentItem],
    summary="List documents from the guideline_chunks collection",
)
async def chroma_guideline_chunks(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    guideline_id: Annotated[str | None, Query(description="Filter by guideline UUID")] = None,
    db=Depends(get_db_manager),
) -> list[ChromaDocumentItem]:
    return await db.vector.list_guideline_chunks(
        limit=limit, offset=offset, guideline_id=guideline_id
    )


@router.get(
    "/chroma/thread-summaries",
    response_model=list[ChromaDocumentItem],
    summary="List documents from the thread_summaries collection",
)
async def chroma_thread_summaries(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    db=Depends(get_db_manager),
) -> list[ChromaDocumentItem]:
    return await db.vector.list_thread_summaries(limit=limit, offset=offset)


@router.get(
    "/chroma/sender-summaries",
    response_model=list[ChromaDocumentItem],
    summary="List documents from the sender_summaries collection",
)
async def chroma_sender_summaries(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    db=Depends(get_db_manager),
) -> list[ChromaDocumentItem]:
    return await db.vector.list_sender_summaries(limit=limit, offset=offset)
