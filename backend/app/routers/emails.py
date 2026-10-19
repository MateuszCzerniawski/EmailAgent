"""
Emails router — list and analyse Gmail messages.

Endpoints
---------
GET /emails
    List emails stored in the RDB with their cached LLM analyses (if any).
    Query params: limit, offset, status, sender_id, thread_id.

GET /emails/analyse/{email_id}
    Run (or retrieve) LLM analysis for an email that already exists in the RDB.
    The email_id is the RDB UUID.  Returns the cached AgentCacheModel if one
    exists, unless force_regeneration=true is passed.
    Raises 404 if the email is not in the RDB.

GET /emails/raw
    [Test] Fetch email headers from Gmail without LLM analysis.

GET /emails/raw/{email_id}
    [Test] Fetch a single email from Gmail with full body, ingest into RDB,
    no LLM analysis.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from email.utils import parseaddr
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import AppConfig
from app.dependencies import get_config, get_db_manager, get_llm_service, get_postbox
from app.logger import get_logger
from app.models.llm_response import LLMResponse, ThreadLLMResponse
from app.models.mail import Mail
from app.postbox.base import EmailNotFoundError, PostboxAuthError
from app.postbox.gmail import GmailPostbox
from app.services.llm_service import LLMAnalysisError, LLMService
from database.exceptions import DuplicateEmailError
from database.models.domain import AgentCacheModel, ChromaDocumentItem, EmailModel, SenderModel, ThreadModel

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class EmailWithAnalysisResponse(BaseModel):
    """List-view response: RDB email + sender + cached analysis (None if not yet analysed)."""
    email: EmailModel
    sender: SenderModel | None = None
    analysis: AgentCacheModel | None


class ThreadWithSenderResponse(BaseModel):
    """Thread list-view response: RDB thread + its sender + most recent email ID."""
    thread: ThreadModel
    sender: SenderModel | None = None
    latest_email_id: UUID | None = None
    latest_email_importance: int | None = None


class AnalyseEmailResponse(BaseModel):
    """Analyse-view response: RDB email + analysis (always present) + timing."""
    email: EmailModel
    analysis: AgentCacheModel
    processing_time_ms: float
    thread_summary: str | None = None


class RawEmailDetailResponse(BaseModel):
    """Raw detail-view response: complete Gmail body, no LLM analysis."""
    mail: Mail
    processing_time_ms: float


class EmailInThreadItem(BaseModel):
    """Lightweight email summary used by the history timeline in the detail view."""
    id: UUID
    subject: str
    sender_name: str | None = None
    sender_email: str | None = None
    received_at: datetime | None = None
    body_preview: str = ""


class ThreadSummaryResponse(BaseModel):
    """Response from POST /threads/{thread_id}/summary."""
    thread_id: UUID
    summary: str
    cached: bool
    message_count: int
    actions: list[dict] = []


class SenderSummaryResponse(BaseModel):
    """Response from POST /senders/{sender_id}/summary."""
    sender_id: UUID
    summary: str
    cached: bool


class ThreadRagCompareResponse(BaseModel):
    """Response from GET /threads/{thread_id}/analyse/compare."""
    thread_id: UUID
    rag: ThreadLLMResponse | None
    no_rag: ThreadLLMResponse | None


class ThreadRagInspectResponse(BaseModel):
    """Response from GET /threads/{thread_id}/analyse/rag-inspect."""
    thread_id: UUID
    query: str
    top_k_requested: int
    min_score_threshold: float
    chunks_retrieved: int
    chunks_passed: int
    chunks_dropped: int
    unique_source_threads: int
    chunks: list[RagChunkDebug]
    formatted_context: str | None


class SendEmailRequest(BaseModel):
    to: str
    body: str
    subject: str = "No subject"
    thread_id: str | None = None
    in_reply_to: str | None = None


class SendEmailResponse(BaseModel):
    message_id: str
    to: str
    subject: str


class SenderTagsResponse(BaseModel):
    """Response from GET /senders/{sender_id}/tags."""
    tags: list[str]


class RagCompareResponse(BaseModel):
    """Response from GET /analyse/{email_id}/compare."""
    email_id: UUID
    rag: LLMResponse | None
    no_rag: LLMResponse | None


class RagChunkDebug(BaseModel):
    """Debug info for a single retrieved chunk."""
    chunk_id: str
    email_id: str
    score: float
    chunk_index: int
    received_at: str
    document: str
    passed_threshold: bool
    has_cached_analysis: bool
    cached_analysis: str | None


class RagInspectResponse(BaseModel):
    """Response from GET /analyse/{email_id}/rag-inspect."""
    email_id: UUID
    query: str
    top_k_requested: int
    min_score_threshold: float
    chunks_retrieved: int
    chunks_passed: int
    chunks_dropped: int
    unique_source_emails: int
    chunks: list[RagChunkDebug]
    formatted_context: str | None


class ThreadSummaryUpdateRequest(BaseModel):
    """Request body for PUT /threads/{thread_id}/summary."""
    summary: str


class SuggestedReplyResponse(BaseModel):
    """Response from POST/PUT /emails/{email_id}/suggested-reply."""
    email_id: UUID
    suggested_reply: str


class ReplyUpdateRequest(BaseModel):
    """Request body for PUT /emails/{email_id}/suggested-reply."""
    reply: str


class MetadataUpdateRequest(BaseModel):
    """Request body for PUT /emails/{email_id}/metadata."""
    tags: list[str]
    importance: int | None = None


class MetadataUpdateResponse(BaseModel):
    """Response from PUT /emails/{email_id}/metadata."""
    email_id: UUID
    tags: list[str]
    importance: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_mail_from_db(db_email: EmailModel, sender: SenderModel | None) -> Mail:
    """
    Construct a provider-agnostic Mail from an RDB EmailModel so it can be
    passed to LLMService.analyze(), which expects a Mail object.
    """
    if sender and sender.display_name:
        sender_str = f"{sender.display_name} <{sender.email}>"
    elif sender:
        sender_str = sender.email
    else:
        sender_str = "unknown@unknown"

    return Mail(
        id=db_email.message_id,
        thread_id=str(db_email.thread_id) if db_email.thread_id else "",
        subject=db_email.subject,
        sender=sender_str,
        recipients=db_email.recipients,
        date=db_email.received_at,
        body_plain=db_email.body or None,
        body_html=db_email.body_html,
    )


def _analyse_safely(
    llm: LLMService,
    mail: Mail,
    rag_context: str | None = None,
    guidelines: str | None = None,
    cache_key: str | None = None,
    bypass_cache: bool = False,
) -> LLMResponse:
    """
    Run LLM analysis, returning a graceful fallback LLMResponse on failure.
    """
    try:
        return llm.analyze(mail, rag_context=rag_context, guidelines=guidelines, _cache_key=cache_key, bypass_cache=bypass_cache)
    except LLMAnalysisError as exc:
        logger.warning("LLM analysis failed for email %s: %s", mail.id, exc)
        return LLMResponse(
            email_id=mail.id,
            summary=f"Analysis failed: {exc}",
            category="other",
            sentiment="neutral",
            key_action_required=False,
            confidence=0.0,
        )


async def _build_guidelines_context(db) -> str:
    """
    Fetch ALL guidelines from the relational DB and format them for the system prompt.
    Guidelines are universal rules — they apply to every email regardless of content.
    """
    guidelines = await db.relational.list_guidelines(limit=50)
    if not guidelines:
        return "(no guidelines defined)"
    parts = []
    for g in guidelines:
        content = g.content[:2000]  # safety cap per guideline
        parts.append(f"[{g.filename}]\n{content}")
    logger.debug("Injecting %d guideline(s) into system prompt", len(guidelines))
    return "\n\n---\n\n".join(parts)


async def _build_rag_context(
    email_id: UUID, mail: Mail, db, top_k: int = 5, min_score: float = 0.3
) -> str | None:
    """
    Retrieve semantically similar email chunks from ChromaDB and look up their
    cached analyses, returning a formatted context string for the LLM prompt.
    Chunks with a similarity score below min_score are discarded.
    Returns None if no chunks pass the threshold.
    """
    query = f"{mail.subject}\n\n{(mail.body_plain or '')[:500]}"
    similar = await db.vector.search_emails(
        query=query,
        top_k=top_k,
        filters={"email_id": {"$ne": str(email_id)}},
    )
    if not similar:
        return None

    seen: set[str] = set()
    parts: list[str] = []
    for result in similar:
        if result.score < min_score:
            continue
        similar_email_id = result.metadata.get("email_id")
        if not similar_email_id or similar_email_id in seen:
            continue
        seen.add(similar_email_id)
        cached = await db.relational.get_agent_cache(UUID(similar_email_id))
        part = f"[Score: {result.score:.2f}]\n{result.document}"
        if cached:
            part += f"\nAnalysis: {cached.analysis}"
        parts.append(part)

    return "\n\n---\n\n".join(parts) if parts else None


def _llm_response_to_cache(
    db_email_id: UUID,
    analysis: LLMResponse,
    existing: AgentCacheModel | None = None,
) -> AgentCacheModel:
    suggested_action = "reply" if analysis.key_action_required else "ignore"
    return AgentCacheModel(
        email_id=db_email_id,
        analysis=analysis.summary,
        summary=analysis.brief_summary,
        suggested_action=suggested_action,
        confidence_score=analysis.confidence,
        model_used="llm_service",
        actions=[action.model_dump() for action in analysis.actions],
        suggested_tags=existing.suggested_tags if (existing and existing.suggested_tags) else analysis.tags,
        importance_score=existing.importance_score if existing else None,
    )


async def _sync_thread_counts(
    thread_id_map: dict[str, UUID], postbox: GmailPostbox, db
) -> dict[str, int]:
    """
    For each gmail_thread_id → db_thread_uuid pair, fetch the true message count
    from Gmail and write it back to the DB thread record.

    Returns a dict of gmail_thread_id → synced count for callers that want to
    apply the updated values in-memory without a second DB read.
    """
    synced: dict[str, int] = {}
    for gmail_thread_id, db_thread_uuid in thread_id_map.items():
        try:
            count = postbox.get_thread_message_count(gmail_thread_id)
        except Exception as exc:
            logger.warning("Could not fetch Gmail thread count for %s: %s", gmail_thread_id, exc)
            continue
        synced[gmail_thread_id] = count
        try:
            thread = await db.relational.get_thread(db_thread_uuid)
            if thread and thread.mail_count != count:
                await db.relational.upsert_thread(thread.model_copy(update={"mail_count": count}))
        except Exception as exc:
            logger.warning("Could not update thread mail_count for %s: %s", db_thread_uuid, exc)
    return synced


async def _build_thread_rag_context(
    thread_id: UUID,
    messages: list,
    db,
    top_k: int = 5,
    min_score: float = 0.3,
) -> str | None:
    """
    Retrieve semantically similar email chunks from OTHER threads in ChromaDB
    and return a formatted context string for the thread analysis prompt.
    """
    if not messages:
        return None

    if isinstance(messages[0], ChromaDocumentItem):
        query = "\n\n".join(m.document_snippet for m in messages[:3])[:800]
    else:
        parts = [f"{m.subject}\n{(m.body_plain or '')[:200]}" for m in messages[:3]]
        query = "\n\n".join(parts)[:800]

    similar = await db.vector.search_emails(
        query=query,
        top_k=top_k,
        filters={"thread_id": {"$ne": str(thread_id)}},
    )
    if not similar:
        return None

    seen: set[str] = set()
    context_parts: list[str] = []
    for result in similar:
        if result.score < min_score:
            continue
        similar_email_id = result.metadata.get("email_id")
        if not similar_email_id or similar_email_id in seen:
            continue
        seen.add(similar_email_id)
        cached = await db.relational.get_agent_cache(UUID(similar_email_id))
        part = f"[Score: {result.score:.2f}]\n{result.document}"
        if cached:
            part += f"\nAnalysis: {cached.analysis}"
        context_parts.append(part)

    return "\n\n---\n\n".join(context_parts) if context_parts else None


async def _build_mails_for_thread(thread_id: UUID, db) -> list[Mail]:
    """
    Fetch all emails for a thread and build Mail objects in chronological order.

    body_plain is set to agent_cache.analysis when available (pre-processed
    summary text, less noisy than raw body for LLM re-summarization),
    falling back to the stored email body.
    """
    emails = await db.relational.list_emails(limit=100, thread_id=thread_id)
    emails = list(reversed(emails))  # list_emails returns desc; reverse to asc
    mails: list[Mail] = []
    for db_email in emails:
        sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
        cache = await db.relational.get_agent_cache(db_email.id)
        base_mail = _build_mail_from_db(db_email, sender)
        body_override = cache.analysis if cache else (db_email.body or None)
        mails.append(base_mail.model_copy(update={"body_plain": body_override}))
    return mails


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[EmailWithAnalysisResponse],
    summary="Fetch emails from Gmail, upsert to RDB, return with LLM analyses",
)
async def list_emails(
    max_results: Annotated[int, Query(ge=1, le=500)] = None,
    query: Annotated[str, Query(description="Gmail search query")] = None,
    postbox: GmailPostbox = Depends(get_postbox),
    llm: LLMService = Depends(get_llm_service),
    config: AppConfig = Depends(get_config),
    db=Depends(get_db_manager),
):
    """
    Fetch emails from Gmail, upsert each into the RDB, then return them with
    LLM analyses.

    For each email:
    - If an agent_cache entry already exists in the RDB it is returned as-is
      (no LLM call).
    - Otherwise the LLM analyses the email body and the result is saved to
      agent_cache before being returned.
    """
    max_results = max_results or config.gmail.max_results
    query = query if query is not None else config.gmail.query

    try:
        mails = postbox.fetch_emails(max_results=max_results, query=query)
    except PostboxAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        logger.error("fetch_emails failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    results = []
    thread_id_map: dict[str, UUID] = {}  # gmail_thread_id -> db thread UUID
    for mail in mails:
        # 1. Upsert email into RDB
        display_name, sender_email = parseaddr(mail.sender)
        db_email = None
        try:
            db_email = await db.ingest_email({
                "message_id": mail.id,
                "gmail_thread_id": mail.thread_id or None,
                "thread_subject": mail.subject,
                "sender_email": sender_email or mail.sender,
                "sender_name": display_name or None,
                "subject": mail.subject,
                "body": mail.body_plain or mail.snippet or "",
                "body_html": mail.body_html,
                "recipients": mail.recipients,
                "received_at": mail.date,
                "has_attachments": mail.has_attachments,
            })
        except DuplicateEmailError:
            db_email = await db.relational.get_email_by_message_id(mail.id)
        except Exception as exc:
            logger.error("DB ingest failed for email %s: %s", mail.id, exc)

        if db_email is None:
            continue

        if mail.thread_id and db_email.thread_id:
            thread_id_map[mail.thread_id] = db_email.thread_id

        # 2. Return cached analysis or generate a new one
        cache_entry = await db.relational.get_agent_cache(db_email.id)
        if cache_entry is None:
            analysis = _analyse_safely(llm, mail)
            try:
                cache_entry = await db.relational.upsert_agent_cache(
                    _llm_response_to_cache(db_email.id, analysis)
                )
                # Backfill thread.summary from brief analysis if not yet set
                if db_email.thread_id and analysis.brief_summary:
                    thread = await db.relational.get_thread(db_email.thread_id)
                    if thread and not thread.summary:
                        await db.relational.update_thread_summary(db_email.thread_id, analysis.brief_summary)
            except Exception as exc:
                logger.error("Agent cache upsert failed for email %s: %s", mail.id, exc)

        sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
        results.append(EmailWithAnalysisResponse(email=db_email, sender=sender, analysis=cache_entry))

    await _sync_thread_counts(thread_id_map, postbox, db)
    return results


@router.get(
    "/raw",
    response_model=list[Mail],
    summary="[Test] List emails without LLM analysis",
)
async def list_emails_raw(
    max_results: Annotated[int, Query(ge=1, le=500)] = None,
    query: Annotated[str, Query(description="Gmail search query")] = None,
    postbox: GmailPostbox = Depends(get_postbox),
    config: AppConfig = Depends(get_config),
    db=Depends(get_db_manager),
):
    """
    Fetch emails (headers + snippet) from Gmail, ingest each into the database,
    and return the list without running LLM analysis.

    Emails already present in the database are silently skipped.
    Useful for testing Gmail connectivity and bulk-importing email metadata.
    """
    max_results = max_results or config.gmail.max_results
    query = query if query is not None else config.gmail.query

    try:
        mails = postbox.fetch_emails(max_results=max_results, query=query)
    except PostboxAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        logger.error("fetch_emails (raw) failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    thread_id_map: dict[str, UUID] = {}
    for mail in mails:
        display_name, sender_email = parseaddr(mail.sender)
        db_email = None
        try:
            db_email = await db.ingest_email({
                "message_id": mail.id,
                "gmail_thread_id": mail.thread_id or None,
                "thread_subject": mail.subject,
                "sender_email": sender_email or mail.sender,
                "sender_name": display_name or None,
                "subject": mail.subject,
                "body": mail.body_plain or mail.snippet or "",
                "body_html": mail.body_html,
                "recipients": mail.recipients,
                "received_at": mail.date,
                "has_attachments": mail.has_attachments,
            })
            logger.debug("Email %s ingested", mail.id)
        except DuplicateEmailError:
            db_email = await db.relational.get_email_by_message_id(mail.id)
            logger.debug("Email %s already in database, skipping ingest", mail.id)
        except Exception as exc:
            logger.error("DB ingest failed for email %s: %s", mail.id, exc)

        if db_email and mail.thread_id and db_email.thread_id:
            thread_id_map[mail.thread_id] = db_email.thread_id

    await _sync_thread_counts(thread_id_map, postbox, db)
    return mails


@router.get(
    "/raw/{email_id}",
    response_model=RawEmailDetailResponse,
    summary="[Test] Get single email with full body, no LLM analysis",
)
async def get_email_raw(
    email_id: str,
    postbox: GmailPostbox = Depends(get_postbox),
    db=Depends(get_db_manager),
):
    """
    Fetch a single email by ID including the full body, without LLM analysis.
    Also uploads the email content to the database.

    Useful for inspecting raw email content and testing Gmail connectivity
    without incurring LLM API costs.
    """
    try:
        start = time.monotonic()
        mail = postbox.get_email(email_id)
        elapsed_ms = (time.monotonic() - start) * 1000
    except EmailNotFoundError:
        raise HTTPException(status_code=404, detail=f"Email not found: {email_id}")
    except PostboxAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        logger.error("get_email_raw(%s) failed: %s", email_id, exc)
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    display_name, sender_email = parseaddr(mail.sender)
    try:
        await db.ingest_email({
            "message_id": mail.id,
            "gmail_thread_id": mail.thread_id or None,
            "thread_subject": mail.subject,
            "sender_email": sender_email or mail.sender,
            "sender_name": display_name or None,
            "subject": mail.subject,
            "body": mail.body_plain or mail.snippet or "",
            "body_html": mail.body_html,
            "recipients": mail.recipients,
            "received_at": mail.date,
            "has_attachments": mail.has_attachments,
        })
        logger.debug("Email %s ingested", mail.id)
    except DuplicateEmailError:
        logger.debug("Email %s already in database, skipping ingest", mail.id)
    except Exception as exc:
        logger.error("DB ingest failed for email %s: %s", mail.id, exc)

    return RawEmailDetailResponse(mail=mail, processing_time_ms=round(elapsed_ms, 2))


@router.get(
    "/analyse/{email_id}",
    response_model=AnalyseEmailResponse,
    summary="Analyse an email that exists in the RDB",
)
async def analyse_email(
    email_id: UUID,
    force_regeneration: Annotated[bool, Query(description="Re-run LLM even if a cached analysis exists")] = False,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
    config: AppConfig = Depends(get_config),
):
    """
    Return the LLM analysis for the given email (identified by its RDB UUID).

    Behaviour:
    - If the email does not exist in the RDB → 404.
    - If a cached analysis exists in agent_cache and force_regeneration=false
      → return the cached result immediately (no LLM call, no Gmail connection).
    - Otherwise → connect to Gmail to fetch the full body, run LLM analysis,
      persist the result to agent_cache, and return it.
    """
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found in database")

    thread = await db.relational.get_thread(db_email.thread_id) if db_email.thread_id else None
    thread_summary = thread.summary if thread else None

    # Return cached analysis if available and not forcing regeneration.
    # Postbox is NOT opened here — no Gmail connection for cached results.
    if not force_regeneration:
        cached = await db.relational.get_agent_cache(email_id)
        if cached is not None:
            logger.info("Returning cached analysis for email %s", email_id)
            return AnalyseEmailResponse(
                email=db_email,
                analysis=cached,
                processing_time_ms=0.0,
                thread_summary=thread_summary,
            )

    # No cache (or force_regeneration): connect to Gmail to fetch the full body.
    sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
    mail = _build_mail_from_db(db_email, sender)

    guidelines_ctx, rag_context = await asyncio.gather(
        _build_guidelines_context(db),
        _build_rag_context(email_id, mail, db, top_k=config.rag.top_k, min_score=config.rag.min_score),
    )

    postbox = GmailPostbox(config)
    postbox.connect()
    try:
        mail_raw = await get_email_raw(mail.id, postbox=postbox, db=db)
    finally:
        postbox.disconnect()

    mail = mail.model_copy(update={
        "body_plain": mail_raw.mail.body_plain,
        "body_html": mail_raw.mail.body_html,
    })

    start = time.monotonic()
    analysis = _analyse_safely(llm, mail, rag_context=rag_context, guidelines=guidelines_ctx, bypass_cache=force_regeneration)
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info("received analysis: %s", analysis)

    # Persist to agent_cache, preserving manually-set importance_score
    existing_cache = await db.relational.get_agent_cache(db_email.id)
    cache_entry = _llm_response_to_cache(db_email.id, analysis, existing=existing_cache)
    try:
        cache_entry = await db.relational.upsert_agent_cache(cache_entry)
        logger.debug("Agent cache upserted for email %s", email_id)
    except Exception as exc:
        logger.error("Agent cache upsert failed for email %s: %s", email_id, exc)

    # Index action-item tasks into vector DB (fire-and-forget; failure is non-fatal)
    if analysis.actions:
        try:
            await db.upsert_tasks_for_email(
                email_id=db_email.id,
                tasks=analysis.actions,
                subject=mail.subject or "",
                sender_id=str(db_email.sender_id) if db_email.sender_id else "",
                received_at=mail.date.isoformat() if mail.date else "",
            )
        except Exception as exc:
            logger.warning("Task indexing failed for %s: %s", db_email.id, exc)

    # Backfill thread.summary from brief analysis; always overwrite on force_regeneration
    if db_email.thread_id and analysis.brief_summary:
        thread = await db.relational.get_thread(db_email.thread_id)
        if thread and (force_regeneration or not thread.summary):
            await db.relational.update_thread_summary(db_email.thread_id, analysis.brief_summary)
            thread_summary = analysis.brief_summary

    return AnalyseEmailResponse(
        email=db_email,
        analysis=cache_entry,
        processing_time_ms=round(elapsed_ms, 2),
        thread_summary=thread_summary,
    )


@router.get(
    "/analyse/{email_id}/rag",
    response_model=LLMResponse,
    summary="Analyse an email using RAG context from similar emails",
)
async def analyse_email_rag(
    email_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
    config: AppConfig = Depends(get_config),
):
    """
    Run LLM analysis with RAG: retrieves the top-k most similar email chunks
    from ChromaDB and injects them as context. Result is cached under the key
    "rag:<message_id>" so the compare endpoint can fetch it without re-running.
    """
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")
    sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
    mail = _build_mail_from_db(db_email, sender)
    rag_context = await _build_rag_context(email_id, mail, db, top_k=config.rag.top_k, min_score=config.rag.min_score)
    return _analyse_safely(llm, mail, rag_context=rag_context, cache_key=f"rag:{mail.id}")


@router.get(
    "/analyse/{email_id}/no-rag",
    response_model=LLMResponse,
    summary="Analyse an email without RAG context",
)
async def analyse_email_no_rag(
    email_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
):
    """
    Run LLM analysis without any RAG context — plain email body only. Result is
    cached under "no-rag:<message_id>" so the compare endpoint can fetch it.
    """
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")
    sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
    mail = _build_mail_from_db(db_email, sender)
    return _analyse_safely(llm, mail, cache_key=f"no-rag:{mail.id}")


@router.get(
    "/analyse/{email_id}/compare",
    response_model=RagCompareResponse,
    summary="Compare RAG vs no-RAG analyses side by side",
)
async def compare_email_analysis(
    email_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
):
    """
    Return both cached analyses (RAG and no-RAG) for the given email side by side.
    No LLM call is made — call /analyse/{email_id}/rag and /analyse/{email_id}/no-rag
    first to populate the cache, then use this endpoint to compare the results.
    Fields are null if the corresponding analysis has not been run yet.
    """
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")
    sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
    mail = _build_mail_from_db(db_email, sender)
    return RagCompareResponse(
        email_id=email_id,
        rag=llm.get_cached_analysis(f"rag:{mail.id}"),
        no_rag=llm.get_cached_analysis(f"no-rag:{mail.id}"),
    )


@router.get(
    "/analyse/{email_id}/rag-inspect",
    response_model=RagInspectResponse,
    summary="[Test] Inspect RAG retrieval for an email without running the LLM",
)
async def rag_inspect(
    email_id: UUID,
    db=Depends(get_db_manager),
    config: AppConfig = Depends(get_config),
):
    """
    Debug endpoint: shows exactly what the RAG retrieval step finds before the LLM
    is called. No LLM call is made.

    Returns:
    - The query string sent to the vector store
    - Every chunk retrieved, with its similarity score, source email, position, and text
    - Whether each source email has a cached analysis, and what it says
    - The final formatted context string that would be injected into the prompt
    """
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")

    sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
    mail = _build_mail_from_db(db_email, sender)

    query = f"{mail.subject}\n\n{(mail.body_plain or '')[:500]}"
    top_k = config.rag.top_k
    min_score = config.rag.min_score

    similar = await db.vector.search_emails(
        query=query,
        top_k=top_k,
        filters={"email_id": {"$ne": str(email_id)}},
    )

    chunk_debugs: list[RagChunkDebug] = []
    context_parts: list[str] = []
    seen: set[str] = set()

    for result in similar:
        meta = result.metadata
        source_email_id = meta.get("email_id", "")
        cached = await db.relational.get_agent_cache(UUID(source_email_id)) if source_email_id else None
        passed = result.score >= min_score

        chunk_debugs.append(RagChunkDebug(
            chunk_id=f"{source_email_id}__chunk_{meta.get('chunk_index', '?')}",
            email_id=source_email_id,
            score=round(result.score, 4),
            chunk_index=int(meta.get("chunk_index", 0)),
            received_at=meta.get("received_at", ""),
            document=result.document,
            passed_threshold=passed,
            has_cached_analysis=cached is not None,
            cached_analysis=cached.analysis if cached else None,
        ))

        if passed and source_email_id not in seen:
            seen.add(source_email_id)
            part = f"[Score: {result.score:.2f}]\n{result.document}"
            if cached:
                part += f"\nAnalysis: {cached.analysis}"
            context_parts.append(part)

    formatted_context = "\n\n---\n\n".join(context_parts) if context_parts else None
    chunks_passed = sum(1 for c in chunk_debugs if c.passed_threshold)

    return RagInspectResponse(
        email_id=email_id,
        query=query,
        top_k_requested=top_k,
        min_score_threshold=min_score,
        chunks_retrieved=len(similar),
        chunks_passed=chunks_passed,
        chunks_dropped=len(similar) - chunks_passed,
        unique_source_emails=len(seen),
        chunks=chunk_debugs,
        formatted_context=formatted_context,
    )


@router.get(
    "/senders",
    response_model=list[SenderModel],
    summary="List all known senders",
)
async def list_senders(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db=Depends(get_db_manager),
):
    """Return all senders stored in the database, ordered by most recently created."""
    return await db.relational.list_senders(limit=limit, offset=offset)


@router.get(
    "/senders/{sender_id}",
    response_model=SenderModel,
    summary="Get a single sender by RDB UUID",
)
async def get_sender(
    sender_id: UUID,
    db=Depends(get_db_manager),
):
    """Return the sender record for the given UUID. Raises 404 if not found."""
    sender = await db.relational.get_sender(sender_id)
    if sender is None:
        raise HTTPException(status_code=404, detail=f"Sender {sender_id} not found")
    return sender


@router.get(
    "/senders/{sender_id}/tags",
    response_model=SenderTagsResponse,
    summary="Get all tag names across a sender's emails",
)
async def get_sender_tags(
    sender_id: UUID,
    db=Depends(get_db_manager),
):
    """Return deduplicated, alphabetically ordered tag names for all emails from this sender."""
    sender = await db.relational.get_sender(sender_id)
    if sender is None:
        raise HTTPException(status_code=404, detail=f"Sender {sender_id} not found")
    tags = await db.relational.get_tags_by_sender(sender_id)
    return SenderTagsResponse(tags=[t.name for t in tags])


@router.post(
    "/senders/{sender_id}/summary",
    response_model=SenderSummaryResponse,
    summary="Generate or refresh the LLM summary for a sender",
)
async def generate_sender_summary(
    sender_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
):
    """
    Analyse the sender's most recent emails (up to 20) and generate a plain-text
    summary. Always force-regenerates — this endpoint is called explicitly by the
    user via a Generate/Refresh button.

    The result is persisted to SenderORM.summary and cached in EmailCache.
    """
    sender = await db.relational.get_sender(sender_id)
    if sender is None:
        raise HTTPException(status_code=404, detail=f"Sender {sender_id} not found")

    db_emails = await db.relational.get_emails_by_sender(sender_id)
    db_emails = db_emails[:20]
    if not db_emails:
        raise HTTPException(status_code=422, detail="Sender has no emails — cannot summarise")

    mails: list[Mail] = []
    for db_email in db_emails:
        cache = await db.relational.get_agent_cache(db_email.id)
        base_mail = _build_mail_from_db(db_email, sender)
        body_override = cache.analysis if cache else (db_email.body or None)
        mails.append(base_mail.model_copy(update={"body_plain": body_override}))

    try:
        summary, was_cached = llm.analyze_sender(
            sender_id=str(sender_id),
            messages=mails,
            force_regeneration=True,
        )
    except LLMAnalysisError as exc:
        logger.error("Sender analysis failed for %s: %s", sender_id, exc)
        raise HTTPException(status_code=502, detail=f"LLM analysis failed: {exc}")

    await db.relational.update_sender_summary(sender_id, summary)
    return SenderSummaryResponse(sender_id=sender_id, summary=summary, cached=was_cached)


@router.get(
    "/threads",
    response_model=list[ThreadWithSenderResponse],
    summary="List threads from the database sorted by most recent activity",
)
async def list_threads(
    top_k: Annotated[int, Query(ge=1, le=500)] = 50,
    sender_id: Annotated[UUID | None, Query(description="Filter by sender UUID")] = None,
    sync_counts: Annotated[bool, Query(description="Sync mail counts from Gmail (slow — avoid on every load)")] = False,
    postbox: GmailPostbox = Depends(get_postbox),
    db=Depends(get_db_manager),
):
    """
    Return threads stored in the RDB sorted by most recent email activity (newest first).

    Threads are populated automatically when emails are ingested via GET /emails.
    Each result includes the thread and its associated sender record.

    Use sender_id to retrieve threads for a specific sender (used by the inbox sidebar).
    Pass sync_counts=true to refresh mail counts from Gmail (adds one API call per thread).
    """
    threads = await db.relational.list_threads(limit=top_k, sender_id=sender_id)

    # Batch-fetch the latest email for each thread in a single query
    thread_ids = [t.id for t in threads if t.id]
    thread_latest = await db.relational.get_latest_email_per_thread(thread_ids)

    # Backfill gmail_thread_id for legacy threads that are missing it
    updated_threads = []
    for thread in threads:
        latest_email = thread_latest.get(thread.id)
        if not thread.gmail_thread_id and latest_email:
            try:
                mail_meta = postbox.get_email_metadata(latest_email.message_id)
                if mail_meta.thread_id:
                    canonical = await db.relational.get_thread_by_gmail_id(mail_meta.thread_id)
                    if canonical and canonical.id != thread.id:
                        await db.relational.merge_thread_into(thread.id, canonical.id)
                        thread_latest[canonical.id] = thread_latest.get(canonical.id) or latest_email
                        thread = None  # absorbed into canonical; skip
                    else:
                        thread = thread.model_copy(update={"gmail_thread_id": mail_meta.thread_id})
                        await db.relational.upsert_thread(thread)
            except Exception as exc:
                logger.warning("Could not backfill gmail_thread_id for thread %s: %s", thread.id, exc)
        if thread is not None:
            updated_threads.append(thread)
    threads = updated_threads

    # Optionally sync mail counts from Gmail (opt-in only — one API call per thread)
    thread_id_map = {t.gmail_thread_id: t.id for t in threads if t.gmail_thread_id and t.id}
    synced_counts = (
        await _sync_thread_counts(thread_id_map, postbox, db)
        if thread_id_map and sync_counts
        else {}
    )

    # Batch-fetch all senders in a single query
    sender_ids = [t.sender_id for t in threads if t.sender_id]
    sender_map = await db.relational.get_senders_by_ids(sender_ids)

    # Batch-fetch agent_cache for all latest emails (to surface per-email importance)
    latest_email_ids = [e.id for e in thread_latest.values()]
    analysis_map = await db.relational.get_agent_caches_by_email_ids(latest_email_ids)

    results = []
    for thread in threads:
        if thread.gmail_thread_id and thread.gmail_thread_id in synced_counts:
            thread = thread.model_copy(update={"mail_count": synced_counts[thread.gmail_thread_id]})
        sender = sender_map.get(thread.sender_id)
        latest_email = thread_latest.get(thread.id)
        cache = analysis_map.get(latest_email.id) if latest_email else None
        results.append(ThreadWithSenderResponse(
            thread=thread,
            sender=sender,
            latest_email_id=latest_email.id if latest_email else None,
            latest_email_importance=cache.importance_score if cache else None,
        ))
    return results


@router.post(
    "/threads/{thread_id}/summary",
    response_model=ThreadSummaryResponse,
    summary="Generate or refresh the LLM summary for a thread",
)
async def generate_thread_summary(
    thread_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
    config: AppConfig = Depends(get_config),
):
    """
    Run the LLM thread analysis using RAG: email chunks are retrieved from
    ChromaDB in chronological order so the full conversation history is taken
    into account when generating the summary. Falls back to relational DB
    emails if no chunks exist in the vector store yet (e.g. embedding has not
    run for this thread). Always force-regenerates — called explicitly by the
    user via a Generate/Refresh button.
    """
    thread = await db.relational.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    # RAG path: retrieve chronologically ordered chunks from ChromaDB so that
    # the full conversation history is available to the LLM.
    chunks = await db.vector.get_thread_chunks(thread_id)
    if chunks:
        messages = chunks
        logger.debug("Thread %s: using %d RAG chunks from ChromaDB", thread_id, len(chunks))
    else:
        # Fallback: chunks not yet embedded — use relational DB emails instead.
        logger.debug("Thread %s: no chunks in ChromaDB, falling back to relational emails", thread_id)
        messages = await _build_mails_for_thread(thread_id, db)

    if not messages:
        raise HTTPException(status_code=422, detail="Thread has no emails — cannot summarise")

    rag_context = await _build_thread_rag_context(
        thread_id, messages, db, top_k=config.rag.top_k, min_score=config.rag.min_score
    )
    logger.debug("Thread %s: RAG context %s", thread_id, "found" if rag_context else "empty")

    try:
        result = llm.analyze_thread(
            thread_id=str(thread_id),
            messages=messages,
            force_regeneration=True,
            rag_context=rag_context,
        )
    except LLMAnalysisError as exc:
        logger.error("Thread analysis failed for %s: %s", thread_id, exc)
        raise HTTPException(status_code=502, detail=f"LLM analysis failed: {exc}")

    await db.relational.update_thread_summary(thread_id, result.summary)
    return ThreadSummaryResponse(
        thread_id=thread_id,
        summary=result.summary,
        cached=result.cached,
        message_count=result.message_count,
        actions=[a.model_dump() for a in result.actions],
    )


@router.put(
    "/threads/{thread_id}/summary",
    response_model=ThreadSummaryResponse,
    summary="Manually set the summary for a thread",
)
async def set_thread_summary(
    thread_id: UUID,
    body: ThreadSummaryUpdateRequest,
    db=Depends(get_db_manager),
):
    """Persist a user-edited summary for a thread without re-running LLM analysis."""
    thread = await db.relational.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    await db.relational.update_thread_summary(thread_id, body.summary)
    return ThreadSummaryResponse(
        thread_id=thread_id,
        summary=body.summary,
        cached=False,
        message_count=thread.mail_count,
    )


@router.get(
    "/threads/{thread_id}/analyse/rag",
    response_model=ThreadLLMResponse,
    summary="Analyse a thread using RAG context from similar emails",
)
async def analyse_thread_rag(
    thread_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
    config: AppConfig = Depends(get_config),
):
    """
    Run LLM thread analysis with RAG: retrieves similar email chunks from other
    threads and injects them as context. Result is cached under
    "thread-rag:{thread_id}" so the compare endpoint can fetch it without re-running.
    """
    thread = await db.relational.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    chunks = await db.vector.get_thread_chunks(thread_id)
    messages = chunks if chunks else await _build_mails_for_thread(thread_id, db)
    if not messages:
        raise HTTPException(status_code=422, detail="Thread has no emails — cannot analyse")
    rag_context = await _build_thread_rag_context(
        thread_id, messages, db, top_k=config.rag.top_k, min_score=config.rag.min_score
    )
    return llm.analyze_thread(
        thread_id=str(thread_id),
        messages=messages,
        force_regeneration=True,
        rag_context=rag_context,
        cache_key=f"thread-rag:{thread_id}",
    )


@router.get(
    "/threads/{thread_id}/analyse/no-rag",
    response_model=ThreadLLMResponse,
    summary="Analyse a thread without RAG context",
)
async def analyse_thread_no_rag(
    thread_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
):
    """
    Run LLM thread analysis without any RAG context — thread content only.
    Result is cached under "thread-no-rag:{thread_id}" so the compare endpoint
    can fetch it without re-running.
    """
    thread = await db.relational.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    chunks = await db.vector.get_thread_chunks(thread_id)
    messages = chunks if chunks else await _build_mails_for_thread(thread_id, db)
    if not messages:
        raise HTTPException(status_code=422, detail="Thread has no emails — cannot analyse")
    return llm.analyze_thread(
        thread_id=str(thread_id),
        messages=messages,
        force_regeneration=True,
        cache_key=f"thread-no-rag:{thread_id}",
    )


@router.get(
    "/threads/{thread_id}/analyse/compare",
    response_model=ThreadRagCompareResponse,
    summary="Compare RAG vs no-RAG thread analyses side by side",
)
async def compare_thread_analysis(
    thread_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
):
    """
    Return both cached thread analyses (RAG and no-RAG) side by side.
    No LLM call is made — call /threads/{thread_id}/analyse/rag and
    /threads/{thread_id}/analyse/no-rag first, then use this endpoint to compare.
    Fields are null if the corresponding analysis has not been run yet.
    """
    thread = await db.relational.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    thread_id_str = str(thread_id)
    return ThreadRagCompareResponse(
        thread_id=thread_id,
        rag=llm.get_cached_thread_analysis(f"thread-rag:{thread_id_str}", thread_id_str),
        no_rag=llm.get_cached_thread_analysis(f"thread-no-rag:{thread_id_str}", thread_id_str),
    )


@router.get(
    "/threads/{thread_id}/analyse/rag-inspect",
    response_model=ThreadRagInspectResponse,
    summary="[Test] Inspect RAG retrieval for a thread without running the LLM",
)
async def thread_rag_inspect(
    thread_id: UUID,
    db=Depends(get_db_manager),
    config: AppConfig = Depends(get_config),
):
    """
    Debug endpoint: shows exactly what the RAG retrieval step finds for a thread
    before the LLM is called. No LLM call is made.

    Returns:
    - The query string sent to the vector store
    - Every chunk retrieved from other threads, with score, source email, and text
    - Whether each source email has a cached analysis
    - The final formatted context string that would be injected into the prompt
    """
    thread = await db.relational.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    chunks = await db.vector.get_thread_chunks(thread_id)
    messages = chunks if chunks else await _build_mails_for_thread(thread_id, db)
    if not messages:
        raise HTTPException(status_code=422, detail="Thread has no emails — cannot inspect")

    if isinstance(messages[0], ChromaDocumentItem):
        query = "\n\n".join(m.document_snippet for m in messages[:3])[:800]
    else:
        parts = [f"{m.subject}\n{(m.body_plain or '')[:200]}" for m in messages[:3]]
        query = "\n\n".join(parts)[:800]

    top_k = config.rag.top_k
    min_score = config.rag.min_score

    similar = await db.vector.search_emails(
        query=query,
        top_k=top_k,
        filters={"thread_id": {"$ne": str(thread_id)}},
    )

    chunk_debugs: list[RagChunkDebug] = []
    context_parts: list[str] = []
    seen: set[str] = set()

    for result in similar:
        meta = result.metadata
        source_email_id = meta.get("email_id", "")
        cached = await db.relational.get_agent_cache(UUID(source_email_id)) if source_email_id else None
        passed = result.score >= min_score

        chunk_debugs.append(RagChunkDebug(
            chunk_id=f"{source_email_id}__chunk_{meta.get('chunk_index', '?')}",
            email_id=source_email_id,
            score=round(result.score, 4),
            chunk_index=int(meta.get("chunk_index", 0)),
            received_at=meta.get("received_at", ""),
            document=result.document,
            passed_threshold=passed,
            has_cached_analysis=cached is not None,
            cached_analysis=cached.analysis if cached else None,
        ))

        if passed and source_email_id not in seen:
            seen.add(source_email_id)
            part = f"[Score: {result.score:.2f}]\n{result.document}"
            if cached:
                part += f"\nAnalysis: {cached.analysis}"
            context_parts.append(part)

    formatted_context = "\n\n---\n\n".join(context_parts) if context_parts else None
    chunks_passed = sum(1 for c in chunk_debugs if c.passed_threshold)

    return ThreadRagInspectResponse(
        thread_id=thread_id,
        query=query,
        top_k_requested=top_k,
        min_score_threshold=min_score,
        chunks_retrieved=len(similar),
        chunks_passed=chunks_passed,
        chunks_dropped=len(similar) - chunks_passed,
        unique_source_threads=len(seen),
        chunks=chunk_debugs,
        formatted_context=formatted_context,
    )


@router.put(
    "/{email_id}/metadata",
    response_model=MetadataUpdateResponse,
    summary="Save edited tags for an email",
)
async def update_email_metadata(
    email_id: UUID,
    body: MetadataUpdateRequest,
    db=Depends(get_db_manager),
):
    """Persist user-edited tags and optional importance score to agent_cache."""
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")
    await db.relational.update_suggested_tags(email_id, body.tags)
    if body.importance is not None:
        await db.relational.update_importance_score(email_id, body.importance)
    return MetadataUpdateResponse(email_id=email_id, tags=body.tags, importance=body.importance)


@router.post(
    "/{email_id}/suggested-reply",
    response_model=SuggestedReplyResponse,
    summary="Generate an AI reply suggestion for an email",
)
async def generate_suggested_reply(
    email_id: UUID,
    llm: LLMService = Depends(get_llm_service),
    db=Depends(get_db_manager),
):
    """
    Call the LLM to draft a professional reply for the given email and persist
    it to agent_cache.suggested_reply. Always regenerates — no cache check.
    """
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")

    sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
    mail = _build_mail_from_db(db_email, sender)

    cache = await db.relational.get_agent_cache(email_id)
    summary = cache.analysis if cache else None

    try:
        reply = llm.generate_reply(mail, summary=summary)
    except LLMAnalysisError as exc:
        logger.error("Reply generation failed for %s: %s", email_id, exc)
        raise HTTPException(status_code=502, detail=f"LLM reply generation failed: {exc}")

    await db.relational.update_suggested_reply(email_id, reply)
    return SuggestedReplyResponse(email_id=email_id, suggested_reply=reply)


@router.put(
    "/{email_id}/suggested-reply",
    response_model=SuggestedReplyResponse,
    summary="Save a manually edited reply suggestion",
)
async def update_suggested_reply(
    email_id: UUID,
    body: ReplyUpdateRequest,
    db=Depends(get_db_manager),
):
    """Persist a user-edited reply to agent_cache.suggested_reply without calling the LLM."""
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found")
    await db.relational.update_suggested_reply(email_id, body.reply)
    return SuggestedReplyResponse(email_id=email_id, suggested_reply=body.reply)


@router.get(
    "/threads/{thread_id}/emails",
    response_model=list[EmailInThreadItem],
    summary="List all emails in a thread, newest first",
)
async def list_emails_in_thread(
    thread_id: UUID,
    db=Depends(get_db_manager),
):
    """
    Return all emails belonging to the given thread, sorted newest-first.
    Used by the frontend history timeline to show prior context for the open email.
    """
    emails = await db.relational.list_emails(limit=100, thread_id=thread_id)
    result = []
    for email in emails:
        sender = await db.relational.get_sender(email.sender_id) if email.sender_id else None
        result.append(EmailInThreadItem(
            id=email.id,
            subject=email.subject or "",
            sender_name=sender.display_name if sender else None,
            sender_email=sender.email if sender else None,
            received_at=email.received_at,
            body_preview=(email.body or "")[:120],
        ))
    return result


@router.post(
    "/send",
    response_model=SendEmailResponse,
    summary="Send an email via the authenticated Gmail account",
)
def send_email(
    payload: SendEmailRequest,
    postbox: GmailPostbox = Depends(get_postbox),
):
    """Send a plain-text email from the authenticated Gmail account."""
    try:
        message_id = postbox.send_email(
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
            thread_id=payload.thread_id,
            in_reply_to=payload.in_reply_to,
        )
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SendEmailResponse(message_id=message_id, to=payload.to, subject=payload.subject)


@router.get(
    "/{email_id}",
    response_model=EmailWithAnalysisResponse,
    summary="Get a single email from the database with its cached analysis",
)
async def get_email(
    email_id: UUID,
    db=Depends(get_db_manager),
):
    """
    Return a single email by its RDB UUID together with its cached LLM analysis
    (if one exists).  Raises 404 if the email is not found in the database.

    Must be declared last so that literal routes (/raw, /analyse/{id}) take
    priority during FastAPI's in-order route matching.
    """
    db_email = await db.relational.get_email(email_id)
    if db_email is None:
        raise HTTPException(status_code=404, detail=f"Email {email_id} not found in database")

    sender = await db.relational.get_sender(db_email.sender_id) if db_email.sender_id else None
    cache_entry = await db.relational.get_agent_cache(email_id)
    return EmailWithAnalysisResponse(email=db_email, sender=sender, analysis=cache_entry)
