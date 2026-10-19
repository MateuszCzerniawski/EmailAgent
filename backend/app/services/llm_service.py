"""
LLM Service — LangChain-based email analysis with email-ID cache.

Architecture
------------
A single LangChain chain handles all analysis in one LLM call per email:
  summary + category + sentiment + action detection → LLMResponse

Prompts are loaded from config/agent_config.yaml at construction time, keeping
them out of the source code and editable without a code redeploy.

Cache
-----
Responses are cached in a local SQLite database keyed by email_id (not by
prompt hash). This means:
  - The same email always returns the cached result regardless of prompt changes.
  - Cache hits are exact and reliable (no heuristic timing needed).
  - `LLMResponse.cached` is always accurate.

The EmailCache class manages the SQLite table directly using stdlib sqlite3.
LangChain's own SQLiteCache is NOT used.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import keyring
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.config import AppConfig, load_agent_config
from app.logger import get_logger
from app.models.llm_response import LLMResponse, ThreadLLMResponse
from app.models.mail import Mail
from database.models.domain import ChromaDocumentItem

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Email-ID-based SQLite cache
# ---------------------------------------------------------------------------


class EmailCache:
    """
    SQLite cache keyed by email_id.

    One row per email. Re-analysing the same email (same id) always returns
    the stored result without calling the LLM API again.

    Thread safety: sqlite3 connections are not thread-safe across threads, so
    each public method opens and closes its own connection — safe for
    FastAPI's thread-pool executor that runs sync route handlers.
    """

    _TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS email_cache (
            email_id    TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            cached_at   TEXT NOT NULL
        )
    """

    def __init__(self, db_path: str, enabled: bool = True) -> None:
        self._enabled = enabled
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(self._TABLE_DDL)

    def get(self, email_id: str) -> LLMResponse | None:
        """Return the cached LLMResponse for *email_id*, or None on miss."""
        if not self._enabled:
            return None
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT response_json FROM email_cache WHERE email_id = ?",
                (email_id,),
            ).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = json.loads(row[0])
        # cached=True is injected here; it was excluded when storing.
        return LLMResponse(email_id=email_id, cached=True, **data)

    def set(self, email_id: str, response: LLMResponse) -> None:
        """Persist *response* for *email_id*, replacing any existing entry."""
        if not self._enabled:
            return
        # Exclude fields that are runtime-only and should not affect cache key.
        payload = response.model_dump(mode = 'json', exclude={"cached", "analyzed_at", "email_id"})
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO email_cache "
                "(email_id, response_json, cached_at) VALUES (?, ?, ?)",
                (
                    email_id,
                    json.dumps(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        logger.debug("Cached LLM response for email %s", email_id)

    def get_thread(self, thread_id: str) -> ThreadLLMResponse | None:
        """Return the cached ThreadLLMResponse for *thread_id*, or None on miss."""
        if not self._enabled:
            return None
        cache_key = f"thread:{thread_id}"
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT response_json FROM email_cache WHERE email_id = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = json.loads(row[0])
        return ThreadLLMResponse(thread_id=thread_id, cached=True, **data)

    def set_thread(self, thread_id: str, response: ThreadLLMResponse) -> None:
        """Persist *response* for *thread_id*, replacing any existing entry."""
        if not self._enabled:
            return
        cache_key = f"thread:{thread_id}"
        payload = response.model_dump(exclude={"cached", "analyzed_at", "thread_id"})
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO email_cache "
                "(email_id, response_json, cached_at) VALUES (?, ?, ?)",
                (
                    cache_key,
                    json.dumps(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        logger.debug("Cached LLM thread response for thread %s", thread_id)

    def get_thread_by_key(self, cache_key: str, thread_id: str) -> ThreadLLMResponse | None:
        """Return cached ThreadLLMResponse by an arbitrary raw cache key."""
        if not self._enabled:
            return None
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT response_json FROM email_cache WHERE email_id = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = json.loads(row[0])
        return ThreadLLMResponse(thread_id=thread_id, cached=True, **data)

    def set_thread_by_key(self, cache_key: str, response: ThreadLLMResponse) -> None:
        """Persist ThreadLLMResponse under an arbitrary raw cache key."""
        if not self._enabled:
            return
        payload = response.model_dump(exclude={"cached", "analyzed_at", "thread_id"})
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO email_cache "
                "(email_id, response_json, cached_at) VALUES (?, ?, ?)",
                (
                    cache_key,
                    json.dumps(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        logger.debug("Cached thread response under key %s", cache_key)

    def get_sender(self, sender_id: str) -> str | None:
        """Return cached sender summary string for *sender_id*, or None on miss."""
        if not self._enabled:
            return None
        cache_key = f"sender:{sender_id}"
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT response_json FROM email_cache WHERE email_id = ?",
                (cache_key,),
            ).fetchone()
        return row[0] if row else None

    def set_sender(self, sender_id: str, summary: str) -> None:
        """Persist plain-text *summary* for *sender_id*, replacing any existing entry."""
        if not self._enabled:
            return
        cache_key = f"sender:{sender_id}"
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO email_cache "
                "(email_id, response_json, cached_at) VALUES (?, ?, ?)",
                (
                    cache_key,
                    summary,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        logger.debug("Cached sender summary for sender %s", sender_id)


# ---------------------------------------------------------------------------
# LLM Service
# ---------------------------------------------------------------------------


class LLMService:
    """
    Wraps a LangChain chain to analyse emails with a single LLM call.

    The chain is built once at construction time.  The EmailCache is checked
    before every chain.invoke() — cache hits never reach the OpenAI API.
    """

    _CLARIFY_RE = re.compile(r'CLARIFY:\s*\n((?:\s*\d+\.\s*.+\n?)+)', re.IGNORECASE)

    def __init__(self, config: AppConfig) -> None:
        """
        Initialise all LangChain chains and the in-process EmailCache.

        Prompt templates are loaded from config/agent_config.yaml so they can
        be edited without touching Python source. A new LLM client is built
        from the current AppConfig each time the service is constructed — since
        LLMService is instantiated per-request via Depends(get_llm_service),
        any runtime provider change (e.g. OpenAI → Ollama) takes effect on the
        very next request.
        """
        self._config = config
        self._cache = EmailCache(
            db_path=config.cache.db_path,
            enabled=config.cache.enabled,
        )
        # Load prompts from agent_config.yaml — not hardcoded.
        agent_cfg = load_agent_config()
        self._system_prompt: str = agent_cfg["email_analysis"]["system_prompt"]
        self._human_prompt: str = agent_cfg["email_analysis"]["human_prompt"]
        self._thread_system_prompt: str = agent_cfg["thread_analysis"]["system_prompt"]
        self._thread_human_prompt: str = agent_cfg["thread_analysis"]["human_prompt"]
        self._sender_system_prompt: str = agent_cfg["sender_profile_analysis"]["system_prompt"]
        self._sender_human_prompt: str = agent_cfg["sender_profile_analysis"]["human_prompt"]
        self._reply_system_prompt: str = agent_cfg["reply_generation"]["system_prompt"]
        self._reply_human_prompt: str = agent_cfg["reply_generation"]["human_prompt"]

        self._llm = self._build_llm()
        self._chain = self._build_chain()
        self._thread_chain = self._build_thread_chain()
        self._sender_chain = self._build_sender_chain()
        self._reply_chain = self._build_reply_chain()
        self._chat_chain = self._build_chat_chain()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def analyze(
        self,
        mail: Mail,
        rag_context: str | None = None,
        guidelines: str | None = None,
        _cache_key: str | None = None,
        bypass_cache: bool = False,
    ) -> LLMResponse:
        """
        Return an LLMResponse for *mail*, using the cache when available.

        Flow:
            1. Check EmailCache by cache key → return cached result if found.
            2. Call LangChain chain → OpenAI API.
            3. Store result in EmailCache.
            4. Return LLMResponse (cached=False).

        Args:
            mail: The Mail object to analyse.  Uses body_plain when available,
                  falls back to snippet (from fetch_emails headers-only call).
            rag_context: Optional pre-formatted string of similar emails retrieved
                  from the vector store, injected into the prompt for context.
            _cache_key: Override the EmailCache lookup key. Defaults to mail.id.
                  Use prefixed keys (e.g. "rag:<id>", "no-rag:<id>") to store
                  RAG and non-RAG results separately for comparison.

        Raises:
            LLMAnalysisError: If the LLM returns malformed JSON or an
                unexpected schema.
        """
        key = _cache_key or mail.id

        # 1. Cache hit (skipped when bypass_cache=True, e.g. force_regeneration)
        if not bypass_cache:
            cached_response = self._cache.get(key)
            if cached_response is not None:
                logger.debug("Cache hit for key %s", key)
                if _cache_key:
                    return cached_response.model_copy(update={"email_id": mail.id})
                return cached_response

        # 2. Cache miss — call LLM
        logger.debug("Cache miss for email %s — calling LLM", mail.id)
        body = (mail.body_plain or mail.snippet or "(no content)").strip()
        body_truncated = body[:4000]  # hard cap to avoid token overflow

        prompt_vars = {
            "categories": ", ".join(self._config.email_categories),
            "guidelines": guidelines or "(no guidelines defined)",
            "subject": mail.subject,
            "sender": mail.sender,
            "date": mail.date.isoformat(),
            "body": body_truncated,
            "rag_context": rag_context or "(no similar emails found)",
        }
        raw_text: str = self._chain.invoke(prompt_vars)

        response = self._parse_response(raw_text, mail.id)

        # 3. Store in cache
        self._cache.set(key, response)

        return response  # cached=False (default in LLMResponse)

    def get_cached_analysis(self, cache_key: str) -> LLMResponse | None:
        """Return a previously stored LLMResponse by its exact cache key, or None."""
        return self._cache.get(cache_key)

    def analyze_thread(
        self,
        thread_id: str,
        messages: list[Mail] | list[ChromaDocumentItem],
        force_regeneration: bool = False,
        rag_context: str | None = None,
        cache_key: str | None = None,
    ) -> ThreadLLMResponse:
        """
        Analyse an entire email thread as one combined object.

        Accepts either a list of Mail objects or a list of ChromaDocumentItem
        objects (e.g. from get_thread_chunks). Both are concatenated into a
        single formatted text block and sent to the LLM in one call.
        When cache_key is provided it is used instead of the default
        "thread:{thread_id}" key — used by rag/no-rag test variants.

        Raises:
            LLMAnalysisError: If the LLM returns malformed JSON.
        """
        # 1. Cache hit (skip when force_regeneration=True)
        if not force_regeneration:
            if cache_key:
                cached = self._cache.get_thread_by_key(cache_key, thread_id)
            else:
                cached = self._cache.get_thread(thread_id)
            if cached is not None:
                logger.debug("Cache hit for thread key %s", cache_key or f"thread:{thread_id}")
                return cached

        # 2. Build combined thread text
        parts: list[str] = []
        if messages and isinstance(messages[0], ChromaDocumentItem):
            for i, chunk in enumerate(messages, start=1):
                received_at = chunk.metadata.get("received_at", "unknown date")
                chunk_index = chunk.metadata.get("chunk_index", i - 1)
                parts.append(
                    f"[Chunk {chunk_index} | {received_at}]\n\n"
                    f"{chunk.document_snippet}"
                )
        else:
            for i, mail in enumerate(messages, start=1):
                body = (mail.body_plain or mail.snippet or "(no content)").strip()
                parts.append(
                    f"[Message {i} of {len(messages)}]\n"
                    f"From: {mail.sender}\n"
                    f"Date: {mail.date.isoformat()}\n"
                    f"Subject: {mail.subject}\n\n"
                    f"{body[:2000]}"
                )
        thread_text = "\n\n---\n\n".join(parts)

        # 3. Call LLM
        logger.debug("Cache miss for thread %s — calling LLM", thread_id)
        thread_vars = {
            "categories": ", ".join(self._config.email_categories),
            "thread": thread_text,
            "rag_context": rag_context or "None",
        }
        raw_text: str = self._thread_chain.invoke(thread_vars)

        data = self._parse_raw_json(raw_text, ref_id=thread_id)
        response = ThreadLLMResponse(
            thread_id=thread_id,
            message_count=len(messages),
            **data,
        )

        # 4. Cache and return
        if cache_key:
            self._cache.set_thread_by_key(cache_key, response)
        else:
            self._cache.set_thread(thread_id, response)
        return response

    def get_cached_thread_analysis(self, cache_key: str, thread_id: str) -> ThreadLLMResponse | None:
        """Return a previously stored ThreadLLMResponse by its exact cache key, or None."""
        return self._cache.get_thread_by_key(cache_key, thread_id)

    def analyze_sender(
        self,
        sender_id: str,
        messages: list[Mail],
        force_regeneration: bool = False,
    ) -> tuple[str, bool]:
        """
        Produce a 2-4 sentence plain-text profile for a sender's recent emails.

        Uses the sender_profile_analysis chain which returns plain text directly.
        Cached using the key "sender:{sender_id}".

        Returns:
            (summary_str, was_cached) tuple.

        Raises:
            LLMAnalysisError: If the LLM call or JSON parse fails.
        """
        if not force_regeneration:
            cached = self._cache.get_sender(sender_id)
            if cached is not None:
                logger.debug("Cache hit for sender %s", sender_id)
                return cached, True

        parts: list[str] = []
        for i, mail in enumerate(messages, start=1):
            body = (mail.body_plain or mail.snippet or "(no content)").strip()
            parts.append(
                f"[Email {i} of {len(messages)}]\n"
                f"From: {mail.sender}\n"
                f"Date: {mail.date.isoformat()}\n"
                f"Subject: {mail.subject}\n\n"
                f"{body[:2000]}"
            )
        combined = "\n\n---\n\n".join(parts)

        logger.debug("Cache miss for sender %s — calling LLM", sender_id)
        sender_vars = {"emails": combined}
        summary: str = self._sender_chain.invoke(sender_vars).strip()

        self._cache.set_sender(sender_id, summary)
        return summary, False

    def generate_reply(self, mail: Mail, summary: str | None = None) -> str:
        """
        Generate a professional reply draft for *mail* using the reply chain.

        Always calls the LLM — no caching — because the user explicitly requested
        a fresh suggestion. The caller is responsible for persisting the result.

        Args:
            mail: The email to reply to.
            summary: Optional pre-computed summary used as extra context.

        Returns:
            Plain-text reply string ready to display in the editor.

        Raises:
            LLMAnalysisError: If the LLM call fails.
        """
        body = (mail.body_plain or mail.snippet or "(no content)").strip()
        try:
            reply_vars = {
                "subject": mail.subject,
                "sender": mail.sender,
                "date": mail.date.isoformat(),
                "body": body[:4000],
                "summary": summary or "(no summary available)",
            }
            return self._reply_chain.invoke(reply_vars).strip()
        except Exception as exc:
            raise LLMAnalysisError(f"Reply generation failed for email {mail.id}: {exc}") from exc

    def chat(self, question: str, rag_context: str, history: str = "") -> tuple[str, list[str]]:
        """Answer a free-form question using email RAG context. No caching.

        Returns (reply, clarifying_questions). clarifying_questions is non-empty
        only when the model cannot answer and outputs a CLARIFY: block.
        """
        raw = self._chat_chain.invoke({
            "question": question,
            "rag_context": rag_context or "(no relevant emails found)",
            "history": history or "",
        }).strip()
        return self._extract_clarifying_questions(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_llm(self) -> ChatOpenAI:
        """Construct ChatOpenAI (or Ollama via its OpenAI-compatible endpoint)."""
        llm_cfg = self._config.llm
        if llm_cfg.provider == "ollama":
            return ChatOpenAI(
                model=llm_cfg.ollama_model,
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
                base_url=f"{llm_cfg.ollama_base_url}/v1",
                api_key="ollama",
            )
        kr = self._config.keyring
        api_key = keyring.get_password(kr.service_name, kr.openai_key_name)
        if not api_key:
            raise RuntimeError(
                "OpenAI API key not found in keyring.\n"
                "Run:  python scripts/setup_secrets.py"
            )
        return ChatOpenAI(
            model=llm_cfg.openai_model,
            temperature=llm_cfg.temperature,
            max_tokens=llm_cfg.max_tokens,
            api_key=api_key,
        )

    def _build_chain(self):
        """Build the prompt | llm | str_parser chain using prompts from YAML."""
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._system_prompt),
                ("human", self._human_prompt),
            ]
        )
        return prompt | self._llm | StrOutputParser()

    def _build_thread_chain(self):
        """Build the thread analysis chain using thread prompts from YAML."""
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._thread_system_prompt),
                ("human", self._thread_human_prompt),
            ]
        )
        return prompt | self._llm | StrOutputParser()

    def _build_sender_chain(self):
        """Build the sender profile chain using sender_profile_analysis prompts from YAML."""
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._sender_system_prompt),
                ("human", self._sender_human_prompt),
            ]
        )
        return prompt | self._llm | StrOutputParser()

    def _build_reply_chain(self):
        """Build the reply generation chain using reply prompts from YAML."""
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._reply_system_prompt),
                ("human", self._reply_human_prompt),
            ]
        )
        return prompt | self._llm | StrOutputParser()

    def _build_chat_chain(self):
        """Build the free-form chat chain using chat_qa prompts from YAML."""
        agent_cfg = load_agent_config()
        chat_cfg = agent_cfg.get("chat_qa", {})
        system = chat_cfg.get("system_prompt", "You are a helpful assistant.")
        human = chat_cfg.get("human_prompt", "{question}")
        prompt = ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", human),
        ])
        return prompt | self._llm | StrOutputParser()

    @staticmethod
    def _extract_clarifying_questions(raw: str) -> tuple[str, list[str]]:
        """
        Parse the raw LLM output for a CLARIFY: block.

        When the model cannot answer it outputs:
            CLARIFY:
            1. First question?
            2. Second question?

        Returns:
            (reply, questions) where reply is any text before the block (or a
            default fallback) and questions is a list of up to 5 strings.
            If no CLARIFY: block is found, returns (raw.strip(), []).
        """
        m = LLMService._CLARIFY_RE.search(raw)
        if not m:
            return raw.strip(), []
        questions = [q.strip() for q in re.findall(r'\d+\.\s*(.+)', m.group(1))][:5]
        preamble = raw[:m.start()].strip()
        reply = preamble if preamble else "I need a bit more context to answer accurately."
        return reply, questions

    @staticmethod
    def _parse_raw_json(raw_text: str, ref_id: str) -> dict[str, Any]:
        """
        Strip markdown fences and parse the LLM JSON output into a dict.

        Raises:
            LLMAnalysisError: on non-JSON or unparseable output.
        """
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = [l for l in cleaned.splitlines() if not l.startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMAnalysisError(
                f"LLM returned non-JSON output for {ref_id}: {exc}\n"
                f"Raw output: {raw_text[:500]}"
            ) from exc

    @staticmethod
    def _parse_response(raw_text: str, email_id: str) -> LLMResponse:
        """Parse the raw LLM text output into an LLMResponse."""
        data = LLMService._parse_raw_json(raw_text, ref_id=email_id)
        try:
            return LLMResponse(email_id=email_id, **data)
        except Exception as exc:
            raise LLMAnalysisError(
                f"LLM JSON does not match LLMResponse schema for email {email_id}: {exc}"
            ) from exc


class LLMAnalysisError(Exception):
    """Raised when the LLM returns an invalid or unparseable response."""
