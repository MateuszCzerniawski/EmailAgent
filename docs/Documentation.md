# email Agent — Documentation

## Overview

email Agent is a full-stack email intelligence application. It connects to Gmail via OAuth2,
ingests emails into a local database, runs AI analysis on each message, and provides a
conversational search interface backed by a vector database.

**Stack:** FastAPI + React/Vite + SQLite + ChromaDB + LangChain + OpenAI (or Ollama local).

---

## Functionalities

| Feature | Description |
|---|---|
| Gmail OAuth2 | One-time auth flow; refresh token stored in OS keyring (Windows Credential Manager / macOS Keychain). |
| Email ingestion | Emails are fetched from Gmail, stored in SQLite, and their body text is chunked and embedded into ChromaDB. |
| AI analysis | Per-email LLM analysis: summary, brief summary, category, sentiment, priority, action items, suggested reply, confidence/importance scores. Results cached in `agent_cache` table. |
| Thread analysis | LLM summarises an entire email thread in one call. Cached per `thread:<id>`. |
| Sender analysis | LLM creates a relationship profile for a sender. Cached per `sender:<id>`. |
| Suggested reply | Generated on demand; user can edit and send via Gmail API. |
| Voice-to-text | Browser microphone → WebM audio → OpenAI Whisper → transcript appended to reply draft. |
| RAG chat | Conversational interface. User query → parallel ChromaDB search (email chunks + task chunks) → LLM answer grounded in retrieved excerpts. |
| Clarifying questions | When RAG context is insufficient, the model emits a `CLARIFY:` block with up to 5 targeted questions rendered as clickable chips. |
| Task RAG | Action items extracted during analysis are indexed into `task_chunks` ChromaDB collection; surfaced alongside email sources in chat replies with deadline and amber badge. |
| Knowledge base | Guideline documents (Markdown / PDF) can be uploaded; chunked and embedded into `guideline_chunks`. |
| Tags | User-defined tags can be created and associated with analysis. |
| LLM provider switching | Toggle between OpenAI (cloud) and Ollama (local) at runtime without restarting. Stored in `config/config.yaml`. |
| Vector refresh | Re-embed all emails, guidelines, and tasks from scratch (triggered from Settings). |
| Local overrides | Per-email draft edits (suggested reply, metadata) persisted to `localStorage`; merged on load. |

---

## Layer Structure

```
backend/
├── app/
│   ├── main.py            ← FastAPI app factory + lifespan startup
│   ├── config.py          ← YAML config loader (AppConfig, LlmConfig, …)
│   ├── dependencies.py    ← FastAPI Depends() factories for all services
│   ├── logger.py          ← Async queue-based logger with colour output
│   ├── models/
│   │   ├── mail.py        ← Mail (provider-agnostic, frozen Pydantic model)
│   │   └── llm_response.py← LLMResponse, ThreadLLMResponse, ActionItem
│   ├── routers/
│   │   ├── auth.py        ← GET /auth/status|gmail|callback  (Gmail OAuth2)
│   │   ├── emails.py      ← GET /emails, /emails/analyse, /emails/{id}, …
│   │   ├── chat.py        ← POST /chat/message  (RAG + LLM)
│   │   ├── config.py      ← GET/PUT /config/llm, /config/ollama/models
│   │   ├── database.py    ← Admin/test endpoints for DB layer
│   │   └── stt.py         ← POST /stt/transcribe  (Whisper)
│   └── services/
│       └── llm_service.py ← LLMService (LangChain chains) + EmailCache
├── database/
│   ├── manager/
│   │   └── manager.py     ← DatabaseManager: orchestrates relational + vector
│   ├── relational/
│   │   ├── base.py        ← RelationalDB ABC
│   │   └── sqlite_db.py   ← SQLiteDB (SQLAlchemy async + aiosqlite)
│   ├── vector/
│   │   ├── base.py        ← VectorDB ABC
│   │   ├── chroma.py      ← ChromaDB (5 collections, run_in_executor wrapper)
│   │   ├── chunker.py     ← TokenChunker (tiktoken, 400 tok / 80 overlap)
│   │   └── embedder.py    ← Embedder (SentenceTransformer or OpenAI)
│   └── models/
│       ├── orm.py         ← SQLAlchemy 2.0 ORM models
│       └── domain.py      ← Pydantic DTOs (cross-layer transfer objects)
├── gmail/
│   └── postbox.py         ← GmailPostbox: OAuth refresh + fetch/send via Gmail API
└── config/
    ├── config.yaml        ← Runtime settings (LLM model, Gmail query, RAG params)
    └── agent_config.yaml  ← All LLM prompt templates (kept out of source code)

frontend/src/
├── api/
│   └── client.js          ← All API calls; JSDoc typedefs mirror backend models
├── router.jsx             ← React Router v6: / | /emails/:id | /settings
└── ui/
    ├── pages/
    │   ├── InboxPage.jsx       ← Paginated/searchable email list with sidebar
    │   ├── EmailDetailPage.jsx ← Full email view, analysis, edit, compose reply
    │   ├── ChatPage.jsx        ← Conversational RAG interface with citation cards
    │   ├── SettingsPage.jsx    ← LLM config, vector DB ops, guidelines/tags
    │   └── ComposePage.jsx     ← Simple compose form
    ├── components/
    │   ├── Badge.jsx           ← Pill label (6 tones)
    │   ├── Button.jsx          ← Polymorphic button (4 variants, 2 sizes)
    │   ├── Card.jsx            ← Card + CardHeader + CardSection
    │   ├── Alert.jsx           ← Inline feedback banner (4 severity variants)
    │   ├── Form.jsx            ← Input, Textarea, Select, Label, FormField
    │   ├── Skeleton.jsx        ← SkeletonLine animated placeholder
    │   ├── InfoBox.jsx         ← Compact key-value display box
    │   ├── SenderSidebar.jsx   ← Collapsible sender/thread tree panel
    │   └── SenderInfoPanel.jsx ← Sender stats and last-contact card
    └── tools/
        ├── normalizeEmail.js   ← Raw API response → stable UI model
        └── storage.js          ← localStorage wrappers (overrides + settings)
```

---

## Data Flow

### 1. Email Ingestion

```
Gmail API  →  GmailPostbox.fetch_emails()
           →  list[Mail]  (provider-agnostic, frozen)
           →  DatabaseManager.ingest_email()
                 ├── RelationalDB.upsert_email()     → SQLite: emails table
                 ├── RelationalDB.upsert_sender()    → SQLite: senders table
                 ├── RelationalDB.upsert_thread()    → SQLite: threads table
                 └── ChromaDB.upsert_email_chunks()  → email_chunks collection
                       (TokenChunker → Embedder → ChromaDB)
```

### 2. Email Analysis

```
GET /emails/analyse/{email_id}
  → RelationalDB.get_email()             → fetch Mail from SQLite
  → EmailCache.get(email_id)             → check SQLite cache (agent_cache)
  →   cache HIT  → return cached LLMResponse
  →   cache MISS → LLMService.analyze(mail)
                     → _chain.invoke()  (LangChain + OpenAI/Ollama)
                     → parse JSON → LLMResponse
                   → EmailCache.set(email_id, response)
                   → RelationalDB.upsert_agent_cache()
                   → ChromaDB.upsert_task_chunks()   (if action items present)
  → return LLMResponse + EmailModel
```

### 3. Chat (RAG)

```
POST /chat/message  { message, history, top_k }
  → asyncio.gather(
        ChromaDB.search_emails(query, top_k=10),    → email_chunks collection
        ChromaDB.search_tasks(query, top_k=5),      → task_chunks collection
    )
  → filter results by score ≥ 0.15
  → sort by score descending
  → enrich with subject/sender from RelationalDB
  → build rag_context string (numbered excerpts)
  → LLMService.chat(question, rag_context, history)
        → _chat_chain.invoke()
        → _extract_clarifying_questions(raw)
              → CLARIFY: block found  → (preamble, [q1..q5])
              → no CLARIFY:           → (full_reply, [])
  → return ChatResponse { reply, citations, clarifying_questions }
```

### 4. LLM Provider Switching

```
PUT /config/llm  { provider: "ollama" | "openai", ollama_model, ... }
  → mutate app.state.config.llm in-place
  → write updated values to config/config.yaml
  → next request → Depends(get_llm_service) → new LLMService built with new config
```

### 5. Vector Refresh

```
POST /database/sync/refresh
  → DatabaseManager.refresh()
       → RelationalDB.list_emails()             → all emails
       → for each email: ChromaDB.upsert_email_chunks()
       → RelationalDB.list_guidelines()         → all guidelines
       → for each guideline: ChromaDB.upsert_guideline_chunks()
       → RelationalDB.list_agent_caches()       → all cached analyses
       → for each cache with actions: ChromaDB.upsert_task_chunks()
```

### 6. Voice-to-Text (STT)

```
Browser microphone → MediaRecorder (WebM/Opus)
  → POST /stt/transcribe  (multipart: audio file + lang form field)
  → keyring.get_password() → OpenAI API key
  → openai.audio.transcriptions.create(model="whisper-1")
  → { text: "<transcript>" }
  → frontend appends transcript to reply draft textarea
```

---

## Configuration

### `config/config.yaml`

| Key | Description |
|---|---|
| `gmail.query` | Gmail search filter for email fetching (e.g. `is:inbox`) |
| `gmail.max_results` | Max emails per fetch |
| `llm.provider` | `"openai"` or `"ollama"` |
| `llm.openai_model` | OpenAI model ID (e.g. `gpt-4o-mini`) |
| `llm.ollama_model` | Ollama model name (e.g. `llama3.2`) |
| `llm.ollama_base_url` | Ollama server base URL (default `http://localhost:11434`) |
| `llm.temperature` | Generation temperature |
| `llm.max_tokens` | Max tokens per LLM response |
| `rag.chunk_size` | Token size per email chunk (default 400) |
| `rag.chunk_overlap` | Overlap between adjacent chunks (default 80) |
| `cache.enabled` | Toggle EmailCache on/off |
| `keyring.service_name` | Keyring namespace for all secrets |
| `keyring.openai_key_name` | Keyring key name for OpenAI API key |
| `keyring.gmail_token_name` | Keyring key name for Gmail OAuth token |

### `config/agent_config.yaml`

All LLM prompt templates live here:

| Key | Used by |
|---|---|
| `email_analysis.system_prompt` / `human_prompt` | `LLMService.analyze()` |
| `thread_analysis.system_prompt` / `human_prompt` | `LLMService.analyze_thread()` |
| `sender_analysis.system_prompt` / `human_prompt` | `LLMService.analyze_sender()` |
| `chat_qa.system_prompt` / `human_prompt` | `LLMService.chat()` |
| `reply_generation.system_prompt` / `human_prompt` | `LLMService.generate_reply()` |

---

## Database Schema

### SQLite (relational)

| Table | Purpose |
|---|---|
| `senders` | Unique email senders with relation type, importance score, contact history |
| `threads` | Email threads with status (open/pending/closed) and optional LLM summary |
| `emails` | Individual email records; FK to sender + thread; `embedded_at` tracks vector sync |
| `agent_cache` | LLM analysis results keyed by `email_id`; stores full JSON response |
| `guidelines` | Uploaded guideline documents; `embedded_at` tracks vector sync |
| `tags` | User-defined tag library |

### ChromaDB (vector, 5 collections)

| Collection | Documents | Metadata |
|---|---|---|
| `email_chunks` | One doc per body chunk | `email_id`, `subject`, `sender_id`, `received_at`, `chunk_index` |
| `thread_summaries` | One doc per thread summary | `thread_id`, `subject`, `sender_id` |
| `sender_summaries` | One doc per sender summary | `sender_id`, `email` |
| `guideline_chunks` | One doc per guideline chunk | `guideline_id`, `filename`, `category`, `chunk_index` |
| `task_chunks` | One doc per action item | `email_id`, `task_text`, `deadline`, `subject`, `sender_id`, `source_type: "task"` |

Embedding model: `all-MiniLM-L6-v2` (SentenceTransformer, 384 dimensions).

---

## Secrets Management

No secrets are ever stored in config files or environment variables. All credentials live in
the OS keyring:

| Secret | Keyring key |
|---|---|
| OpenAI API key | `config.keyring.openai_key_name` |
| Gmail OAuth refresh token | `config.keyring.gmail_token_name` |

Run `python scripts/setup_secrets.py` once to store these interactively.

---

## Development Quick-Start

```bash
# Backend
cd backend
uv sync
python scripts/setup_secrets.py   # first time only
uvicorn app.main:app --reload      # http://127.0.0.1:8000

# Frontend
cd frontend
npm install
npm run proxy   # CORS proxy :8001 → :8000
npm run dev     # Vite :5173

# Or all at once (VS Code)
Ctrl+Shift+B    # runs "Start email Agent" task
```
