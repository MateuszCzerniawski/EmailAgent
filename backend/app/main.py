"""
FastAPI application factory.

Startup / shutdown lifecycle (lifespan)
----------------------------------------
1. Initialise AppLogger (async queue-based logging with colors).
2. Load AppConfig from config/config.yaml.
3. Store config on app.state so Depends(get_config) can retrieve it.
4. Initialise DatabaseManager (PostgreSQL + ChromaDB) — optional; app starts
   without a DB if DATABASE_URL is not configured (DB endpoints return 503).
5. Register all routers.

Running the server
------------------
From the backend/ directory:

    python -m uvicorn app.main:app --reload

Interactive API docs:  http://127.0.0.1:8000/docs
OpenAPI JSON:          http://127.0.0.1:8000/openapi.json
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from email.utils import parseaddr

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import LOGGING_CONFIG_PATH, load_config, load_database_config
from app.logger import AppLogger, get_logger
from app.routers import auth, emails, stt
from app.routers import database as database_router
from app.routers import config as config_router
from app.routers import chat as chat_router


async def _startup_email_sync(db_manager, config) -> None:
    """
    Background task: fetch emails from Gmail on startup and ingest any that are
    not yet in the RDB.  Runs concurrently with request handling — failures are
    logged but never propagate to callers.
    """
    from app.postbox.gmail import GmailPostbox
    from database.exceptions import DuplicateEmailError

    try:
        postbox = GmailPostbox(config)
        postbox.connect()
        mails = postbox.fetch_emails(
            max_results=config.gmail.max_results,
            query=config.gmail.query,
        )
        ingested = 0
        for mail in mails:
            display_name, sender_email = parseaddr(mail.sender)
            try:
                await db_manager.ingest_email({
                    "message_id": mail.id,
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
                ingested += 1
            except DuplicateEmailError:
                pass
            except Exception as exc:
                logger_startup = get_logger(__name__)
                logger_startup.warning("Startup sync: ingest failed for %s: %s", mail.id, exc)
        postbox.disconnect()
        get_logger(__name__).info("Startup sync complete: %d new emails ingested", ingested)
    except Exception as exc:
        get_logger(__name__).warning("Startup sync failed (non-fatal): %s", exc)

# ---------------------------------------------------------------------------
# Windows: asyncpg requires SelectorEventLoop (not the default ProactorEventLoop)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Everything before `yield` runs at startup; everything after runs at shutdown.
    """
    # --- STARTUP ---

    # 1. Configure logging first so all subsequent startup messages are captured.
    app_logger = AppLogger(LOGGING_CONFIG_PATH)
    app_logger.setup()
    app.state.app_logger = app_logger

    logger = get_logger(__name__)
    logger.info("Logger initialised from %s", LOGGING_CONFIG_PATH)

    # 2. Parse config/config.yaml.
    config = load_config()
    app.state.config = config
    logger.info("Configuration loaded (LLM provider: %s, model: %s)", config.llm.provider, config.llm.active_model)
    logger.info(
        "Email-ID cache %s at %s",
        "enabled" if config.cache.enabled else "disabled",
        config.cache.db_path,
    )

    # 3. Initialise the OAuth state store for the auth router.
    app.state.oauth_states = {}

    # 4. Initialise the database layer (PostgreSQL + ChromaDB).
    #    Wrapped in try/except so the app starts even without a running DB —
    #    endpoints that require the DB will return 503 Service Unavailable.
    app.state.db_manager = None
    try:
        from database.manager.manager import DatabaseManager
        from database.relational.sqlite_db import SQLiteDB
        from database.vector.chroma import ChromaVectorDB

        db_config = load_database_config()
        relational = SQLiteDB(db_config.database_url)
        vector = ChromaVectorDB(
            chroma_path=db_config.chroma_path,
            embedding_model=db_config.embedding_model,
            chunk_size=db_config.chunk_size,
            chunk_overlap=db_config.chunk_overlap,
        )
        db_manager = DatabaseManager(relational, vector)
        await db_manager.startup()
        app.state.db_manager = db_manager
        logger.info(
            "Database layer initialized (RelationalDB + ChromaDB, model=%s)",
            db_config.embedding_model,
        )
        asyncio.create_task(_startup_email_sync(db_manager, config))
    except Exception as exc:
        logger.warning(
            "Database layer NOT initialized — DB endpoints will return 503. "
            "Reason: %s",
            exc,
        )

    logger.info(
        "email Email Agent started — docs at http://%s:%d/docs",
        config.server.host,
        config.server.port,
    )

    yield

    # --- SHUTDOWN ---
    logger.info("email Email Agent shutting down")
    if app.state.db_manager is not None:
        await app.state.db_manager.shutdown()
    app_logger.shutdown()  # flush remaining queued log records


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="email Email Agent",
        description=(
            "FastAPI server that connects to Gmail, reads emails, "
            "and analyses them with LangChain + OpenAI.\n\n"
            "**First-time setup:** run `python scripts/setup_secrets.py` "
            "before starting the server."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow localhost origins for local development / browser testing.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://localhost:5173",
            "http://127.0.0.1",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers.
    app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
    app.include_router(emails.router, prefix="/emails", tags=["Emails"])
    app.include_router(database_router.router, prefix="/database", tags=["Database"])
    app.include_router(stt.router, prefix="/stt", tags=["STT"])
    app.include_router(config_router.router, prefix="/config", tags=["Config"])
    app.include_router(chat_router.router, prefix="/chat", tags=["Chat"])

    @app.get("/", tags=["Health"], summary="Health check")
    def health():
        """Return server status. Useful for load balancers and smoke tests."""
        return {"status": "ok", "service": "email-email-agent"}

    return app


# Module-level app instance — used by uvicorn: `uvicorn app.main:app`
app = create_app()
