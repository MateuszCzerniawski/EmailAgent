"""
Chat router — POST /chat/message

Accepts a user question + conversation history, searches email chunks in ChromaDB,
enriches results with subject/sender from the relational DB, and returns a
grounded LLM reply with citation metadata. Email IDs are never returned.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.dependencies import get_db_manager, get_llm_service
from app.services.llm_service import LLMService

router = APIRouter()

_MIN_SCORE = 0.15
_MAX_HISTORY_MSGS = 20  # last 20 messages (10 user+assistant pairs)


class HistoryMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryMessage] = Field(default_factory=list)
    top_k: int = Field(default=10, ge=1, le=20)


class ChatCitation(BaseModel):
    email_id: str
    subject: str
    sender: str
    received_at: str
    snippet: str
    score: float
    source_type: str = "email"
    task_deadline: str | None = None


class ChatResponse(BaseModel):
    reply: str
    citations: list[ChatCitation]
    clarifying_questions: list[str] = []


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    body: ChatRequest,
    db=Depends(get_db_manager),
    llm: LLMService = Depends(get_llm_service),
):
    # 1. Parallel semantic search — email chunks AND task chunks
    results, task_results = await asyncio.gather(
        db.vector.search_emails(body.message, top_k=body.top_k),
        db.search_tasks(body.message, top_k=5),
    )

    # 2. Deduplicate by email_id (keep highest score per email), filter by min score
    best: dict[str, object] = {}
    for r in results:
        eid = r.metadata.get("email_id", "")
        if not eid:
            continue
        if eid not in best or r.score > best[eid].score:
            best[eid] = r
    filtered = sorted(
        [r for r in best.values() if r.score >= _MIN_SCORE],
        key=lambda r: r.score,
        reverse=True,
    )

    # 3. Enrich with subject + sender name from relational DB
    filtered_eids = [UUID(r.metadata["email_id"]) for r in filtered]
    emails_by_id: dict[str, object] = {}
    for eid in filtered_eids:
        try:
            email = await db.relational.get_email(eid)
            if email:
                emails_by_id[str(eid)] = email
        except Exception:
            pass

    sender_ids = list({e.sender_id for e in emails_by_id.values() if e.sender_id})
    senders_by_id = await db.relational.get_senders_by_ids(sender_ids) if sender_ids else {}

    # 4. Build RAG context string + citation list
    rag_lines: list[str] = []
    citations: list[ChatCitation] = []
    for r in filtered:
        eid = r.metadata.get("email_id", "")
        email = emails_by_id.get(eid)
        if email:
            sender_obj = senders_by_id.get(email.sender_id)
            if sender_obj:
                sender_str = sender_obj.display_name or sender_obj.email
            else:
                sender_str = "Unknown"
            # Prefer metadata subject (faster); fall back to relational DB value
            subject = r.metadata.get("subject") or email.subject or "(no subject)"
            received = email.received_at.strftime("%Y-%m-%d") if email.received_at else ""
        else:
            sender_str = "Unknown"
            subject = r.metadata.get("subject") or "(unknown subject)"
            received = r.metadata.get("received_at", "")[:10]

        rag_lines.append(
            f"From: {sender_str} | Subject: {subject} | Date: {received}\n{r.document}"
        )
        citations.append(ChatCitation(
            email_id=eid,
            subject=subject,
            sender=sender_str,
            received_at=received,
            snippet=r.document[:300],
            score=round(r.score, 3),
        ))

    # 4b. Build task citations
    task_filtered = [r for r in task_results if r.score >= _MIN_SCORE]
    task_filtered.sort(key=lambda r: r.score, reverse=True)

    for r in task_filtered:
        meta = r.metadata
        task_text = meta.get("task_text", r.document)
        deadline = meta.get("deadline") or None
        subject = meta.get("subject") or "(unknown subject)"
        eid = meta.get("email_id", "")
        received = meta.get("received_at", "")[:10]

        sender_str = "Unknown"
        try:
            if eid:
                email = emails_by_id.get(eid) or await db.relational.get_email(UUID(eid))
                if email:
                    sender_obj = senders_by_id.get(email.sender_id)
                    if sender_obj:
                        sender_str = sender_obj.display_name or sender_obj.email
        except Exception:
            pass

        rag_lines.append(
            f"[Task] {task_text}"
            + (f" (deadline: {deadline})" if deadline else "")
            + f"\nFrom email: {subject}"
        )
        citations.append(ChatCitation(
            email_id=eid,
            subject=subject,
            sender=sender_str,
            received_at=received,
            snippet=task_text,
            score=round(r.score, 3),
            source_type="task",
            task_deadline=deadline,
        ))

    rag_context = "\n\n---\n\n".join(rag_lines) if rag_lines else "(no relevant emails found)"

    # 5. Format history string (capped to avoid token overflow)
    history = body.history[-_MAX_HISTORY_MSGS:]
    history_str = ""
    if history:
        history_str = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in history
        ) + "\n\n"

    # 6. Call LLM and return
    reply, clarifying_questions = llm.chat(body.message, rag_context, history_str)
    return ChatResponse(reply=reply, citations=citations, clarifying_questions=clarifying_questions)
