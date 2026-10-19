"""
FastAPI dependency factories.

All injectable objects are created here so route handlers stay thin and
testable. Use `Depends(...)` in route signatures to inject these.

Example usage in a route:
    from app.dependencies import get_postbox, get_llm_service

    @router.get("/emails")
    def list_emails(
        postbox: GmailPostbox = Depends(get_postbox),
        llm: LLMService = Depends(get_llm_service),
    ):
        ...
"""

from __future__ import annotations

from typing import Generator

from fastapi import Depends, HTTPException, Request

from app.config import AppConfig
from app.postbox.gmail import GmailPostbox
from app.services.llm_service import LLMService


def get_config(request: Request) -> AppConfig:
    """
    Return the AppConfig stored on app.state during lifespan startup.

    This is a zero-cost dependency — the config is parsed once at startup
    and re-used for every request.
    """
    return request.app.state.config


def get_postbox(
    config: AppConfig = Depends(get_config),
) -> Generator[GmailPostbox, None, None]:
    """
    Yield a connected GmailPostbox and guarantee disconnect after the request.

    Using a generator dependency (yield) ensures disconnect() is called even
    if the route handler raises an exception.
    """
    postbox = GmailPostbox(config)
    postbox.connect()
    try:
        yield postbox
    finally:
        postbox.disconnect()


def get_llm_service(
    config: AppConfig = Depends(get_config),
) -> LLMService:
    """
    Return an LLMService instance.

    A new instance is created per request. The underlying ChatOpenAI object
    is lightweight — the actual HTTP connection is made only during chain.invoke().
    The SQLiteCache (set globally in main.py lifespan) is shared across all
    instances, so there is no per-request cache initialisation overhead.
    """
    return LLMService(config)


def get_db_manager(request: Request):
    """
    Return the DatabaseManager stored on app.state during lifespan startup.

    Raises HTTP 503 if the database layer was not initialized (e.g. DATABASE_URL
    is not configured or PostgreSQL was unreachable at startup).
    """
    manager = getattr(request.app.state, "db_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database layer not initialized. "
                "Ensure DATABASE_URL is set and PostgreSQL is reachable, "
                "then restart the server."
            ),
        )
    return manager
