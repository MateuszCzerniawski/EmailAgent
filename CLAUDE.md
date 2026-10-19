# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Development Commands

### Backend
```bash
cd backend
uv sync                          # Install / sync dependencies
python scripts/setup_secrets.py  # One-time: store OpenAI key + Gmail OAuth in OS keyring
uvicorn app.main:app --reload    # Start server on http://127.0.0.1:8000
pytest                           # Run tests
```

### Frontend
```bash
cd frontend
npm install      # Install dependencies
npm run dev      # Vite dev server on http://localhost:5173
npm run proxy    # CORS proxy on http://127.0.0.1:8001 → forwards to :8000
npm run build    # Production build → dist/
```

### Start everything at once
- **VS Code**: `Ctrl+Shift+B` runs the "Start email Agent" task (backend + proxy + frontend in parallel terminals).
- **PowerShell**: `./start.ps1` from the repo root (opens three terminal windows).

---

## Architecture

### Stack overview
Full-stack email intelligence app. FastAPI backend + React/Vite frontend. Gmail is the only email provider. All AI work goes through LangChain + OpenAI. Two databases: SQLite (relational) and ChromaDB (vector/RAG).

### Backend layers (`backend/`)

**Entry point:** `app/main.py` — `create_app()` builds the FastAPI app. The `lifespan` context manager runs in this order on startup: AppLogger → AppConfig (YAML) → DatabaseManager (SQLite + Chroma) → background email-sync task.

**Configuration:** `config/config.yaml` controls gmail query/limit, LLM model/temperature, cache, RAG chunk sizes. `config/agent_config.yaml` holds all LLM prompt templates (email analysis, thread analysis, reply generation). Never put secrets in either file — all API keys and OAuth tokens live in the OS keyring (Windows Credential Manager / macOS Keychain).

**Dependency injection:** `app/dependencies.py` provides FastAPI `Depends` factories for every service: `get_config`, `get_postbox`, `get_llm_service`, `get_db_manager`. Routers import these; they never construct services directly.

**Request → response flow:**
1. Router handler calls `get_postbox()` → `GmailPostbox` (OAuth auto-refreshes token from keyring).
2. `GmailPostbox.fetch_emails()` / `.get_email()` returns `list[Mail]` — a provider-agnostic model in `app/models/mail.py`. `Mail` is frozen (`frozen=True`); use `mail.model_copy(update={...})` to modify.
3. `LLMService.analyze(mail)` checks `EmailCache` first (SQLite-backed, keyed by `mail.id`). Cache miss → `_chain.invoke(...)` via LangChain → `LLMResponse` → cache stored.
4. For thread/sender summaries: `LLMService.analyze_thread()` / `.analyze_sender()` — both reuse `_thread_chain`; their caches use `"thread:<id>"` / `"sender:<id>"` keys in the same SQLite cache table.
5. Results are persisted to the relational DB via `DatabaseManager.relational` (a `RelationalDB` instance).

**Database layer:**
- `database/relational/base.py` — abstract `RelationalDB` interface. All callers use this ABC; never touch ORM instances outside the `relational/` package.
- `database/relational/sqlite_db.py` — concrete implementation. `connect()` runs `Base.metadata.create_all()` (idempotent) **plus** explicit `ALTER TABLE` migration statements for columns added after the initial schema. This is the migration strategy for dev — no Alembic.
- `database/models/orm.py` — SQLAlchemy 2.0 mapped classes, dialect-agnostic (TEXT for UUIDs, JSON for arrays/enums).
- `database/models/domain.py` — Pydantic domain models (DTOs). ORM instances never leave the `relational/` package; converters (`_*_to_domain`) always return domain models.

**Route ordering constraint:** In `app/routers/emails.py`, `GET /{email_id}` is a catch-all and **must be the last route registered**. All specific sub-paths (`/raw/{id}`, `/analyse/{id}`, `/senders`, `/threads`, `/{id}/metadata`, `/{id}/suggested-reply`) must appear before it.

### Frontend layers (`frontend/src/`)

**Routing:** `router.jsx` — three routes: `/` → `InboxPage`, `/emails/:emailId` → `EmailDetailPage`, `/settings` → `SettingsPage`.

**API client:** `api/client.js` — single `request()` fetch wrapper targeting `VITE_API_BASE_URL` (defaults to `http://127.0.0.1:8001`, the CORS proxy). All API functions are exported from this file with JSDoc typedefs for every response shape.

**Data normalization:** `ui/tools/normalizeEmail.js` — `normalizeEmailListItem` and `normalizeEmailDetail` transform raw backend responses into the UI model. The backend can return `{ mail: ... }` or `{ email: ... }` shapes; these functions smooth over that. `importanceScore` from the agent cache is `Number(analysisRaw.importance_score)` — guard against `Number(null) === 0` by checking `!= null` before converting.

**Local overrides:** `ui/tools/storage.js` — `getEmailOverrides` / `setEmailOverrides` persist per-email draft edits to `localStorage`. These are merged over the normalized server data in `EmailDetailPage` before setting state.

**Component primitives:** `Badge`, `Button`, `Card`/`CardHeader`, `Alert`, `InfoBox`, `Input`/`Select`/`Textarea`/`FormField`, `Skeleton` — in `ui/components/`. `CardHeader` accepts `title` as any React node (used for the segmented toggle switch in email detail).

**Sender sidebar flow:** `SenderSidebar` fetches senders eagerly on mount, then lazily loads threads per sender on first expand via `listThreads({ senderId })`. Threads are cached in `threadMap` state to avoid re-fetching.

### Key invariants
- **`Mail` is frozen.** Never mutate; always `mail.model_copy(update={...})`.
- **`create_all` does not migrate.** Adding a column to an existing ORM model also requires an `ALTER TABLE ... ADD COLUMN` in `SQLiteDB.connect()` wrapped in `try/except` (SQLite raises on duplicate column).
- **`suggested_tags` is a JSON column.** SQLAlchemy handles Python list ↔ JSON automatically; pass a plain Python list to `.values(suggested_tags=...)`.
- **All secrets in OS keyring.** `keyring.get_password(namespace, key)` where `namespace` comes from `config.yaml → keyring_namespace`. Never `.env` for secrets.
- **Proxy in dev.** The frontend talks to `:8001` (proxy), not `:8000` (backend) directly, to avoid CORS. `VITE_API_BASE_URL` controls this.
