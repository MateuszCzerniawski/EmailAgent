# email Email Agent

Project done during summer semester 2026.
The goal was to integrate LLM into a fully functional app and use AI agent in development.
Other contributors:
- [TheBitson124](https://github.com/TheBitson124)
- [Sophirra](https://github.com/Sophirra)
- [Trinket9](https://github.com/Trinket9)

---

A FastAPI server that connects to Gmail, reads your emails, and uses LangChain + OpenAI to **summarise and categorise** each one. LLM responses are cached by **email ID** in a local SQLite database — repeated requests for the same email return instantly. All activity is logged asynchronously to a colored console and a log file.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.12+ | [python.org](https://www.python.org/downloads/) |
| uv | `pip install uv` or see [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| OpenAI account | API key from [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Google Cloud project | Free tier is sufficient |

### Google Cloud setup (one-time)

1. Go to [console.cloud.google.com](https://console.cloud.google.com/).
2. Create a new project (or use an existing one).
3. Enable the **Gmail API** (APIs & Services → Library → Gmail API → Enable).
4. Create **OAuth 2.0 credentials** (APIs & Services → Credentials → Create Credentials → OAuth client ID).
   - Application type: **Desktop app**
   - Give it any name.
5. Click **Download JSON** and save the file as `credentials.json` in the `backend/` directory.

> `credentials.json` is gitignored and never committed.

---

## Installation

```bash
cd backend
uv sync
```

This installs all dependencies (FastAPI, LangChain, Google API client, keyring, …) into the local `.venv`.

---

## First-Time Setup — Storing Keys with Keyring

All secrets are stored in the **OS keyring** (Windows Credential Manager / macOS Keychain / Linux Secret Service). No `.env` files are used.

Run the setup script once per machine/user:

```bash
cd backend
python -m uvicorn --version  # verify the venv is active
python scripts/setup_secrets.py
```

Or, if `uv` is on your PATH:

```bash
cd backend
uv run setup-secrets
```

The script will:

1. **Ask for your OpenAI API key** — input is hidden (like a password prompt).
   Stored as: `email-email-agent / openai_api_key`

2. **Open a browser for Gmail consent** — log in with the Gmail account you want to read.
   The script reads `credentials.json` automatically.
   Stored as:
   - `email-email-agent / gmail_client_id`
   - `email-email-agent / gmail_client_secret`
   - `email-email-agent / gmail_refresh_token`

> You can skip either step by pressing Enter. Re-running the script safely overwrites previously stored values.

### Verifying stored secrets

**Windows:** Open *Credential Manager* → *Windows Credentials* → look for entries starting with `email-email-agent`.

**macOS:** Open *Keychain Access* → search for `email-email-agent`.

**Linux:** Use `secret-tool lookup service email-email-agent` or open *Passwords and Keys* (GNOME Seahorse).

### Rotating keys

To update a key, simply re-run `python scripts/setup_secrets.py` — it will overwrite the existing value.

---

## Starting the Server

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

The server is now running at **http://127.0.0.1:8000**.

| URL | Description |
|-----|-------------|
| http://127.0.0.1:8000/docs | Interactive Swagger UI |
| http://127.0.0.1:8000/redoc | ReDoc documentation |
| http://127.0.0.1:8000/openapi.json | Raw OpenAPI schema |

---

## API Quick-Start

### 1. Check authentication status

```
GET http://127.0.0.1:8000/auth/status
```

Returns `{"authenticated": true}` if the Gmail token is stored, `false` otherwise.

### 2. Connect Gmail (if not already authenticated)

```
GET http://127.0.0.1:8000/auth/gmail
```

Opens the Google OAuth consent screen in your browser. After granting access you are redirected to `/emails`.

Alternatively, just run `python scripts/setup_secrets.py` — it handles the same flow in the terminal.

### 3. List and analyse emails

```
GET http://127.0.0.1:8000/emails
GET http://127.0.0.1:8000/emails?query=is:unread&max_results=10
```

Returns a list of emails, each with:
- Full headers (subject, sender, date, labels, …)
- LLM-generated summary, category, sentiment, and action detection.
- `cached: true/false` — whether the response was served from the email-ID SQLite cache.

### 4. Get a single email with full body analysis

```
GET http://127.0.0.1:8000/emails/{email_id}
```

Fetches the full email body and runs a detailed LLM analysis. The result is cached by `email_id` — the second call for the same ID returns instantly.

---

## Configuration

All configuration lives in `backend/config/`. Edit these files to customise behaviour without touching code:

### `config/config.yaml` — main settings

```yaml
email_categories: [work, finance, newsletter, …]  # LLM picks one per email
gmail:
  max_results: 50
  query: "is:unread"
llm:
  model: "gpt-4o-mini"   # change to "gpt-4o" for higher quality
  temperature: 0.0       # keep at 0.0
  max_tokens: 512
cache:
  enabled: true
  db_path: ".cache/llm_cache.db"  # SQLite, keyed by email ID
```

### `config/logging_config.yaml` — log levels and colors

```yaml
log_file: "logs/app.log"
console_level: "DEBUG"   # DEBUG/INFO/WARNING/ERROR
file_level: "INFO"
colors:
  DEBUG: cyan
  INFO: green
  WARNING: yellow
  ERROR: red
  CRITICAL: purple
```

### `config/agent_config.yaml` — LLM prompts

Edit `email_analysis.system_prompt` and `email_analysis.human_prompt` to change how the LLM analyses emails. Changes take effect on server restart.

---

## Troubleshooting

### `PostboxAuthError: Gmail refresh token not found in keyring`

Run the setup script: `python scripts/setup_secrets.py`

### `Failed to refresh Gmail access token`

The refresh token may have been revoked. Revoke all access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions), then re-run `setup-secrets`.

### `OpenAI API key not found in keyring`

Run `python scripts/setup_secrets.py` and enter your OpenAI key when prompted.

### `credentials.json not found`

Download your OAuth2 Desktop App credentials from [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials) and save the file as `backend/credentials.json`.

### `No refresh token received` during OAuth

Google only returns a refresh token on the first consent. Revoke access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions) and re-run the setup.

### Cache seems stale

The cache is keyed by `email_id`. If you want to re-analyse an email (e.g. after changing the prompt in `config/agent_config.yaml`), delete the row from `backend/.cache/llm_cache.db` or delete the file entirely to clear the whole cache.

---

## Technical Documentation

See [docs/architecture.md](docs/architecture.md) for:
- Full data flow diagrams
- Authentication lifecycle
- Caching strategy details
- Postbox abstraction and how to add a new provider
- Complete API reference and configuration table
