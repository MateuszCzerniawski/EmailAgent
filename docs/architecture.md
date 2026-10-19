# email Email Agent — Architecture

## Overview

The email Email Agent is a FastAPI server that:

1. Authenticates with Gmail via OAuth2.
2. Reads emails using the Gmail REST API.
3. Analyses each email with LangChain + OpenAI in a single LLM call.
4. Caches LLM responses by **email ID** in a local SQLite database.
5. Logs all activity asynchronously to both console (colored) and file.

All secrets (API keys, OAuth tokens) are stored in the OS keyring — never in files.

---

## Directory Structure

```
backend/
├── config/
│   ├── config.yaml         Application settings (categories, LLM, cache, …)
│   ├── logging_config.yaml Log levels, file path, ANSI color map
│   └── agent_config.yaml   LLM prompt templates (system + human)
├── app/
│   ├── main.py             FastAPI app factory + lifespan (logger + config init)
│   ├── config.py           AppConfig — parses config.yaml; path constants
│   ├── logger.py           AppLogger — async QueueHandler + ColorFormatter
│   ├── dependencies.py     FastAPI Depends factories
│   ├── models/
│   │   ├── mail.py         Mail — provider-agnostic email model
│   │   └── llm_response.py LLMResponse — structured LLM output
│   ├── postbox/
│   │   ├── base.py         Postbox ABC — provider contract
│   │   └── gmail.py        GmailPostbox — Google API implementation
│   ├── services/
│   │   └── llm_service.py  LLMService + EmailCache — LangChain chain + SQLite cache
│   └── routers/
│       ├── auth.py         GET /auth/* — OAuth2 flow
│       └── emails.py       GET /emails, GET /emails/{id}
├── scripts/
│   └── setup_secrets.py    First-time keyring setup CLI
└── logs/
    └── app.log             INFO+ log file (created automatically)
```

---

## Logging Architecture

```
logger.info(...)  ←  any module using get_logger(__name__)
    │
    ▼
QueueHandler  (non-blocking — log call returns immediately)
    │
    ▼  background thread (QueueListener)
    ├─ FileHandler   (INFO+)   → logs/app.log          plain text
    └─ StreamHandler (DEBUG+)  → stderr                ANSI colors

Colors:  DEBUG=cyan  INFO=green  WARNING=yellow  ERROR=red  CRITICAL=purple
```

Configured via `config/logging_config.yaml`. All modules import via:
```python
from app.logger import get_logger
logger = get_logger(__name__)
```

---

## Data Flow

### Listing emails (GET /emails)

```
HTTP request
    │
    ▼
emails.list_emails()
    │
    ├─► GmailPostbox.connect()          ← reads refresh_token from keyring
    │       └─► Google OAuth token refresh
    │
    ├─► GmailPostbox.fetch_emails()     ← Gmail API: messages.list + messages.get(metadata)
    │       └─► list[Mail]  (headers + snippet, no body)
    │
    └─► LLMService.analyze(mail)  ×N
            │
            ├─► EmailCache.get(mail.id)
            │       ├── HIT  → return LLMResponse(cached=True)   (< 5 ms)
            │       └── MISS → ChatOpenAI.invoke()               (300–1500 ms)
            │                   └─► EmailCache.set(mail.id, response)
            │
            └─► LLMResponse (summary, category, sentiment, …)

HTTP response: list[EmailSummaryResponse]
```

### Single email (GET /emails/{id})

Same flow as above but:
- Calls `GmailPostbox.get_email()` → full body populated.
- LLM receives full body text (truncated at 4 000 chars).
- Response includes `processing_time_ms`.

---

## Authentication Lifecycle

### First-time setup (CLI)

```
python scripts/setup_secrets.py
    │
    ├─► prompts for OpenAI API key (hidden input)
    │       └─► keyring.set_password(…, "openai_api_key", key)
    │
    └─► Gmail OAuth2 flow (InstalledAppFlow)
            ├─► reads credentials.json (downloaded from Google Cloud Console)
            ├─► stores client_id + client_secret in keyring
            ├─► opens browser → Google consent screen
            └─► keyring.set_password(…, "gmail_refresh_token", token)
```

### Per-request (server)

```
GmailPostbox.connect()
    ├─► keyring → refresh_token, client_id, client_secret
    ├─► google.oauth2.credentials.Credentials(refresh_token=…)
    ├─► creds.refresh(Request())   ← short-lived access_token
    └─► googleapiclient.build("gmail", "v1", credentials=creds)
```

### Web OAuth flow (/auth/gmail → /auth/callback)

```
GET /auth/gmail → RedirectResponse to Google consent page
GET /auth/callback?code=…&state=… → flow.fetch_token() → keyring.set_password(refresh_token)
```

---

## Email-ID Cache

### Why email-ID instead of prompt hash?

LangChain's built-in `SQLiteCache` keys on a hash of `(prompt_text, model_params)`.
This causes subtle misses whenever the prompt template, categories list, or any
model parameter changes — even if the email itself is unchanged.

The custom `EmailCache` (in `llm_service.py`) keys strictly on `email_id`:

| | LangChain SQLiteCache | EmailCache (this project) |
|-|----------------------|--------------------------|
| Key | hash(prompt + model params) | email_id |
| Cache hit requires | identical prompt text AND model params | same email_id |
| Hit accuracy | `cached` flag is a timing heuristic | `cached=True` is exact |
| Persistence | yes (SQLite) | yes (SQLite) |

### Cache table schema

```sql
CREATE TABLE email_cache (
    email_id     TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    cached_at    TEXT NOT NULL
)
```

### Cache miss/hit flow

```
EmailCache.get(email_id)
    ├── row found   → deserialise JSON → LLMResponse(cached=True)
    └── no row      → None  (LLMService calls OpenAI, then EmailCache.set)
```

---

## LLM Prompt Management

Prompts are stored in `config/agent_config.yaml` (not in source code):

```yaml
email_analysis:
  system_prompt: |
    You are an email analysis assistant ...
  human_prompt: |
    Subject: {subject}
    From: {sender}
    ...
```

Loaded by `LLMService.__init__()` via `load_agent_config()`. Changing prompts
requires only editing the YAML file and restarting the server — no code changes.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| GET | `/auth/status` | Is Gmail authenticated? |
| GET | `/auth/gmail` | Initiate OAuth2 flow |
| GET | `/auth/callback` | OAuth2 redirect handler |
| DELETE | `/auth/gmail` | Remove stored Gmail token |
| GET | `/emails` | List emails with LLM analysis |
| GET | `/emails/{email_id}` | Single email with full body analysis |

Interactive docs: `http://127.0.0.1:8000/docs`

---

## Configuration Reference

### config/config.yaml

| Key | Default | Description |
|-----|---------|-------------|
| `email_categories` | 10 defaults | LLM must pick one |
| `gmail.max_results` | 50 | Emails per request |
| `gmail.query` | `is:unread` | Gmail filter |
| `llm.model` | `gpt-4o-mini` | OpenAI model |
| `llm.temperature` | 0.0 | Keep at 0.0 |
| `llm.max_tokens` | 512 | Response length cap |
| `cache.enabled` | true | Enable email-ID cache |
| `cache.db_path` | `.cache/llm_cache.db` | SQLite file path |
| `server.host` | `127.0.0.1` | Bind address |
| `server.port` | 8000 | Bind port |
| `keyring.service_name` | `email-email-agent` | Keyring namespace |

### config/logging_config.yaml

| Key | Default | Description |
|-----|---------|-------------|
| `log_file` | `logs/app.log` | File path (relative to backend/) |
| `console_level` | `DEBUG` | Minimum level for console |
| `file_level` | `INFO` | Minimum level for log file |
| `format` | `%(asctime)s …` | Log record format string |
| `colors.DEBUG` | cyan | ANSI color name |
| `colors.INFO` | green | ANSI color name |
| `colors.WARNING` | yellow | ANSI color name |
| `colors.ERROR` | red | ANSI color name |
| `colors.CRITICAL` | purple | ANSI color name |

### config/agent_config.yaml

Contains `email_analysis.system_prompt` and `email_analysis.human_prompt`.
Edit freely — changes take effect on server restart.

---

## Postbox Abstraction

```
Postbox  (app/postbox/base.py)
└── GmailPostbox  (app/postbox/gmail.py)
        └── [future] OutlookPostbox, IMAPPostbox, …
```

| Method | Description |
|--------|-------------|
| `connect()` | Authenticate and build the provider service object |
| `fetch_emails(max_results, query)` | List emails (headers + snippet only) |
| `get_email(email_id)` | Fetch single email with full body |
| `disconnect()` | Release the service object |
